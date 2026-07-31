from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.core.management import call_command
from django.test import TestCase

from main import models


class StripeSubscriptionList(list):
    has_more = False


class CheckStripeTestCase(TestCase):
    def setUp(self):
        self.active_user = models.User.objects.create(
            username="active",
            is_premium=True,
            stripe_customer_id="cus_active",
            stripe_subscription_id="sub_active",
        )
        self.stale_user = models.User.objects.create(
            username="stale",
            is_premium=True,
            stripe_customer_id="cus_stale",
            stripe_subscription_id="sub_stale",
        )
        self.monero_user = models.User.objects.create(
            username="monero",
            is_premium=True,
            monero_address="monero-address",
            stripe_customer_id="cus_monero",
            stripe_subscription_id="sub_monero",
        )
        self.grandfathered_user = models.User.objects.create(
            username="grandfathered",
            is_premium=True,
            is_grandfathered=True,
            stripe_customer_id="cus_grandfathered",
            stripe_subscription_id="sub_grandfathered",
        )

    def stripe_subscriptions(self):
        return StripeSubscriptionList(
            [SimpleNamespace(id="sub_active", customer="cus_active")]
        )

    def test_dry_run_reports_stale_user_without_changes(self):
        output = StringIO()
        with patch.object(
            stripe.Subscription,
            "list",
            return_value=self.stripe_subscriptions(),
        ):
            call_command("checkstripe", stdout=output)

        self.stale_user.refresh_from_db()
        self.assertTrue(self.stale_user.is_premium)
        self.assertEqual(self.stale_user.stripe_subscription_id, "sub_stale")
        self.assertIn(
            "Premium users excluded from Stripe reconciliation: 2", output.getvalue()
        )
        self.assertIn("Stripe premium users requiring downgrade: 1", output.getvalue())
        self.assertIn("Dry run: no users changed", output.getvalue())

    def test_fix_downgrades_only_stale_stripe_user(self):
        output = StringIO()
        with patch.object(
            stripe.Subscription,
            "list",
            return_value=self.stripe_subscriptions(),
        ):
            call_command("checkstripe", "--fix", stdout=output)

        self.stale_user.refresh_from_db()
        self.active_user.refresh_from_db()
        self.monero_user.refresh_from_db()
        self.grandfathered_user.refresh_from_db()

        self.assertFalse(self.stale_user.is_premium)
        self.assertIsNone(self.stale_user.stripe_subscription_id)
        self.assertTrue(self.active_user.is_premium)
        self.assertTrue(self.monero_user.is_premium)
        self.assertTrue(self.grandfathered_user.is_premium)
        self.assertEqual(self.monero_user.stripe_subscription_id, "sub_monero")
        self.assertEqual(
            self.grandfathered_user.stripe_subscription_id,
            "sub_grandfathered",
        )
        self.assertIn("Downgraded 1 Stripe premium users", output.getvalue())
