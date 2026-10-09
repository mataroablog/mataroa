"""Django/WSGI requests → OAuth verification → owner-scoped ORM tools."""

from datetime import timedelta
from unittest import SkipTest

from django.conf import settings
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from main.models import Post, User

if not settings.MATAROA_CHATGPT_ENABLED:
    raise SkipTest("Enable the ChatGPT integration to run OAuth tests.")

from main.models import OAuthClient, OAuthGrant, OAuthToken  # noqa: E402
from mataroa.oauth import token_hash  # noqa: E402


class HTTPIntegrationTests(TransactionTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.app = OAuthClient.objects.create(
            client_id="test-chatgpt",
            client_type="public",
            redirect_uris="https://chatgpt.com/connector_platform_oauth_redirect",
            name="ChatGPT test",
        )
        self.alice = User.objects.create_user(username="alice")
        self.bob = User.objects.create_user(username="bob")
        self.alice_post = Post.objects.create(
            owner=self.alice,
            title="Alice private",
            slug="shared",
            body="Alice only",
            published_at=None,
        )
        self.bob_post = Post.objects.create(
            owner=self.bob,
            title="Bob private",
            slug="shared",
            body="Bob only",
            published_at=None,
        )
        self.tokens = {}
        for name, user, scope in [
            ("alice", self.alice, "blog:read drafts:write posts:publish"),
            ("bob", self.bob, "blog:read"),
        ]:
            value = f"disposable-{name}-integration-token"
            grant = OAuthGrant.objects.create(
                client=self.app,
                user=user,
                scope=scope,
                resource="https://mataroa.blog/mcp",
                redirect_uri=self.app.redirect_uris,
                code_hash=token_hash(f"code-{name}"),
                code_challenge="x" * 43,
                code_expires=timezone.now(),
                consumed=True,
            )
            OAuthToken.objects.create(
                grant=grant,
                scope=scope,
                access_hash=token_hash(value),
                refresh_hash=token_hash(f"refresh-{name}"),
                access_expires=timezone.now() + timedelta(hours=1),
                refresh_expires=timezone.now() + timedelta(days=30),
            )
            self.tokens[name] = value

    def rpc(self, user, method, params=None, **headers):
        return self.client.post(
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            content_type="application/json",
            secure=True,
            headers={
                "host": "mataroa.blog",
                "authorization": f"Bearer {self.tokens[user]}",
                "accept": "application/json, text/event-stream",
                "mcp-protocol-version": "2025-11-25",
                **headers,
            },
        )

    def request(self, user, name, args):
        return self.rpc(user, "tools/call", {"name": name, "arguments": args})

    def call(self, user, name, args):
        result = self.request(user, name, args)
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["result"]

    def test_same_slug_resolves_to_authenticated_account_over_http(self):
        alice = self.call("alice", "get_post", {"slug": "shared"})
        bob = self.call("bob", "get_post", {"slug": "shared"})
        self.assertEqual(alice["structuredContent"]["post"]["body"], "Alice only")
        self.assertEqual(bob["structuredContent"]["post"]["body"], "Bob only")

    def test_read_only_token_cannot_publish_and_no_data_changes(self):
        read = self.call("bob", "get_post", {"slug": "shared"})
        denied = self.call(
            "bob",
            "publish_post",
            {
                "slug": "shared",
                "published_at": "2026-10-08",
                "expected_content_sha256": read["structuredContent"]["post"][
                    "content_sha256"
                ],
            },
        )
        self.assertTrue(denied["isError"])
        self.bob_post.refresh_from_db()
        self.assertIsNone(self.bob_post.published_at)

    def test_reviewed_draft_publishes_through_real_transport(self):
        read = self.call("alice", "get_post", {"slug": "shared"})
        result = self.call(
            "alice",
            "publish_post",
            {
                "slug": "shared",
                "published_at": "2026-10-08",
                "expected_content_sha256": read["structuredContent"]["post"][
                    "content_sha256"
                ],
            },
        )
        self.assertTrue(result["structuredContent"]["ok"])
        self.alice_post.refresh_from_db()
        self.bob_post.refresh_from_db()
        self.assertEqual(self.alice_post.published_at.isoformat(), "2026-10-08")
        self.assertIsNone(self.bob_post.published_at)

    def test_revocation_blocks_next_http_request(self):
        OAuthGrant.objects.filter(user=self.alice).delete()
        result = self.request("alice", "list_posts", {})
        self.assertEqual(result.status_code, 401)

    def raw(self, data, **headers):
        return self.client.generic(
            "POST",
            "/mcp",
            data,
            content_type="application/json",
            secure=True,
            headers={
                "host": "mataroa.blog",
                "authorization": f"Bearer {self.tokens['alice']}",
                "accept": "application/json, text/event-stream",
                **headers,
            },
        )

    def test_initialization_negotiates_versions_and_only_implemented_capabilities(self):
        for version in ("2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"):
            result = self.rpc(
                "alice",
                "initialize",
                {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
            self.assertEqual(result.status_code, 200)
            result = result.json()["result"]
            self.assertEqual(
                result["protocolVersion"],
                version if version != "2099-01-01" else "2025-11-25",
            )
            self.assertEqual(result["serverInfo"]["name"], "mataroa")
            self.assertEqual(
                set(result["capabilities"]), {"tools", "resources", "extensions"}
            )
            self.assertIn("openai/extensions", result["capabilities"]["extensions"])
            self.assertNotIn("Mcp-Session-Id", self.rpc("alice", "ping"))
        self.assertEqual(
            self.rpc("alice", "initialize", {}).json()["error"]["code"], -32602
        )
        self.assertEqual(
            self.rpc("alice", "ping", **{"mcp-protocol-version": "bad"}).status_code,
            400,
        )
        self.assertEqual(
            self.raw(b'{"jsonrpc":"2.0","id":"ping","method":"ping"}').json(),
            {"jsonrpc": "2.0", "id": "ping", "result": {}},
        )

    def test_tools_resources_and_mentions_over_http(self):
        from main.mcp.server import APP_MIME_TYPE, LIBRARY_URI

        tools = {
            tool["name"]: tool
            for tool in self.rpc("alice", "tools/list").json()["result"]["tools"]
        }
        self.assertEqual(len(tools), 11)
        self.assertTrue(tools["list_posts"]["annotations"]["readOnlyHint"])
        self.assertEqual(
            tools["open_library"]["_meta"]["ui"]["resourceUri"], LIBRARY_URI
        )
        self.assertEqual(tools["search_mentions"]["_meta"]["ui"]["visibility"], ["app"])
        opened = self.call("alice", "open_library", {})
        self.assertEqual(
            opened["structuredContent"]["posts"][0]["title"], "Alice private"
        )
        mention = self.call("alice", "search_mentions", {"query": "Alice"})[
            "structuredContent"
        ]["items"][0]
        self.assertEqual(mention["uri"], "mataroa://posts/shared")
        self.assertEqual(mention["type"], "resource_link")
        self.assertEqual(
            self.call("bob", "search_mentions", {"query": "Alice"})[
                "structuredContent"
            ],
            {"items": []},
        )
        resources = self.rpc("alice", "resources/list").json()["result"]["resources"]
        self.assertEqual(resources[0]["mimeType"], APP_MIME_TYPE)
        templates = self.rpc("alice", "resources/templates/list").json()["result"][
            "resourceTemplates"
        ]
        self.assertEqual(templates[0]["uriTemplate"], "mataroa://posts/{slug}")
        ui = self.rpc("alice", "resources/read", {"uri": LIBRARY_URI}).json()["result"][
            "contents"
        ][0]
        self.assertIn('id="library-title"', ui["text"])
        self.assertEqual(
            ui["_meta"]["ui"]["csp"]["resourceDomains"], ["https://mataroa.blog"]
        )
        self.assertEqual(ui["_meta"]["openai/ui"]["preferredDisplayMode"], "fullscreen")
        for user, title in (("alice", "Alice private"), ("bob", "Bob private")):
            post = self.rpc(user, "resources/read", {"uri": mention["uri"]}).json()[
                "result"
            ]["contents"][0]
            self.assertIn(title, post["text"])
            self.assertEqual(post["mimeType"], "text/markdown")

    def test_bad_methods_tools_resources_and_cursors(self):
        for method, params, code in (
            ("unknown", {}, -32601),
            ("resources/subscribe", {}, -32601),
            ("tools/call", {"name": "__init__"}, -32602),
            ("tools/call", {"name": ["list_posts"]}, -32602),
            (
                "tools/call",
                {"name": "create_draft", "arguments": {"title": "Ignored"}, "task": {}},
                -32602,
            ),
            ("tools/list", {"cursor": "unknown"}, -32602),
            ("resources/read", {"uri": "mataroa://posts/missing"}, -32002),
            ("resources/read", {"uri": "mataroa://posts/shared?owner=bob"}, -32002),
            ("resources/read", {"uri": "file:///etc/passwd"}, -32002),
            ("resources/read", {"uri": {}}, -32002),
        ):
            with self.subTest(method=method, params=params):
                self.assertEqual(
                    self.rpc("alice", method, params).json()["error"]["code"], code
                )
        self.assertEqual(Post.objects.count(), 2)

    def test_auth_challenge_does_not_accept_session_or_api_key(self):
        self.client.force_login(self.alice)
        for authorization in (
            "",
            "Bearer wrong",
            f"Bearer {self.alice.api_key}",
            "Basic bogus",
            "Bearer bad token",
        ):
            response = self.rpc("alice", "tools/list", authorization=authorization)
            self.assertEqual(response.status_code, 401)
            self.assertIn(
                'resource_metadata="https://mataroa.blog/.well-known/oauth-protected-resource/mcp"',
                response["WWW-Authenticate"],
            )
            self.assertEqual(response["Cache-Control"], "no-store")
        # Header names and bearer scheme are case-insensitive.
        self.assertEqual(
            self.rpc(
                "alice", "ping", authorization=f"bearer {self.tokens['alice']}"
            ).status_code,
            200,
        )

    def test_expired_wrong_audience_and_inactive_tokens_are_rejected(self):
        grant = OAuthGrant.objects.get(user=self.alice)
        grant.resource = "https://other.example/mcp"
        grant.save()
        self.assertEqual(self.request("alice", "list_posts", {}).status_code, 401)
        grant.resource = "https://mataroa.blog/mcp"
        grant.save()
        self.alice.is_active = False
        self.alice.save()
        self.assertEqual(self.request("alice", "list_posts", {}).status_code, 401)
        self.alice.is_active = True
        self.alice.save()
        OAuthToken.objects.filter(grant=grant).update(
            access_expires=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.request("alice", "list_posts", {}).status_code, 401)

    def test_host_origin_and_https_checks_prevent_cross_domain_redirects(self):
        for host in ("evil.example", "bob.mataroa.blog", "mataroa.blog:1234"):
            self.assertEqual(self.rpc("alice", "ping", host=host).status_code, 400)
        self.bob.custom_domain = "bob.example"
        self.bob.save()
        self.assertEqual(self.rpc("alice", "ping", host="bob.example").status_code, 400)
        for origin in (
            "https://evil.example",
            "null",
            "https://chatgpt.com.evil.example",
            "",
        ):
            self.assertEqual(self.rpc("alice", "ping", origin=origin).status_code, 403)
        for origin in ("https://chatgpt.com", "https://mataroa.blog"):
            self.assertEqual(self.rpc("alice", "ping", origin=origin).status_code, 200)
        response = self.client.post(
            "/mcp", {}, content_type="application/json", HTTP_HOST="mataroa.blog"
        )
        self.assertEqual(response.status_code, 400)

    def test_http_methods_content_type_and_accept(self):
        for method in ("GET", "DELETE", "PUT", "OPTIONS", "HEAD"):
            response = self.client.generic(
                method,
                "/mcp",
                secure=True,
                headers={
                    "host": "mataroa.blog",
                    "authorization": f"Bearer {self.tokens['alice']}",
                },
            )
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "POST")
        self.assertEqual(
            self.client.post(
                "/mcp",
                {},
                secure=True,
                headers={
                    "host": "mataroa.blog",
                    "authorization": f"Bearer {self.tokens['alice']}",
                },
            ).status_code,
            415,
        )
        for accept in (
            "text/html",
            "application/json",
            "text/event-stream",
            "application/json;q=0, text/event-stream",
        ):
            self.assertEqual(self.rpc("alice", "ping", accept=accept).status_code, 406)

    def test_bad_json_and_invalid_envelopes_never_dispatch_tools(self):
        for body in (
            b"{",
            b"\xff",
            b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}',
            b"[" * 2000,
        ):
            self.assertEqual(self.raw(body).json()["error"]["code"], -32700)
        import json

        for message in (
            [],
            [{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
            None,
            42,
            {"jsonrpc": "1.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": True, "method": "ping"},
            {"jsonrpc": "2.0", "id": None, "method": "ping"},
            {"jsonrpc": "2.0", "id": 1, "method": []},
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "result": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
        ):
            with self.subTest(message=message):
                self.assertEqual(self.raw(json.dumps(message)).status_code, 400)
        self.assertEqual(Post.objects.count(), 2)

    def test_notifications_and_client_responses_are_empty_202_and_never_write(self):
        import json

        for message in (
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 7},
            },
            {"jsonrpc": "2.0", "method": "notifications/unknown"},
            {"jsonrpc": "2.0", "id": 7, "result": {}},
            {"jsonrpc": "2.0", "id": 7, "error": {"code": -1, "message": "Cancelled"}},
        ):
            response = self.raw(json.dumps(message))
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.content, b"")
        response = self.raw(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "create_draft",
                        "arguments": {"title": "Never write"},
                    },
                }
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Post.objects.count(), 2)

    def test_request_size_is_bounded_with_and_without_content_length(self):
        from io import BytesIO

        from django.test import RequestFactory

        from main.views.mcp import MAX_BODY, endpoint

        body = b" " * (MAX_BODY + 1)
        self.assertEqual(self.raw(body).status_code, 413)
        request = RequestFactory().post(
            "/mcp",
            b"{}",
            content_type="application/json",
            secure=True,
            HTTP_HOST="mataroa.blog",
            HTTP_AUTHORIZATION=f"Bearer {self.tokens['alice']}",
            HTTP_ACCEPT="application/json, text/event-stream",
        )
        request.META.pop("CONTENT_LENGTH", None)
        request._stream = BytesIO(body)
        self.assertEqual(endpoint(request).status_code, 413)
        self.assertEqual(request._stream.tell(), MAX_BODY + 1)

    def test_strict_tool_arguments_cannot_change_owner_or_bypass_bounds(self):
        for name, args in (
            ("list_posts", {"limit": True}),
            ("list_posts", {"limit": "5"}),
            ("list_posts", {"offset": -1}),
            ("list_posts", {"status": "private"}),
            ("list_posts", {"query": "x" * 301}),
            ("list_posts", []),
            ("get_post", {"slug": "shared\n"}),
            ("get_post", {"slug": "shared", "user_id": self.bob.pk}),
            ("list_comments", {"include_email": True}),
            ("list_comments", {"pending_only": "false"}),
            ("get_comment", {"comment_id": False}),
            ("create_draft", {"title": "x" * 301}),
            ("create_draft", {"title": "x", "body": "x" * 1_000_001}),
            ("create_draft", {"title": "\ud800"}),
            ("create_draft", {}),
            ("update_draft", {"slug": "shared", "expected_content_sha256": "g" * 64}),
        ):
            with self.subTest(name=name, args_type=type(args)):
                result = self.call("alice", name, args)
                self.assertTrue(result["isError"])
                self.assertNotIn("structuredContent", result)
        self.assertEqual(Post.objects.count(), 2)

    def test_unexpected_errors_do_not_expose_drafts_credentials_or_tracebacks(self):
        from unittest.mock import patch

        secret = "private-draft-and-token"
        with patch(
            "main.mcp.backend.DjangoBlogBackend.list_posts",
            side_effect=RuntimeError(secret),
        ):
            response = self.request("alice", "list_posts", {})
        self.assertEqual(response.json()["error"]["code"], -32603)
        self.assertNotIn(secret.encode(), response.content)
        with patch(
            "main.views.mcp.verify_access_token", side_effect=RuntimeError(secret)
        ):
            response = self.request("alice", "list_posts", {})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], -32603)
        self.assertNotIn(secret.encode(), response.content)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_real_wsgi_entrypoint_runs_django_request_lifecycle(self):
        import json

        from django.core.signals import request_finished, request_started
        from django.test import RequestFactory

        from mataroa.wsgi import application

        events = []

        def started(**kwargs):
            events.append("start")

        def finished(**kwargs):
            events.append("finish")

        request_started.connect(started)
        request_finished.connect(finished)
        try:
            request = RequestFactory().post(
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "get_post", "arguments": {"slug": "shared"}},
                },
                content_type="application/json",
                secure=True,
                HTTP_HOST="mataroa.blog",
                HTTP_AUTHORIZATION=f"Bearer {self.tokens['alice']}",
                HTTP_ACCEPT="application/json, text/event-stream",
            )
            statuses = []
            response = application(
                request.environ, lambda status, headers: statuses.append(status)
            )
            body = b"".join(response)
            response.close()
            self.assertEqual(statuses, ["200 OK"])
            self.assertEqual(
                json.loads(body)["result"]["structuredContent"]["post"]["body"],
                "Alice only",
            )
            self.assertEqual(events, ["start", "finish"])
        finally:
            request_started.disconnect(started)
            request_finished.disconnect(finished)

    def modern(self, method, params=None, *, metadata=None, **headers):
        params = dict(params or {})
        params["_meta"] = (
            metadata
            if metadata is not None
            else {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        )
        routing = {"mcp-method": method, "mcp-protocol-version": "2026-07-28"}
        name = params.get("name", params.get("uri"))
        if name is not None:
            routing["mcp-name"] = name
        return self.rpc("alice", method, params, **{**routing, **headers})

    def test_modern_discovery_and_inline_calls_work_without_initialization(self):
        result = self.modern("server/discover").json()["result"]
        self.assertIn("2026-07-28", result["supportedVersions"])
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "mataroa"
        )
        self.assertEqual(result["cacheScope"], "private")
        self.assertEqual(result["ttlMs"], 0)
        result = self.modern(
            "tools/call", {"name": "get_post", "arguments": {"slug": "shared"}}
        ).json()["result"]
        self.assertEqual(result["structuredContent"]["post"]["body"], "Alice only")
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(self.modern("ping").status_code, 404)
        self.assertEqual(self.modern("unknown").json()["error"]["code"], -32601)

    def test_modern_metadata_and_mirrored_headers_are_checked_before_writes(self):
        params = {"name": "create_draft", "arguments": {"title": "Never written"}}
        for headers in (
            {"mcp-method": "tools/list"},
            {"mcp-method": ""},
            {"mcp-name": "get_post"},
            {"mcp-name": ""},
            {"mcp-name": "=?base64?bad!?="},
            {"mcp-protocol-version": "2025-11-25"},
        ):
            with self.subTest(headers=headers):
                response = self.modern("tools/call", params, **headers)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], -32020)
        for meta in (
            {},
            {"io.modelcontextprotocol/protocolVersion": "2026-07-28"},
            {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": [],
            },
        ):
            self.assertEqual(
                self.modern("tools/call", params, metadata=meta).json()["error"][
                    "code"
                ],
                -32602,
            )
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2099-01-01",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        error = self.modern(
            "tools/call",
            params,
            metadata=meta,
            **{"mcp-protocol-version": "2099-01-01"},
        ).json()["error"]
        self.assertEqual(error["code"], -32022)
        self.assertIn("2026-07-28", error["data"]["supported"])
        self.assertEqual(error["data"]["requested"], "2099-01-01")
        self.assertEqual(Post.objects.count(), 2)

    def test_modern_encoded_resource_header(self):
        import base64

        uri = "mataroa://posts/shared"
        encoded = "=?base64?" + base64.b64encode(uri.encode()).decode() + "?="
        response = self.modern("resources/read", {"uri": uri}, **{"mcp-name": encoded})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alice private", response.json()["result"]["contents"][0]["text"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_owners_through_one_wsgi_application(self):
        import json
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from unittest.mock import patch

        from django.db import close_old_connections
        from django.test import RequestFactory

        from main.mcp.backend import DjangoBlogBackend
        from mataroa.wsgi import application

        barrier = Barrier(2)
        original = DjangoBlogBackend.list_posts

        def overlap(backend):
            barrier.wait(timeout=5)
            return original(backend)

        def read(user):
            close_old_connections()
            try:
                request = RequestFactory().post(
                    "/mcp",
                    {
                        "jsonrpc": "2.0",
                        "id": user,
                        "method": "tools/call",
                        "params": {"name": "list_posts"},
                    },
                    content_type="application/json",
                    secure=True,
                    HTTP_HOST="mataroa.blog",
                    HTTP_AUTHORIZATION=f"Bearer {self.tokens[user]}",
                    HTTP_ACCEPT="application/json, text/event-stream",
                )
                response = application(request.environ, lambda status, headers: None)
                try:
                    return json.loads(b"".join(response))["result"][
                        "structuredContent"
                    ]["posts"]
                finally:
                    response.close()
            finally:
                close_old_connections()

        with (
            patch.object(DjangoBlogBackend, "list_posts", overlap),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            alice, bob = list(pool.map(read, ("alice", "bob")))
        self.assertEqual([post["title"] for post in alice], ["Alice private"])
        self.assertEqual([post["title"] for post in bob], ["Bob private"])
