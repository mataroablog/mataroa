from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from main import models


class OAuthRemovalMigrationTestCase(TransactionTestCase):
    def test_drops_populated_oauth_tables_and_preserves_blog_data(self):
        executor = MigrationExecutor(connection)
        before = [("main", "0117_oauth_admin_names")]
        after = [("main", "0118_remove_oauth")]
        self.addCleanup(executor.migrate, executor.loader.graph.leaf_nodes())
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        User = old_apps.get_model("main", "User")
        Post = old_apps.get_model("main", "Post")
        OAuthClient = old_apps.get_model("main", "OAuthClient")
        OAuthGrant = old_apps.get_model("main", "OAuthGrant")
        OAuthToken = old_apps.get_model("main", "OAuthToken")

        user = User.objects.create(username="oauth-blogger")
        post = Post.objects.create(
            owner=user,
            title="Keep this post",
            slug="keep-this-post",
            body="Blog content",
        )
        client = OAuthClient.objects.create(
            client_id="chatgpt",
            name="ChatGPT",
            client_type="public",
            redirect_uris="https://example.com/callback",
        )
        grant = OAuthGrant.objects.create(
            client=client,
            user=user,
            scope="posts:read",
            resource="https://mataroa.blog/mcp",
            redirect_uri="https://example.com/callback",
            code_hash="a" * 64,
            code_challenge="b" * 43,
            code_expires=timezone.now(),
        )
        OAuthToken.objects.create(
            grant=grant,
            access_hash="c" * 64,
            refresh_hash="d" * 64,
            scope="posts:read",
            access_expires=timezone.now(),
            refresh_expires=timezone.now(),
        )
        oauth_tables = {
            OAuthClient._meta.db_table,
            OAuthGrant._meta.db_table,
            OAuthToken._meta.db_table,
        }
        tables_before = set(connection.introspection.table_names())
        self.assertTrue(oauth_tables <= tables_before)

        executor = MigrationExecutor(connection)
        executor.migrate(after)

        self.assertEqual(
            set(connection.introspection.table_names()), tables_before - oauth_tables
        )
        new_apps = executor.loader.project_state(after).apps
        for model_name in ("OAuthClient", "OAuthGrant", "OAuthToken"):
            with self.assertRaises(LookupError):
                new_apps.get_model("main", model_name)
        saved_user = models.User.objects.get(pk=user.pk)
        self.assertEqual(saved_user.username, user.username)
        self.assertEqual(saved_user.api_key, user.api_key)
        saved_post = models.Post.objects.get(pk=post.pk)
        self.assertEqual(saved_post.owner_id, user.pk)
        self.assertEqual(saved_post.title, post.title)
        self.assertEqual(saved_post.body, post.body)
