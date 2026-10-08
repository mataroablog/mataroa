"""Real ASGI transport → OAuth verifier → tenant-scoped ORM tools."""

from datetime import timedelta
from unittest import SkipTest
from unittest.mock import patch

import httpx
from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import TransactionTestCase
from django.utils import timezone

from main.models import Post, User

if not settings.MATAROA_CHATGPT_ENABLED:
    raise SkipTest("Enable the ChatGPT integration to run OAuth tests.")

from oauth2_provider.models import (  # noqa: E402
    AccessToken,
    Application,
    set_token_value,
)


class HTTPIntegrationTests(TransactionTestCase):
    def setUp(self):
        self.app = Application.objects.create(
            client_id="test-chatgpt",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://chatgpt.com/connector_platform_oauth_redirect",
            name="ChatGPT test",
            skip_authorization=False,
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
            access = AccessToken(
                application=self.app,
                user=user,
                expires=timezone.now() + timedelta(hours=1),
                scope=scope,
                resource=["https://mataroa.blog/mcp"],
            )
            set_token_value(access, value)
            access.save()
            self.tokens[name] = value

    async def request(self, user, name, args):
        from importlib import reload

        import mataroa.asgi

        # Simulate a fresh ASGI worker for this short-lived test event loop.
        runtime = reload(mataroa.asgi)
        application, mcp_application = runtime.application, runtime.mcp_application
        async with (
            mcp_application.router.lifespan_context(mcp_application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(application),
                base_url="https://mataroa.blog",
            ) as client,
        ):
            return await client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {self.tokens[user]}",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-11-25",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                },
            )

    def call(self, user, name, args):
        result = async_to_sync(self.request)(user, name, args)
        self.assertEqual(result.status_code, 200, result.text)
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
        AccessToken.objects.filter(user=self.alice).delete()
        result = async_to_sync(self.request)("alice", "list_posts", {})
        self.assertEqual(result.status_code, 401)

    def test_mcp_route_cleans_up_database_connections_on_auth_rejection(self):
        AccessToken.objects.filter(user=self.alice).delete()
        with patch("django.db.close_old_connections") as cleanup:
            result = async_to_sync(self.request)("alice", "list_posts", {})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(cleanup.call_count, 2)
