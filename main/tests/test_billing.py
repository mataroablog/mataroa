from datetime import datetime, timedelta
from unittest.mock import patch

import stripe
from django.test import TestCase
from django.urls import reverse

from main import models
from main.views import billing


class BillingCannotChangeIsPremiumTestCase(TestCase):
    """Test user cannot change their is_premium flag without going through billing."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)

    def test_update_billing_settings(self):
        data = {
            "username": "alice",
            "is_premium": True,
        }
        self.client.post(reverse("user_update"), data)
        self.assertFalse(models.User.objects.get(id=self.user.id).is_premium)


class BillingIndexGrandfatherTestCase(TestCase):
    """Test billing pages work accordingly for grandathered user."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.is_grandfathered = True
        self.user.save()
        self.client.force_login(self.user)

    def test_index(self):
        response = self.client.get(reverse("billing_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Grandfather Plan")

    def test_cannot_subscribe(self):
        response = self.client.post(reverse("billing_subscription"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("dashboard"))

    def test_cannot_cancel_get(self):
        response = self.client.get(reverse("billing_subscription_cancel"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("dashboard"))


class BillingIndexFreeTestCase(TestCase):
    """Test billing index works for free user."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.save()
        self.client.force_login(self.user)

    def test_index(self):
        with (
            patch.object(
                stripe.Customer, "create", return_value={"id": "cus_123abcdefg"}
            ),
            patch.object(billing, "_get_stripe_subscription", return_value=None),
            patch.object(
                billing,
                "_get_payment_methods",
            ),
            patch.object(billing, "_get_invoices"),
        ):
            response = self.client.get(reverse("billing_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Free Plan")


class BillingIndexPremiumTestCase(TestCase):
    """Test billing index works for premium user."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.is_premium = True
        self.user.save()
        self.client.force_login(self.user)

    def test_index(self):
        one_year_later = datetime.now() + timedelta(days=365)
        subscription = {
            "current_period_end": one_year_later.timestamp(),
            "current_period_start": datetime.now().timestamp(),
        }
        with (
            patch.object(
                stripe.Customer, "create", return_value={"id": "cus_123abcdefg"}
            ),
            patch.object(
                billing,
                "_get_stripe_subscription",
                return_value=subscription,
            ),
            patch.object(billing, "_get_payment_methods"),
            patch.object(billing, "_get_invoices"),
        ):
            response = self.client.get(reverse("billing_index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Premium Plan")


class BillingCardAddTestCase(TestCase):
    """Test billing card add functionality."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.is_premium = True
        self.user.save()
        self.client.force_login(self.user)

    def test_card_add_get(self):
        with patch.object(
            stripe.SetupIntent, "create", return_value={"client_secret": "seti_123abc"}
        ):
            response = self.client.get(reverse("billing_card"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Add card")

    def test_card_add_post(self):
        one_year_later = datetime.now() + timedelta(days=365)
        subscription = {
            "current_period_end": one_year_later.timestamp(),
            "current_period_start": datetime.now().timestamp(),
        }
        with (
            patch.object(
                stripe.Customer, "create", return_value={"id": "cus_123abcdefg"}
            ),
            patch.object(
                billing,
                "_get_stripe_subscription",
                return_value=subscription,
            ),
            patch.object(billing, "_get_payment_methods"),
            patch.object(billing, "_get_invoices"),
        ):
            response = self.client.post(
                reverse("billing_card"),
                data={"card_token": "tok_123"},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Premium Plan")


class BillingCancelSubscriptionTestCase(TestCase):
    """Test billing cancel subscription."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.is_premium = True
        self.user.stripe_customer_id = "cus_123abcdefg"
        self.user.save()
        self.client.force_login(self.user)

    def test_cancel_subscription_get(self):
        one_year_later = datetime.now() + timedelta(days=365)
        subscription = {
            "current_period_end": one_year_later.timestamp(),
            "current_period_start": datetime.now().timestamp(),
        }
        with patch.object(
            billing,
            "_get_stripe_subscription",
            return_value=subscription,
        ):
            response = self.client.get(reverse("billing_subscription_cancel"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Cancel Premium")

    def test_cancel_subscription_post(self):
        with (
            patch.object(stripe.Subscription, "delete"),
            patch.object(
                billing,
                "_get_stripe_subscription",
                return_value={"id": "sub_123"},
            ),
        ):
            response = self.client.post(reverse("billing_subscription_cancel"))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(models.User.objects.get(id=self.user.id).is_premium)


class BillingCancelSubscriptionTwiceTestCase(TestCase):
    """Test billing cancel subscription when already canceled."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.stripe_customer_id = "cus_123abcdefg"
        self.user.save()
        self.client.force_login(self.user)

    def test_cancel_subscription_get(self):
        with (
            patch.object(billing, "_get_stripe_subscription", return_value=None),
            patch.object(
                stripe.Customer, "create", return_value={"id": "cus_123abcdefg"}
            ),
            patch.object(
                billing,
                "_get_payment_methods",
            ),
            patch.object(billing, "_get_invoices"),
        ):
            response = self.client.get(reverse("billing_subscription_cancel"))

            # need to check inside with context because billing_index needs
            # __get_stripe_subscription patch
            self.assertRedirects(response, reverse("billing_index"))

    def test_cancel_subscription_post(self):
        with (
            patch.object(stripe.Subscription, "delete"),
            patch.object(
                billing,
                "_get_stripe_subscription",
                return_value=None,
            ),
            patch.object(
                stripe.Customer, "create", return_value={"id": "cus_123abcdefg"}
            ),
            patch.object(
                billing,
                "_get_payment_methods",
            ),
            patch.object(billing, "_get_invoices"),
        ):
            response = self.client.post(reverse("billing_subscription_cancel"))

            self.assertRedirects(response, reverse("billing_index"))
            self.assertFalse(models.User.objects.get(id=self.user.id).is_premium)


class BillingReenableSubscriptionTestCase(TestCase):
    """Test re-enabling subscription after cancelation."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.stripe_customer_id = "cus_123abcdefg"
        self.user.save()
        self.client.force_login(self.user)

    def test_reenable_subscription_post(self):
        one_year_later = datetime.now() + timedelta(days=365)
        subscription = {
            "current_period_end": one_year_later.timestamp(),
            "current_period_start": datetime.now().timestamp(),
        }
        created_subscription = {
            "id": "sub_456abcdefg",
            "latest_invoice": {
                "payment_intent": {
                    "client_secret": "seti_123abc",
                },
            },
        }
        with (
            patch.object(stripe.Subscription, "delete"),
            patch.object(
                billing,
                "_get_stripe_subscription",
                return_value=subscription,
            ),
            patch.object(
                stripe.Customer, "create", return_value={"id": "cus_123abcdefg"}
            ),
            patch.object(
                stripe.Subscription,
                "create",
                return_value=created_subscription,
            ),
            patch.object(
                billing,
                "_get_payment_methods",
            ),
            patch.object(billing, "_get_invoices"),
        ):
            response = self.client.post(reverse("billing_subscription"))

            self.assertRedirects(response, reverse("billing_index"))
            # premium should not be enabled immediately; webhook will enable after successful charge
            self.assertFalse(models.User.objects.get(id=self.user.id).is_premium)
