from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from main import models


class RedirectGrandfatherMigrationTestCase(TransactionTestCase):
    def test_existing_accounts_keep_access_and_new_accounts_do_not(self):
        executor = MigrationExecutor(connection)
        before = [("main", "0114_user_is_delisted")]
        after = [("main", "0115_user_is_redirect_grandfathered")]
        self.addCleanup(executor.migrate, executor.loader.graph.leaf_nodes())
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        User = old_apps.get_model("main", "User")
        existing = User.objects.create(
            username="existing", redirect_domain="example.com"
        )
        existing_without_redirect = User.objects.create(username="existing-no-redirect")

        executor = MigrationExecutor(connection)
        executor.migrate(after)

        for user_id in (existing.pk, existing_without_redirect.pk):
            user = models.User.objects.get(pk=user_id)
            self.assertTrue(user.is_redirect_grandfathered)
            self.assertTrue(user.has_redirect_features)
        self.assertEqual(
            models.User.objects.get(pk=existing.pk).redirect_domain, "example.com"
        )
        new_user = models.User.objects.create(username="new")
        self.assertFalse(new_user.is_redirect_grandfathered)
        self.assertFalse(new_user.has_redirect_features)
