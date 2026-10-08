"""Protocol, authorization, and privacy tests without real user credentials."""

from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
from django.test import SimpleTestCase
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from main.mcp.backend import MataroaError
from main.mcp.server import DRAFTS, PUBLISH, READ, create_server

RESOURCE = "https://mataroa.blog/mcp"
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


def token(subject="1", scopes=None, resource=RESOURCE):
    return AccessToken(
        token="test-only-token",
        client_id="test-chatgpt",
        subject=subject,
        resource=resource,
        scopes=scopes if scopes is not None else [READ],
        expires_at=4070908800,
    )


def make_server(principal=None):
    service = AsyncMock()
    service.list_posts.return_value = deepcopy(POSTS)
    service.get_post.return_value = deepcopy(POSTS[0])
    seen = []

    def factory(user_id):
        seen.append(user_id)
        return service

    server = create_server(
        backend_factory=factory,
        principal_provider=lambda: principal,
        ui_html="<main>Test library</main>",
    )
    return server, service, seen


class MCPServerTests(SimpleTestCase):
    async def test_metadata_read_write_scopes_and_native_entrypoint(self):
        server, _, _ = make_server(token())
        tools = {t.name: t for t in await server.list_tools()}
        self.assertIs(tools["list_posts"].annotations.read_only_hint, True)
        self.assertIs(tools["create_draft"].annotations.read_only_hint, False)
        self.assertIs(tools["publish_post"].annotations.destructive_hint, True)
        self.assertIs(tools["publish_post"].annotations.open_world_hint, True)
        self.assertIn(
            PUBLISH, tools["publish_post"].meta["securitySchemes"][0]["scopes"]
        )
        self.assertEqual(
            tools["open_library"].meta["openai/ui"]["entrypoints"],
            [{"type": "global"}, {"type": "thread"}],
        )
        self.assertEqual(
            tools["search_mentions"].meta["openai/extensions"], {"mentions/search": {}}
        )
        self.assertFalse(any("delete" in name or "approve" in name for name in tools))
        self.assertNotIn("api_key", str([t.input_schema for t in tools.values()]))
        self.assertNotIn("user_id", str([t.input_schema for t in tools.values()]))

    async def test_missing_or_invalid_identity_never_reaches_backend(self):
        for principal in [
            None,
            token(subject=None),
            token(subject="bad"),
            token(subject="0"),
            token(scopes=[]),
        ]:
            with self.subTest(principal=principal):
                server, service, seen = make_server(principal)
                with self.assertRaises(ToolError):
                    await server.call_tool("list_posts", {})
                self.assertFalse(seen)
                service.list_posts.assert_not_awaited()

    async def test_scope_enforced_before_write(self):
        server, service, _ = make_server(token())
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
            with self.assertRaisesRegex(ToolError, "permission"):
                await server.call_tool(name, args)
        service.create_draft.assert_not_awaited()
        service.update_draft.assert_not_awaited()
        service.publish_post.assert_not_awaited()

    async def test_draft_permission_does_not_grant_publish(self):
        server, _, _ = make_server(token(scopes=[READ, DRAFTS]))
        with self.assertRaisesRegex(ToolError, "permission"):
            await server.call_tool(
                "publish_post",
                {
                    "slug": "draft",
                    "published_at": "2026-10-08",
                    "expected_content_sha256": "a" * 64,
                },
            )

    async def test_search_status_pagination_and_null_bodies(self):
        server, _, seen = make_server(token(subject="42"))
        result = await server.call_tool("list_posts", {"query": "hello", "limit": 1})
        self.assertEqual(result.structured_content["total"], 1)
        self.assertEqual(result.structured_content["posts"][0]["slug"], "draft")
        self.assertNotIn("body", result.structured_content["posts"][0])
        result = await server.call_tool("list_posts", {"status": "published"})
        self.assertEqual(
            [p["slug"] for p in result.structured_content["posts"]], ["live"]
        )
        result = await server.call_tool("list_posts", {"status": "scheduled"})
        self.assertEqual(result.structured_content["posts"][0]["slug"], "future")
        self.assertEqual(seen, [42, 42, 42])

    async def test_list_input_bounds(self):
        for args in [
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"query": "x" * 301},
            {"status": "private"},
        ]:
            with self.subTest(args=args):
                server, service, _ = make_server(token())
                with self.assertRaises(ToolError):
                    await server.call_tool("list_posts", args)
                service.list_posts.assert_not_awaited()

    async def test_comments_never_expose_email_even_if_backend_does(self):
        server, service, _ = make_server(token())
        service.list_comments.return_value = [
            {"id": 1, "body": "hi", "email": "private@example.com"}
        ]
        service.get_comment.return_value = service.list_comments.return_value[0]
        result = await server.call_tool("list_comments", {})
        self.assertNotIn("email", result.structured_content["comments"][0])
        result = await server.call_tool("get_comment", {"comment_id": 1})
        self.assertNotIn("email", result.structured_content["comment"])

    async def test_publish_preserves_exact_reviewed_fingerprint_and_date(self):
        server, service, _ = make_server(token(scopes=[READ, PUBLISH]))
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
        result = await server.call_tool("publish_post", args)
        service.publish_post.assert_awaited_once_with(
            "draft", published_at="2026-10-09", expected_content_sha256="d" * 64
        )
        self.assertIs(result.structured_content["ok"], True)
        service.get_post.assert_not_awaited()

    async def test_expected_errors_are_safe_tool_errors(self):
        server, service, _ = make_server(token())
        service.get_post.side_effect = MataroaError("not_found", "Post not found.")
        with self.assertRaisesRegex(ToolError, "not_found: Post not found"):
            await server.call_tool("get_post", {"slug": "missing"})

    async def test_streamable_http_rejects_missing_and_wrong_audience_tokens(self):

        class Verifier:
            async def verify_token(self, value):
                if value == "good":
                    return token()
                if value == "wrong-audience":
                    return token(resource="https://other.example/mcp")
                return None

        server = create_server(
            backend_factory=lambda _: AsyncMock(),
            token_verifier=Verifier(),
            issuer_url="https://mataroa.blog",
            resource_url=RESOURCE,
            ui_html="<main>Test</main>",
        )
        app = server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                allowed_hosts=["mataroa.blog"]
            ),
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="https://mataroa.blog"
            ) as client,
        ):
            headers = {"Accept": "application/json, text/event-stream"}
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
            for bearer in [None, "bad", "wrong-audience"]:
                h = dict(headers)
                if bearer:
                    h["Authorization"] = f"Bearer {bearer}"
                response = await client.post("/mcp", json=request, headers=h)
                self.assertEqual(response.status_code, 401)
                self.assertIn(
                    "resource_metadata=", response.headers["WWW-Authenticate"]
                )
            response = await client.post(
                "/mcp",
                json=request,
                headers={**headers, "Authorization": "Bearer good"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["serverInfo"]["name"], "mataroa")
