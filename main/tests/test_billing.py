import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from main import models
from main.views import billing


class BillingEnablePremiumTestCase(TestCase):
    def test_stale_user_instance_does_not_send_duplicate_notification(self):
        user = models.User.objects.create(username="alice")
        stale_user = models.User.objects.get(pk=user.pk)

        with patch.object(billing, "mail_admins") as mail_admins:
            first_enabled = billing._enable_premium(
                user,
                "New premium subscriber from webhook: alice",
            )
            second_enabled = billing._enable_premium(
                stale_user,
                "New premium subscriber from welcome page: alice",
            )

        self.assertTrue(first_enabled)
        self.assertFalse(second_enabled)
        self.assertTrue(stale_user.is_premium)
        self.assertTrue(stale_user.is_approved)
        mail_admins.assert_called_once()


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
        response = self.client.get(reverse("billing_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Grandfather Plan")

    def test_cannot_subscribe(self):
        response = self.client.post(reverse("billing_resubscribe"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("billing_overview"))

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
            response = self.client.get(reverse("billing_overview"))
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
            response = self.client.get(reverse("billing_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Premium Plan")


class BillingSubscribeTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice",
            stripe_customer_id="cus_existing",
            stripe_subscription_id="sub_existing",
        )
        self.client.force_login(self.user)
        self.subscription = stripe.Subscription.construct_from(
            {"id": "sub_existing", "status": "incomplete_expired"},
            "sk_test",
        )
        self.retrieve = self.enterContext(
            patch.object(
                stripe.Subscription, "retrieve", return_value=self.subscription
            )
        )
        self.create_session = self.enterContext(
            patch.object(
                stripe.checkout.Session,
                "create",
                return_value=stripe.checkout.Session.construct_from(
                    {"id": "cs_new", "url": "https://checkout.stripe.com/c/pay/cs_new"},
                    "sk_test",
                ),
            )
        )
        self.create_customer = self.enterContext(
            patch.object(stripe.Customer, "create", return_value={"id": "cus_new"})
        )
        self.enterContext(
            patch.object(
                stripe.Subscription,
                "create",
                side_effect=AssertionError("Checkout creates the subscription"),
            )
        )

    def assert_redirects_to_checkout(self):
        response = self.client.post(reverse("billing_subscribe"))

        self.assertRedirects(
            response,
            "https://checkout.stripe.com/c/pay/cs_new",
            fetch_redirect_response=False,
        )
        self.create_session.assert_called_once()
        kwargs = self.create_session.call_args.kwargs
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["client_reference_id"], str(self.user.id))
        self.assertTrue(
            kwargs["success_url"].endswith(
                reverse("billing_welcome") + "?session_id={CHECKOUT_SESSION_ID}"
            )
        )
        self.assertTrue(kwargs["cancel_url"].endswith(reverse("billing_overview")))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_premium)
        return kwargs

    def test_get_explains_terms_without_contacting_stripe(self):
        response = self.client.get(reverse("billing_subscribe"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue to Stripe")
        self.assertNotContains(response, "js.stripe.com")
        self.create_session.assert_not_called()
        self.retrieve.assert_not_called()
        self.create_customer.assert_not_called()

    def test_post_redirects_to_stripe_checkout(self):
        kwargs = self.assert_redirects_to_checkout()

        self.assertEqual(kwargs["customer"], "cus_existing")
        self.assertEqual(kwargs["line_items"][0]["quantity"], 1)
        self.create_customer.assert_not_called()
        # the subscription ID is only replaced once checkout completes
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_subscription_id, "sub_existing")

    def test_incomplete_subscription_starts_new_checkout(self):
        self.subscription.status = "incomplete"
        self.assert_redirects_to_checkout()

    def test_missing_subscription_starts_new_checkout(self):
        self.retrieve.side_effect = stripe.InvalidRequestError(
            "No such subscription", "id", code="resource_missing"
        )
        self.assert_redirects_to_checkout()

    def test_customer_without_subscription_starts_checkout(self):
        self.user.stripe_subscription_id = None
        self.user.save()
        self.assert_redirects_to_checkout()
        self.retrieve.assert_not_called()

    def test_first_checkout_creates_customer(self):
        self.user.stripe_customer_id = None
        self.user.stripe_subscription_id = None
        self.user.save()

        kwargs = self.assert_redirects_to_checkout()

        self.create_customer.assert_called_once()
        self.assertEqual(kwargs["customer"], "cus_new")
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_customer_id, "cus_new")

    def test_active_subscription_redirects_without_another_payment(self):
        for status in ("active", "trialing"):
            with self.subTest(status=status):
                self.subscription.status = status
                response = self.client.post(reverse("billing_subscribe"))
                self.assertRedirects(
                    response, reverse("billing_overview"), fetch_redirect_response=False
                )
        self.create_session.assert_not_called()

    def test_premium_and_grandfathered_users_do_not_start_checkout(self):
        for field in ("is_premium", "is_grandfathered"):
            for method in ("get", "post"):
                with self.subTest(field=field, method=method):
                    setattr(self.user, field, True)
                    self.user.save()
                    response = getattr(self.client, method)(
                        reverse("billing_subscribe")
                    )
                    self.assertRedirects(
                        response,
                        reverse("billing_overview"),
                        fetch_redirect_response=False,
                    )
                    setattr(self.user, field, False)
                    self.user.save()
        self.retrieve.assert_not_called()
        self.create_session.assert_not_called()
        self.create_customer.assert_not_called()

    def test_invalid_stripe_request_does_not_start_checkout(self):
        self.retrieve.side_effect = stripe.InvalidRequestError(
            "Invalid expansion", "expand", code="parameter_unknown"
        )
        with self.assertRaisesMessage(
            Exception, "Failed to get subscription from Stripe"
        ):
            self.client.post(reverse("billing_subscribe"))
        self.create_session.assert_not_called()

    def test_checkout_failure_shows_error(self):
        self.create_session.side_effect = stripe.APIConnectionError("down")

        response = self.client.post(reverse("billing_subscribe"))

        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        self.assertIn(
            "payment processor unavailable",
            str(list(get_messages(response.wsgi_request))[0]),
        )


class BillingCardAddTestCase(TestCase):
    """Test billing card add functionality."""

    def setUp(self):
        self.user = models.User.objects.create(
            username="alice", is_premium=True, stripe_customer_id="cus_alice"
        )
        self.client.force_login(self.user)
        self.create_session = self.enterContext(
            patch.object(
                stripe.checkout.Session,
                "create",
                return_value=stripe.checkout.Session.construct_from(
                    {
                        "id": "cs_card",
                        "url": "https://checkout.stripe.com/c/pay/cs_card",
                    },
                    "sk_test",
                ),
            )
        )

    def test_card_add_get(self):
        response = self.client.get(reverse("billing_card"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"Add card")
        self.assertContains(response, b"Continue to Stripe")
        self.create_session.assert_not_called()

    def test_card_add_post_redirects_to_stripe_checkout(self):
        response = self.client.post(reverse("billing_card"))

        self.assertRedirects(
            response,
            "https://checkout.stripe.com/c/pay/cs_card",
            fetch_redirect_response=False,
        )
        kwargs = self.create_session.call_args.kwargs
        self.assertEqual(kwargs["mode"], "setup")
        self.assertEqual(kwargs["customer"], "cus_alice")
        self.assertTrue(
            kwargs["success_url"].endswith(
                reverse("billing_card_confirm") + "?session_id={CHECKOUT_SESSION_ID}"
            )
        )

    def test_user_without_customer_cannot_add_card(self):
        self.user.stripe_customer_id = None
        self.user.save()
        response = self.client.post(reverse("billing_card"))
        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        self.create_session.assert_not_called()


class BillingCardConfirmTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice", stripe_customer_id="cus_alice"
        )
        self.client.force_login(self.user)

    def get_confirm(self, session):
        with patch.object(
            stripe.checkout.Session,
            "retrieve",
            return_value=stripe.checkout.Session.construct_from(session, "sk_test"),
        ):
            response = self.client.get(
                reverse("billing_card_confirm"), {"session_id": "cs_card"}
            )
        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_completed_session_shows_success(self):
        messages = self.get_confirm(
            {"id": "cs_card", "customer": "cus_alice", "status": "complete"}
        )
        self.assertEqual(messages, ["payment method added"])

    def test_another_customers_session_is_ignored(self):
        messages = self.get_confirm(
            {"id": "cs_card", "customer": "cus_bob", "status": "complete"}
        )
        self.assertEqual(messages, [])


class BillingCardDefaultTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice",
            is_premium=True,
            stripe_customer_id="cus_alice",
            stripe_subscription_id="sub_alice",
        )
        self.client.force_login(self.user)
        self.subscription = stripe.Subscription.construct_from(
            {
                "id": "sub_alice",
                "status": "active",
                "default_payment_method": "pm_old",
            },
            "sk_test",
        )
        self.enterContext(
            patch.object(
                billing,
                "_get_payment_methods",
                return_value={"pm_old": {}, "pm_new": {}},
            )
        )
        self.retrieve = self.enterContext(
            patch.object(
                stripe.Subscription, "retrieve", return_value=self.subscription
            )
        )
        self.modify_customer = self.enterContext(
            patch.object(stripe.Customer, "modify")
        )
        self.modify_subscription = self.enterContext(
            patch.object(stripe.Subscription, "modify")
        )

    def post_default(self, payment_method_id="pm_new"):
        return self.client.post(
            reverse("billing_card_default", args=[payment_method_id])
        )

    def test_updates_customer_and_subscription_default(self):
        response = self.post_default()

        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        self.modify_customer.assert_called_once_with(
            "cus_alice", invoice_settings={"default_payment_method": "pm_new"}
        )
        self.modify_subscription.assert_called_once_with(
            "sub_alice", default_payment_method="pm_new"
        )

    def test_without_subscription_updates_customer_only(self):
        self.user.stripe_subscription_id = None
        self.user.save()

        self.post_default()

        self.modify_customer.assert_called_once()
        self.retrieve.assert_not_called()
        self.modify_subscription.assert_not_called()

    def test_ended_subscription_is_not_modified(self):
        for status in ("canceled", "incomplete_expired"):
            with self.subTest(status=status):
                self.subscription.status = status
                self.post_default()
        self.modify_subscription.assert_not_called()

    def test_missing_subscription_is_not_modified(self):
        self.retrieve.side_effect = stripe.InvalidRequestError(
            "No such subscription", "id", code="resource_missing"
        )
        self.post_default()
        self.modify_customer.assert_called_once()
        self.modify_subscription.assert_not_called()

    def test_subscription_update_failure_is_reported(self):
        self.modify_subscription.side_effect = stripe.APIConnectionError("down")
        response = self.post_default()
        self.assertEqual(response.status_code, 503)

    def test_card_of_another_customer_is_rejected(self):
        response = self.post_default("pm_bob")
        self.assertEqual(response.status_code, 400)
        self.modify_customer.assert_not_called()
        self.modify_subscription.assert_not_called()


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
            patch.object(stripe.Subscription, "modify"),
            patch.object(
                billing,
                "_get_stripe_subscription",
                return_value={"id": "sub_123"},
            ),
        ):
            response = self.client.post(reverse("billing_subscription_cancel"))

        self.assertEqual(response.status_code, 302)
        # user keeps premium until end of period
        self.assertTrue(models.User.objects.get(id=self.user.id).is_premium)


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

            # need to check inside with context because billing_overview needs
            # __get_stripe_subscription patch
            self.assertRedirects(response, reverse("billing_overview"))

    def test_cancel_subscription_post(self):
        with (
            patch.object(stripe.Subscription, "modify"),
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

            self.assertRedirects(response, reverse("billing_overview"))
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
                "status": "open",
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
            response = self.client.post(reverse("billing_resubscribe"))

            self.assertRedirects(response, reverse("billing_overview"))
            # premium should not be enabled immediately; webhook will enable after successful charge
            self.assertFalse(models.User.objects.get(id=self.user.id).is_premium)

    def test_paid_invoice_enables_premium_without_legacy_payment_intent(self):
        subscription = stripe.Subscription.construct_from(
            {
                "id": "sub_new",
                "status": "active",
                "latest_invoice": {"id": "in_new", "status": "paid"},
            },
            "sk_test",
        )
        with (
            patch.object(
                stripe.Subscription, "create", return_value=subscription
            ) as create,
            patch.object(billing, "_get_payment_methods", return_value={"pm_card": {}}),
            patch.object(billing, "mail_admins") as mail_admins,
        ):
            response = self.client.post(reverse("billing_resubscribe"))

        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        self.assertEqual(create.call_args.kwargs["expand"], ["latest_invoice"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_premium)
        self.assertTrue(self.user.is_approved)
        self.assertEqual(self.user.stripe_subscription_id, "sub_new")
        mail_admins.assert_called_once()

    def test_missing_invoice_does_not_enable_premium(self):
        with (
            patch.object(
                stripe.Subscription,
                "create",
                return_value={"id": "sub_new", "latest_invoice": None},
            ),
            patch.object(billing, "_get_payment_methods", return_value={"pm_card": {}}),
            patch.object(billing, "mail_admins") as mail_admins,
        ):
            response = self.client.post(reverse("billing_resubscribe"))

        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_premium)
        mail_admins.assert_not_called()


class BillingWelcomeTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice",
            stripe_customer_id="cus_alice",
            stripe_subscription_id="sub_old",
        )
        self.client.force_login(self.user)

    def get_welcome(self, session):
        with (
            patch.object(
                stripe.checkout.Session,
                "retrieve",
                return_value=stripe.checkout.Session.construct_from(session, "sk_test"),
            ) as retrieve,
            patch.object(billing, "mail_admins") as mail_admins,
        ):
            response = self.client.get(
                reverse("billing_welcome"), {"session_id": "cs_paid"}
            )
        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        retrieve.assert_called_once_with("cs_paid")
        mail_admins.assert_not_called()
        self.user.refresh_from_db()
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_return_saves_subscription_but_waits_for_webhook(self):
        messages = self.get_welcome(
            {
                "id": "cs_paid",
                "mode": "subscription",
                "customer": "cus_alice",
                "subscription": "sub_new",
            }
        )
        self.assertEqual(self.user.stripe_subscription_id, "sub_new")
        self.assertFalse(self.user.is_premium)
        self.assertFalse(self.user.is_approved)
        self.assertIn("payment is being confirmed", messages[0])

    def test_another_customers_session_is_ignored(self):
        self.get_welcome(
            {
                "id": "cs_paid",
                "mode": "subscription",
                "customer": "cus_bob",
                "subscription": "sub_bob",
            }
        )
        self.assertEqual(self.user.stripe_subscription_id, "sub_old")
        self.assertFalse(self.user.is_premium)

    def test_premium_already_enabled_by_webhook_shows_success(self):
        self.user.is_premium = True
        self.user.is_approved = True
        self.user.save()
        messages = self.get_welcome(
            {
                "id": "cs_paid",
                "mode": "subscription",
                "customer": "cus_alice",
                "subscription": "sub_new",
            }
        )
        self.assertIn("premium subscription enabled", messages)

    def test_missing_session_id_redirects(self):
        with patch.object(stripe.checkout.Session, "retrieve") as retrieve:
            response = self.client.get(reverse("billing_welcome"))
        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        retrieve.assert_not_called()


class BillingWebhookTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice",
            stripe_customer_id="cus_abc123",
        )
        self.client.force_login(self.user)

    def test_missing_secret_rejects_unsigned_events_in_all_environments(self):
        self.client.logout()
        payload = json.dumps(
            {
                "id": "evt_test",
                "object": "event",
                "type": "invoice.payment_succeeded",
                "data": {"object": {"object": "invoice", "customer": "cus_abc123"}},
            }
        )
        for debug in (False, True):
            with (
                self.subTest(debug=debug),
                self.settings(STRIPE_WEBHOOK_SECRET="", DEBUG=debug),
                patch.object(billing, "mail_admins") as mail_admins,
            ):
                response = self.client.post(
                    reverse("billing_stripe_webhook"),
                    data=payload,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 503)
                self.user.refresh_from_db()
                self.assertFalse(self.user.is_premium)
                self.assertFalse(self.user.is_approved)
                mail_admins.assert_not_called()

    def test_missing_signature_is_rejected(self):
        self.client.logout()
        with self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_premium)
        self.assertFalse(self.user.is_approved)

    def test_valid_signature_enables_premium_without_login(self):
        self.client.logout()
        payload = json.dumps(
            {
                "id": "evt_test",
                "object": "event",
                "type": "invoice.payment_succeeded",
                "data": {"object": {"object": "invoice", "customer": "cus_abc123"}},
            }
        )
        timestamp = int(time.time())
        signature = hmac.new(
            b"whsec_test", f"{timestamp}.{payload}".encode(), hashlib.sha256
        ).hexdigest()
        with (
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
            patch.object(billing, "mail_admins"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}",
            )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_premium)
        self.assertTrue(self.user.is_approved)

    def test_invoice_payment_succeeded_enables_premium_and_approved(self):
        invoice_obj = SimpleNamespace(customer="cus_abc123")
        event = SimpleNamespace(
            type="invoice.payment_succeeded",
            data=SimpleNamespace(object=invoice_obj),
        )
        with (
            patch.object(stripe.Webhook, "construct_event", return_value=event),
            patch.object(billing, "get_connection") as mock_get_connection,
            patch.object(billing, "mail_admins") as mock_mail_admins,
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=dummy",
            )

        self.assertEqual(response.status_code, 200)
        user = models.User.objects.get(id=self.user.id)
        self.assertTrue(user.is_premium)
        self.assertTrue(user.is_approved)
        mock_mail_admins.assert_called_once_with(
            "New premium subscriber from webhook: alice",
            self.user.blog_absolute_url,
            connection=mock_get_connection.return_value,
        )
        mock_get_connection.assert_called_once_with(timeout=5)

    def test_checkout_session_completed_saves_subscription(self):
        session = stripe.checkout.Session.construct_from(
            {
                "id": "cs_paid",
                "mode": "subscription",
                "customer": "cus_abc123",
                "subscription": "sub_from_checkout",
            },
            "sk_test",
        )
        event = SimpleNamespace(
            type="checkout.session.completed",
            data=SimpleNamespace(object=session),
        )
        with (
            patch.object(stripe.Webhook, "construct_event", return_value=event),
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=dummy",
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_subscription_id, "sub_from_checkout")
        # premium is enabled by invoice.payment_succeeded, not here
        self.assertFalse(self.user.is_premium)

    def test_customer_subscription_deleted_downgrades_and_clears_subscription(self):
        self.user.is_premium = True
        self.user.stripe_subscription_id = "sub_123"
        self.user.save()

        sub_obj = SimpleNamespace(customer="cus_abc123")
        event = SimpleNamespace(
            type="customer.subscription.deleted",
            data=SimpleNamespace(object=sub_obj),
        )
        with (
            patch.object(stripe.Webhook, "construct_event", return_value=event),
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=dummy",
            )

        self.assertEqual(response.status_code, 200)
        user = models.User.objects.get(id=self.user.id)
        self.assertFalse(user.is_premium)
        self.assertIsNone(user.stripe_subscription_id)

    def test_customer_subscription_deleted_clears_stale_subscription_id(self):
        self.user.stripe_subscription_id = "sub_123"
        self.user.save()

        sub_obj = SimpleNamespace(customer="cus_abc123")
        event = SimpleNamespace(
            type="customer.subscription.deleted",
            data=SimpleNamespace(object=sub_obj),
        )
        with (
            patch.object(stripe.Webhook, "construct_event", return_value=event),
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=dummy",
            )

        self.assertEqual(response.status_code, 200)
        user = models.User.objects.get(id=self.user.id)
        self.assertFalse(user.is_premium)
        self.assertIsNone(user.stripe_subscription_id)

    def test_processing_failure_returns_500_for_stripe_retry(self):
        invoice_obj = SimpleNamespace(customer="cus_abc123")
        event = SimpleNamespace(
            type="invoice.payment_succeeded",
            data=SimpleNamespace(object=invoice_obj),
        )
        with (
            patch.object(stripe.Webhook, "construct_event", return_value=event),
            patch.object(
                models.User.objects,
                "get",
                side_effect=RuntimeError("database unavailable"),
            ),
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=dummy",
            )

        self.assertEqual(response.status_code, 500)

    def test_signature_verification_failure_returns_400(self):
        def _raise_sig_error(*args, **kwargs):
            raise stripe.SignatureVerificationError(
                message="bad signature",
                sig_header="t=1,v1=bad",
                http_body=b"{}",
            )

        with (
            patch.object(
                stripe.Webhook, "construct_event", side_effect=_raise_sig_error
            ),
            self.settings(STRIPE_WEBHOOK_SECRET="whsec_test"),
        ):
            response = self.client.post(
                reverse("billing_stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=bad",
            )

        self.assertEqual(response.status_code, 400)
