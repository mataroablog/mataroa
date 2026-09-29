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
            {
                "id": "sub_existing",
                "status": "incomplete",
                "latest_invoice": {
                    "id": "in_existing",
                    "status": "open",
                    "confirmation_secret": {"client_secret": "pi_existing_secret"},
                },
            },
            "sk_test",
        )
        self.new_subscription = stripe.Subscription.construct_from(
            {
                "id": "sub_new",
                "status": "incomplete",
                "latest_invoice": {
                    "id": "in_new",
                    "status": "open",
                    "confirmation_secret": {"client_secret": "pi_new_secret"},
                },
            },
            "sk_test",
        )
        self.retrieve = self.enterContext(
            patch.object(
                stripe.Subscription, "retrieve", return_value=self.subscription
            )
        )
        self.create = self.enterContext(
            patch.object(
                stripe.Subscription, "create", return_value=self.new_subscription
            )
        )
        self.create_customer = self.enterContext(
            patch.object(stripe.Customer, "create", return_value={"id": "cus_new"})
        )
        self.enterContext(
            patch.object(
                stripe.PaymentIntent,
                "list",
                side_effect=AssertionError("Checkout must use its own invoice"),
            )
        )

    def assert_replacement_checkout(self):
        response = self.client.get(reverse("billing_subscribe"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stripe_client_secret"], "pi_new_secret")
        self.create.assert_called_once()
        self.assertEqual(self.create.call_args.kwargs["customer"], "cus_existing")
        self.assertEqual(
            self.create.call_args.kwargs["expand"],
            ["latest_invoice.confirmation_secret"],
        )
        self.create_customer.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_customer_id, "cus_existing")
        self.assertEqual(self.user.stripe_subscription_id, "sub_new")
        self.assertFalse(self.user.is_premium)

    def test_expired_subscription_is_replaced(self):
        self.subscription.status = "incomplete_expired"
        self.assert_replacement_checkout()

    def test_canceled_subscription_is_replaced(self):
        self.subscription.status = "canceled"
        self.assert_replacement_checkout()

    def test_missing_subscription_is_replaced(self):
        self.retrieve.side_effect = stripe.InvalidRequestError(
            "No such subscription", "id", code="resource_missing"
        )
        self.assert_replacement_checkout()

    def test_customer_without_subscription_gets_new_checkout(self):
        self.user.stripe_subscription_id = None
        self.user.save()
        self.assert_replacement_checkout()
        self.retrieve.assert_not_called()

    def test_first_checkout_creates_customer_and_subscription(self):
        self.user.stripe_customer_id = None
        self.user.stripe_subscription_id = None
        self.user.save()

        response = self.client.get(reverse("billing_subscribe"))

        self.assertEqual(response.status_code, 200)
        self.create_customer.assert_called_once()
        self.assertEqual(self.create.call_args.kwargs["customer"], "cus_new")
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_customer_id, "cus_new")
        self.assertEqual(self.user.stripe_subscription_id, "sub_new")

    def test_incomplete_subscription_reuses_its_invoice_secret(self):
        response = self.client.get(reverse("billing_subscribe"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stripe_client_secret"], "pi_existing_secret")
        self.assertContains(response, "pi_existing_secret")
        self.create.assert_not_called()
        self.create_customer.assert_not_called()
        self.retrieve.assert_called_once_with(
            "sub_existing", expand=["latest_invoice.confirmation_secret"]
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_subscription_id, "sub_existing")

    def test_active_subscription_redirects_without_another_payment(self):
        for status in ("active", "trialing"):
            with self.subTest(status=status):
                self.subscription.status = status
                response = self.client.get(reverse("billing_subscribe"))
                self.assertRedirects(
                    response, reverse("billing_overview"), fetch_redirect_response=False
                )
        self.create.assert_not_called()

    def test_premium_and_grandfathered_users_do_not_start_checkout(self):
        for field in ("is_premium", "is_grandfathered"):
            with self.subTest(field=field):
                setattr(self.user, field, True)
                self.user.save()
                response = self.client.get(reverse("billing_subscribe"))
                self.assertRedirects(
                    response, reverse("billing_overview"), fetch_redirect_response=False
                )
                setattr(self.user, field, False)
        self.retrieve.assert_not_called()
        self.create.assert_not_called()
        self.create_customer.assert_not_called()

    def test_invalid_stripe_request_does_not_create_another_subscription(self):
        self.retrieve.side_effect = stripe.InvalidRequestError(
            "Invalid expansion", "expand", code="parameter_unknown"
        )
        with self.assertRaisesMessage(
            Exception, "Failed to get subscription from Stripe"
        ):
            self.client.get(reverse("billing_subscribe"))
        self.create.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_subscription_id, "sub_existing")

    def test_missing_confirmation_secret_does_not_render_broken_form(self):
        self.subscription.latest_invoice.confirmation_secret = None

        response = self.client.get(reverse("billing_subscribe"))

        self.assertRedirects(
            response, reverse("billing_overview"), fetch_redirect_response=False
        )
        self.assertIn(
            "payment form unavailable",
            str(list(get_messages(response.wsgi_request))[0]),
        )
        self.create.assert_not_called()


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


class BillingWebhookTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice",
            stripe_customer_id="cus_abc123",
        )
        self.client.force_login(self.user)

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
