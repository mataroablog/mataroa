"""Protocol, authorization, and privacy tests without real user credentials."""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from main.mcp.backend import MataroaError
from main.mcp.server import (
    DELETE,
    DRAFTS,
    PUBLISH,
    READ,
    TOOLS,
    ToolService,
    posts_resource,
)

POSTS = [
    {
        "slug": "draft",
        "title": "First draft",
        "body": "Hello",
        "published_at": None,
        "url": "https://alice.mataroa.blog/blog/draft/",
        "content_sha256": "a" * 64,
    },
    {
        "slug": "live",
        "title": "Published",
        "body": "World",
        "published_at": "2020-01-01",
        "url": "https://alice.mataroa.blog/blog/live/",
        "content_sha256": "b" * 64,
    },
    {
        "slug": "future",
        "title": "Scheduled",
        "body": None,
        "published_at": (date.today() + timedelta(days=5)).isoformat(),
        "url": "https://alice.mataroa.blog/blog/future/",
        "content_sha256": "c" * 64,
    },
]


def token(subject=1, scopes=None):
    return SimpleNamespace(
        user_id=subject, scopes=scopes if scopes is not None else [READ]
    )


def make_tool_service(principal=None):
    service = Mock()
    service.list_posts.return_value = deepcopy(POSTS)
    service.get_post.return_value = deepcopy(POSTS[0])
    seen = []

    def factory(user_id):
        seen.append(user_id)
        return service

    server = ToolService(
        principal.user_id if principal else None,
        principal.scopes if principal else [],
        backend_factory=factory,
    )
    return server, service, seen


class MCPServerTests(SimpleTestCase):
    def test_reader_html_formats_markdown_without_active_content_or_app_attributes(
        self,
    ):
        server, backend, _ = make_tool_service(token())
        body = (
            "## Heading\n\nA **bold** paragraph.\n\n- One\n- Two\n\n"
            '<script>alert(1)</script><img src=x onerror="alert(1)">'
            '<iframe src="https://example.com"></iframe>'
            '<p id="open-post" class="reader" style="position:fixed">Text</p>'
            '<a href="javascript:alert(1)">Unsafe</a>'
        )
        backend.get_post.return_value = {**POSTS[0], "body": body}
        post = server.get_post("draft")["post"]
        self.assertEqual(post["body"], body)
        self.assertEqual(post["content_sha256"], POSTS[0]["content_sha256"])
        html = post["body_html"]
        self.assertIn("<h2>Heading</h2>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<li>One</li>", html)
        for forbidden in (
            "<script",
            "<img",
            "<iframe",
            "onerror=",
            'id="',
            'class="',
            'style="',
            'href="javascript:',
        ):
            self.assertNotIn(forbidden, html)

    def test_metadata_read_write_scopes_and_native_entrypoint(self):
        server, _, _ = make_tool_service(token())
        tools = {t["name"]: t for t in TOOLS}
        self.assertIs(tools["list_posts"]["annotations"]["readOnlyHint"], True)
        self.assertIs(tools["create_draft"]["annotations"]["readOnlyHint"], False)
        self.assertIs(tools["publish_post"]["annotations"]["destructiveHint"], True)
        self.assertIs(tools["publish_post"]["annotations"]["openWorldHint"], True)
        self.assertIn(
            PUBLISH, tools["publish_post"]["_meta"]["securitySchemes"][0]["scopes"]
        )
        self.assertEqual(
            tools["open_posts"]["_meta"]["openai/ui"]["entrypoints"],
            [{"type": "global"}, {"type": "thread"}],
        )
        self.assertEqual(
            tools["search_mentions"]["_meta"]["openai/extensions"],
            {"mentions/search": {}},
        )
        self.assertIs(tools["delete_post"]["annotations"]["destructiveHint"], True)
        self.assertIs(tools["delete_post"]["annotations"]["readOnlyHint"], False)
        self.assertEqual(
            tools["delete_post"]["_meta"]["securitySchemes"][0]["scopes"],
            [READ, DELETE],
        )
        self.assertFalse(any("approve" in name for name in tools))
        self.assertNotIn("api_key", str([t["inputSchema"] for t in tools.values()]))
        self.assertNotIn("user_id", str([t["inputSchema"] for t in tools.values()]))

    def test_missing_or_invalid_identity_never_reaches_backend(self):
        for principal in [
            None,
            token(subject=None),
            token(subject="bad"),
            token(subject="0"),
            token(scopes=[]),
        ]:
            with self.subTest(principal=principal):
                server, service, seen = make_tool_service(principal)
                with self.assertRaises(MataroaError):
                    server.call_tool("list_posts", {})
                self.assertFalse(seen)
                service.list_posts.assert_not_called()

    def test_scope_enforced_before_write(self):
        server, service, _ = make_tool_service(token())
        for name, args in [
            ("create_draft", {"title": "Test"}),
            (
                "update_draft",
                {"slug": "draft", "expected_content_sha256": "a" * 64, "body": "Edit"},
            ),
            (
                "publish_post",
                {
                    "slug": "draft",
                    "expected_content_sha256": "a" * 64,
                    "published_at": "2026-10-08",
                },
            ),
        ]:
            with self.assertRaisesRegex(MataroaError, "permission"):
                server.call_tool(name, args)
        service.create_draft.assert_not_called()
        service.update_draft.assert_not_called()
        service.publish_post.assert_not_called()

    def test_draft_permission_does_not_grant_publish(self):
        server, _, _ = make_tool_service(token(scopes=[READ, DRAFTS]))
        with self.assertRaisesRegex(MataroaError, "permission"):
            server.call_tool(
                "publish_post",
                {
                    "slug": "draft",
                    "published_at": "2026-10-08",
                    "expected_content_sha256": "a" * 64,
                },
            )

    def test_search_status_pagination_and_null_bodies(self):
        server, _, seen = make_tool_service(token(subject=42))
        result = server.call_tool("list_posts", {"query": "hello", "limit": 1})
        self.assertEqual(result["structuredContent"]["total"], 1)
        self.assertEqual(result["structuredContent"]["posts"][0]["slug"], "draft")
        self.assertNotIn("body", result["structuredContent"]["posts"][0])
        result = server.call_tool("list_posts", {"status": "published"})
        self.assertEqual(
            [p["slug"] for p in result["structuredContent"]["posts"]], ["live"]
        )
        result = server.call_tool("list_posts", {"status": "scheduled"})
        self.assertEqual(result["structuredContent"]["posts"][0]["slug"], "future")
        self.assertEqual(seen, [42, 42, 42])

    def test_list_input_bounds(self):
        for args in [
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"query": "x" * 301},
            {"status": "private"},
        ]:
            with self.subTest(args=args):
                server, service, _ = make_tool_service(token())
                with self.assertRaises(MataroaError):
                    server.call_tool("list_posts", args)
                service.list_posts.assert_not_called()

    def test_comments_never_expose_email_even_if_backend_does(self):
        server, service, _ = make_tool_service(token())
        service.list_comments.return_value = [
            {"id": 1, "body": "hi", "email": "private@example.com"}
        ]
        service.get_comment.return_value = service.list_comments.return_value[0]
        result = server.call_tool("list_comments", {})
        self.assertNotIn("email", result["structuredContent"]["comments"][0])
        result = server.call_tool("get_comment", {"comment_id": 1})
        self.assertNotIn("email", result["structuredContent"]["comment"])

    def test_publish_preserves_exact_reviewed_fingerprint_and_date(self):
        server, service, _ = make_tool_service(token(scopes=[READ, PUBLISH]))
        service.publish_post.return_value = {
            "ok": True,
            "slug": "draft",
            "url": POSTS[0]["url"],
        }
        args = {
            "slug": "draft",
            "published_at": "2026-10-09",
            "expected_content_sha256": "d" * 64,
        }
        result = server.call_tool("publish_post", args)
        service.publish_post.assert_called_once_with(
            "draft", published_at="2026-10-09", expected_content_sha256="d" * 64
        )
        self.assertIs(result["structuredContent"]["ok"], True)
        service.get_post.assert_not_called()

    def test_deletion_requires_its_own_scope_and_preserves_reviewed_fingerprint(self):
        args = {"slug": "draft", "expected_content_sha256": "d" * 64}
        for scopes in ([READ], [READ, DRAFTS], [READ, PUBLISH], [DELETE]):
            with self.subTest(scopes=scopes):
                server, backend, seen = make_tool_service(token(scopes=scopes))
                with self.assertRaisesRegex(MataroaError, "permission"):
                    server.call_tool("delete_post", args)
                self.assertFalse(seen)
                backend.delete_post.assert_not_called()
        server, backend, _ = make_tool_service(token(scopes=[READ, DELETE]))
        backend.delete_post.return_value = {"ok": True, "slug": "draft"}
        result = server.call_tool("delete_post", args)
        self.assertEqual(result["structuredContent"], {"ok": True, "slug": "draft"})
        backend.delete_post.assert_called_once_with(
            "draft", expected_content_sha256="d" * 64
        )
        backend.get_post.assert_not_called()

    def test_expected_errors_are_safe_tool_errors(self):
        server, service, _ = make_tool_service(token())
        service.get_post.side_effect = MataroaError("not_found", "Post not found.")
        with self.assertRaisesRegex(MataroaError, "Post not found"):
            server.call_tool("get_post", {"slug": "missing"})


class PostsAssetTests(SimpleTestCase):
    def test_collected_assets_and_csp_match_the_resource(self):
        """A sandbox can load the exact deployed files without a JS build or CORS."""
        import re
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from urllib.parse import urlsplit

        from django.core.management import call_command
        from django.test import override_settings

        for static_url, origin in [
            ("/static/", "https://mataroa.blog"),
            ("https://static.example/assets/", "https://static.example"),
        ]:
            with (
                self.subTest(static_url=static_url),
                TemporaryDirectory() as directory,
                override_settings(
                    DEBUG=False,
                    STATIC_ROOT=directory,
                    STATIC_URL=static_url,
                    MATAROA_MCP_ISSUER_URL="https://mataroa.blog",
                    STORAGES={
                        "staticfiles": {
                            "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
                        }
                    },
                ),
            ):
                call_command("collectstatic", interactive=False, verbosity=0)
                html, origins = posts_resource()
                self.assertEqual(origins, [origin])
                urls = re.findall(r'(?:src|href)="([^"]+)"', html)
                self.assertEqual(len(urls), 5)
                for url in urls:
                    self.assertTrue(url.startswith(origin + "/"))
                    self.assertRegex(url, r"/mcp/[a-z]+\.[a-f0-9]{12}\.(js|css)$")
                    filename = urlsplit(url).path.rsplit("/", 1)[1]
                    self.assertTrue((Path(directory) / "mcp" / filename).is_file())
                self.assertEqual(html.count("<script defer src="), 4)
                self.assertNotIn('<script type="module">', html)
