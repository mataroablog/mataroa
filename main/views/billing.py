import json
import logging
from datetime import UTC, datetime

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import mail_admins
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic.edit import FormView

from main import forms, models, scheme

logger = logging.getLogger(__name__)


@login_required
def billing_overview(request):
    """
    Renders the billing index page which includes a summary of subscription and
    payment methods.
    """
    # respond for grandfathered users first
    if request.user.is_grandfathered:
        return render(
            request,
            "main/billing_overview.html",
            {
                "is_grandfathered": True,
            },
        )

    # respond for monero case
    if request.user.monero_address:
        return render(request, "main/billing_overview.html")

    stripe.api_key = settings.STRIPE_API_KEY

    # create stripe customer for user if it does not exist
    if not request.user.stripe_customer_id:
        try:
            stripe_response = stripe.Customer.create()
        except stripe.StripeError as ex:
            logger.error(str(ex))
            raise Exception("Failed to create customer on Stripe.") from ex
        request.user.stripe_customer_id = stripe_response["id"]
        request.user.save()

    # get subscription if exists
    current_period_start = None
    current_period_end = None
    subscription_status = None
    if request.user.stripe_subscription_id:
        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        if subscription:
            subscription_status = subscription.get("status")
            if subscription.get("cancel_at_period_end"):
                subscription_status = "canceling"
        # parse period fields when present (even if not active),
        # so "Last payment" can be shown after scheduling cancellation
        latest_invoice = subscription.get("latest_invoice") if subscription else None
        if isinstance(latest_invoice, dict) and latest_invoice.get("status") == "paid":
            items = (subscription or {}).get("items") or {}
            item_data = items.get("data") or []
            first_item = item_data[0] if item_data else {}
            if current_period_start := first_item.get("current_period_start"):
                current_period_start = datetime.fromtimestamp(
                    current_period_start, tz=UTC
                )
            if current_period_end := first_item.get("current_period_end"):
                current_period_end = datetime.fromtimestamp(current_period_end, tz=UTC)

    # transform into list of values
    payment_methods = _get_payment_methods(request.user.stripe_customer_id).values()

    return render(
        request,
        "main/billing_overview.html",
        {
            "stripe_customer_id": request.user.stripe_customer_id,
            "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
            "stripe_price_id": settings.STRIPE_PRICE_ID,
            "current_period_end": current_period_end,
            "current_period_start": current_period_start,
            "subscription_status": subscription_status,
            "payment_methods": payment_methods,
            "invoice_list": _get_invoices(request.user.stripe_customer_id),
        },
    )


def _get_stripe_subscription(stripe_subscription_id):
    stripe.api_key = settings.STRIPE_API_KEY

    try:
        stripe_subscription = stripe.Subscription.retrieve(
            stripe_subscription_id,
            expand=["latest_invoice", "latest_invoice.payment_intent"],
        )
    except stripe.InvalidRequestError as ex:
        logger.warning("Subscription %s not found: %s", stripe_subscription_id, str(ex))
        return None
    except stripe.StripeError as ex:
        logger.error(
            "Failed to get subscription %s from Stripe: %s",
            stripe_subscription_id,
            str(ex),
        )
        raise Exception("Failed to get subscription from Stripe.") from ex

    return stripe_subscription


def _get_payment_methods(stripe_customer_id):
    """Get user's payment methods and transform them into a dictionary."""
    stripe.api_key = settings.STRIPE_API_KEY

    # get default payment method id
    try:
        default_pm_id = stripe.Customer.retrieve(
            stripe_customer_id
        ).invoice_settings.default_payment_method
    except stripe.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to retrieve customer data from Stripe.") from ex

    # get payment methods
    try:
        stripe_payment_methods = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type="card",
        )
    except stripe.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to retrieve payment methods from Stripe.") from ex

    # normalise payment methods
    payment_methods = {}
    for stripe_pm in stripe_payment_methods.data:
        payment_methods[stripe_pm.id] = {
            "id": stripe_pm.id,
            "brand": stripe_pm.card.brand,
            "last4": stripe_pm.card.last4,
            "exp_month": stripe_pm.card.exp_month,
            "exp_year": stripe_pm.card.exp_year,
            "is_default": False,
        }
        if stripe_pm.id == default_pm_id:
            payment_methods[stripe_pm.id]["is_default"] = True

    return payment_methods


def _get_invoices(stripe_customer_id):
    """Get user's invoices and transform them into a dictionary."""
    stripe.api_key = settings.STRIPE_API_KEY

    # get user invoices
    try:
        stripe_invoices = stripe.Invoice.list(customer=stripe_customer_id)
    except stripe.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to retrieve invoices data from Stripe.") from ex

    # normalize invoice objects
    invoice_list = []
    for stripe_inv in stripe_invoices.data:
        invoice_list.append(
            {
                "id": stripe_inv.id,
                "url": stripe_inv.hosted_invoice_url,
                "pdf": stripe_inv.invoice_pdf,
                "period_start": datetime.fromtimestamp(stripe_inv.period_start, tz=UTC),
                "period_end": datetime.fromtimestamp(stripe_inv.period_end, tz=UTC),
                "created": datetime.fromtimestamp(stripe_inv.created, tz=UTC),
            }
        )

    return invoice_list


class BillingSubscribe(LoginRequiredMixin, FormView):
    form_class = forms.StripeForm
    template_name = "main/billing_subscribe.html"
    success_url = reverse_lazy("billing_overview")
    success_message = (
        "payment is processing; premium will be enabled once the charge succeeds"
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stripe_public_key"] = settings.STRIPE_PUBLIC_KEY
        return context

    def get(self, request, *args, **kwargs):
        stripe.api_key = settings.STRIPE_API_KEY

        # ensure customer exists
        if not request.user.stripe_customer_id:
            try:
                created = stripe.Customer.create()
                request.user.stripe_customer_id = created.get("id")
                request.user.save()
            except stripe.StripeError as ex:
                logger.error("Failed creating customer before subscribe: %s", str(ex))
                messages.error(
                    request, "payment processor unavailable; please try again later"
                )
                return redirect("billing_overview")

        url = f"{scheme.get_protocol()}//{settings.CANONICAL_HOST}"
        url += reverse_lazy("billing_welcome")

        if request.user.stripe_subscription_id:
            stripe_subscription = _get_stripe_subscription(
                request.user.stripe_subscription_id
            )
            # create new subscription if:
            # * subscription is canceled but webhook was not received (yet)
            # * stripe fails or returns None
            if (
                stripe_subscription.get("status") == "canceled"
                or stripe_subscription is None
            ):
                stripe_subscription = _create_stripe_subscription(
                    request.user.stripe_customer_id
                )
        else:
            stripe_subscription = _create_stripe_subscription(
                request.user.stripe_customer_id
            )
        request.user.stripe_subscription_id = stripe_subscription.get("id")
        request.user.save()

        payment_intents = stripe.PaymentIntent.list(
            customer=request.user.stripe_customer_id, limit=1
        )
        client_secret = None
        if payment_intents.data:
            client_secret = payment_intents.data[0].client_secret

        context = self.get_context_data()
        context["stripe_client_secret"] = client_secret
        context["stripe_return_url"] = url
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)

        if form.is_valid():
            messages.success(request, self.success_message)
            return self.form_valid(form)
        else:
            return self.form_invalid(form)


def _create_stripe_subscription(customer_id):
    stripe.api_key = settings.STRIPE_API_KEY

    # expand subscription's latest invoice and invoice's payment_intent
    # so we can pass it to the front end to confirm the payment
    try:
        stripe_subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[
                {
                    "price": settings.STRIPE_PRICE_ID,
                }
            ],
            payment_behavior="default_incomplete",
            payment_settings={"save_default_payment_method": "on_subscription"},
        )
        logger.info(f"Created subscription: {stripe_subscription.get('id')}")
    except stripe.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to create subscription on Stripe.") from ex

    return stripe_subscription


class BillingCard(LoginRequiredMixin, FormView):
    form_class = forms.StripeForm
    template_name = "main/billing_card.html"
    success_url = reverse_lazy("billing_overview")
    success_message = "new card added"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stripe_public_key"] = settings.STRIPE_PUBLIC_KEY
        return context

    def get(self, request, *args, **kwargs):
        stripe.api_key = settings.STRIPE_API_KEY
        context = self.get_context_data()

        data = _create_setup_intent(request.user.stripe_customer_id)
        context["stripe_client_secret"] = data["stripe_client_secret"]

        url = f"{scheme.get_protocol()}//{settings.CANONICAL_HOST}"
        url += reverse_lazy("billing_card_confirm")
        context["stripe_return_url"] = url

        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)

        if form.is_valid():
            messages.success(request, self.success_message)
            return self.form_valid(form)
        else:
            return self.form_invalid(form)


def _create_setup_intent(customer_id):
    stripe.api_key = settings.STRIPE_API_KEY

    try:
        stripe_setup_intent = stripe.SetupIntent.create(
            automatic_payment_methods={"enabled": True},
            customer=customer_id,
        )
    except stripe.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to create setup intent on Stripe.") from ex

    return {
        "stripe_client_secret": stripe_setup_intent["client_secret"],
    }


class BillingCardDelete(LoginRequiredMixin, View):
    """View that deletes a card from a user on Stripe."""

    template_name = "main/billing_card_confirm_delete.html"
    success_url = reverse_lazy("billing_overview")
    success_message = "card deleted"
    slug_url_kwarg = "stripe_payment_method_id"

    # dict of valid payment methods with id as key and an obj as val
    stripe_payment_methods = {}

    def get_context_data(self, **kwargs):
        card_id = self.kwargs.get(self.slug_url_kwarg)
        context = {
            "card": self.stripe_payment_methods[card_id],
        }
        return context

    def post(self, request, *args, **kwargs):
        card_id = self.kwargs.get(self.slug_url_kwarg)
        try:
            stripe.PaymentMethod.detach(card_id)
        except stripe.StripeError as ex:
            logger.error(str(ex))
            messages.error(request, "payment processor unresponsive; please try again")
            return redirect(reverse_lazy("billing_overview"))

        messages.success(request, self.success_message)
        return HttpResponseRedirect(self.success_url)

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return render(request, self.template_name, context)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.stripe_customer_id:
            mail_admins(
                "User tried to delete card without stripe_customer_id",
                f"user.id={request.user.id}\nuser.username={request.user.username}",
            )
            messages.error(
                request,
                "something has gone terribly wrong but we were just notified about it",
            )
            return redirect("dashboard")

        self.stripe_payment_methods = _get_payment_methods(
            request.user.stripe_customer_id
        )

        # check if card id is valid for user
        card_id = self.kwargs.get(self.slug_url_kwarg)
        if card_id not in self.stripe_payment_methods:
            mail_admins(
                "User tried to delete card with invalid Stripe card ID",
                f"user.id={request.user.id}\nuser.username={request.user.username}",
            )
            messages.error(
                request,
                "this is not a valid card ID",
            )
            return redirect("dashboard")

        return super().dispatch(request, *args, **kwargs)


@require_POST
@login_required
def billing_card_default(request, stripe_payment_method_id):
    """View method that changes the default card of a user on Stripe."""

    stripe_payment_methods = _get_payment_methods(request.user.stripe_customer_id)

    if stripe_payment_method_id not in stripe_payment_methods:
        return HttpResponseBadRequest("Invalid Card ID.")

    stripe.api_key = settings.STRIPE_API_KEY
    try:
        stripe.Customer.modify(
            request.user.stripe_customer_id,
            invoice_settings={
                "default_payment_method": stripe_payment_method_id,
            },
        )
    except stripe.StripeError as ex:
        logger.error(str(ex))
        return HttpResponse("Could not change default card.", status=503)

    messages.success(request, "default card updated")
    return redirect("billing_overview")


class BillingCancel(LoginRequiredMixin, View):
    """View that cancels a user subscription on Stripe."""

    template_name = "main/billing_subscription_cancel.html"
    success_url = reverse_lazy("billing_overview")
    success_message = "subscription will be canceled at period end"

    def post(self, request):
        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        try:
            # cancel at period end to keep access for the remainder of the paid period
            stripe.Subscription.modify(subscription["id"], cancel_at_period_end=True)
        except stripe.StripeError as ex:
            logger.error(str(ex))
            return HttpResponse("Subscription could not be canceled.", status=503)
        mail_admins(
            f"Cancellation premium subscriber: {request.user.username}",
            f"{request.user.blog_absolute_url}\n",
        )
        messages.success(request, self.success_message)
        return HttpResponseRedirect(self.success_url)

    def get(self, request):
        return render(request, self.template_name)

    def dispatch(self, request, *args, **kwargs):
        # redirect unauthenticated users to login
        if not request.user.is_authenticated:
            return redirect("login")

        # redirect grandfathered users to dashboard
        if request.user.is_grandfathered:
            return redirect("dashboard")

        # if user has no customer id, redirect to billing_overview to have it generated
        if not request.user.stripe_customer_id:
            return redirect("billing_overview")

        # if user is not premium, redirect
        if not request.user.is_premium:
            return redirect("billing_overview")

        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        if not subscription:
            return redirect("billing_overview")

        return super().dispatch(request, *args, **kwargs)


class BillingResume(LoginRequiredMixin, View):
    """View that resumes a canceled user subscription on Stripe."""

    template_name = "main/billing_subscription_resume.html"
    success_url = reverse_lazy("billing_overview")
    success_message = "subscription resumed"

    def post(self, request, *args, **kwargs):
        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        try:
            stripe.Subscription.modify(subscription["id"], cancel_at_period_end=False)
        except stripe.StripeError as ex:
            logger.error(str(ex))
            return HttpResponse("Subscription could not be resumed.", status=503)
        messages.success(request, self.success_message)
        return HttpResponseRedirect(self.success_url)

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)

    def dispatch(self, request, *args, **kwargs):
        # redirect grandfathered users to dashboard
        if request.user.is_grandfathered:
            return redirect("dashboard")

        # if user has no customer id, redirect to billing_overview
        if not request.user.stripe_customer_id:
            return redirect("billing_overview")

        # if user is not premium, redirect
        if not request.user.is_premium:
            return redirect("billing_overview")

        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        if not subscription:
            return redirect("billing_overview")

        # only allow resuming if subscription is set to cancel
        if not subscription.get("cancel_at_period_end"):
            return redirect("billing_overview")

        return super().dispatch(request, *args, **kwargs)


class BillingResubscribe(LoginRequiredMixin, View):
    """
    View that creates a new subscription for returning users with saved payment methods.
    Charges immediately using the default payment method.
    """

    template_name = "main/billing_resubscribe.html"
    success_url = reverse_lazy("billing_overview")
    success_message = "premium subscription enabled"

    def get(self, request):
        stripe.api_key = settings.STRIPE_API_KEY

        payment_methods = _get_payment_methods(request.user.stripe_customer_id)

        # make a card default if not already
        has_default = any(pm.get("is_default") for pm in payment_methods.values())
        if payment_methods and not has_default:
            first_pm = next(iter(payment_methods.values()))
            try:
                stripe.Customer.modify(
                    request.user.stripe_customer_id,
                    invoice_settings={"default_payment_method": first_pm["id"]},
                )
                payment_methods[first_pm["id"]]["is_default"] = True
            except Exception as e:
                logger.error(f"Unable to set default payment method: {e}")

        default_card = None
        for pm in payment_methods.values():
            if pm["is_default"]:
                default_card = pm
                break

        context = {
            "default_card": default_card,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        stripe.api_key = settings.STRIPE_API_KEY

        try:
            # create subscription with immediate charge using saved payment method
            stripe_subscription = stripe.Subscription.create(
                customer=request.user.stripe_customer_id,
                items=[
                    {
                        "price": settings.STRIPE_PRICE_ID,
                    }
                ],
                payment_behavior="error_if_incomplete",
                payment_settings={"save_default_payment_method": "on_subscription"},
                expand=["latest_invoice.payment_intent"],
            )

            request.user.stripe_subscription_id = stripe_subscription.get("id")
            request.user.save()

            # check if payment succeeded immediately
            latest_invoice = stripe_subscription.get("latest_invoice")
            if latest_invoice:
                payment_intent = latest_invoice.get("payment_intent")
                if payment_intent and payment_intent.get("status") == "succeeded":
                    if not request.user.is_premium:
                        request.user.is_premium = True
                        request.user.is_approved = True
                        request.user.save()
                        if request.user.blog_absolute_url == request.user.blog_url:
                            blog_info = request.user.blog_absolute_url
                        else:
                            blog_info = f"{request.user.blog_absolute_url}\n\n{request.user.blog_url}"
                        mail_admins(
                            f"New premium resubscriber: {request.user.username}",
                            blog_info,
                        )
                    messages.success(request, self.success_message)
                else:
                    messages.info(request, "payment is processing")
            else:
                messages.info(request, "payment processing")

        except stripe.StripeError as ex:
            logger.error("Failed to create resubscription: %s", str(ex))
            messages.error(
                request,
                "failed to create subscription; please try again or contact support",
            )

        return redirect(self.success_url)

    def dispatch(self, request, *args, **kwargs):
        # redirect grandfathered users
        if request.user.is_grandfathered:
            return redirect("billing_overview")

        # redirect if already premium
        if request.user.is_premium:
            return redirect("billing_overview")

        # ensure customer exists
        if not request.user.stripe_customer_id:
            return redirect("billing_overview")

        stripe.api_key = settings.STRIPE_API_KEY

        # check if user has payment methods
        payment_methods = _get_payment_methods(request.user.stripe_customer_id)
        if not payment_methods:
            # no saved payment methods, redirect to regular subscribe flow
            return redirect("billing_subscribe")

        return super().dispatch(request, *args, **kwargs)


@login_required
def billing_welcome(request):
    """
    View that Stripe returns to if subscription initialisation is successful.
    Adds a message alert and redirects to billing_overview.
    """
    payment_intent = request.GET.get("payment_intent")
    if not payment_intent:
        return redirect("billing_overview")

    stripe.api_key = settings.STRIPE_API_KEY
    stripe_intent = stripe.PaymentIntent.retrieve(payment_intent)

    if stripe_intent["status"] == "succeeded":
        # charge succeeded during client-side confirmation flow
        # enable premium if not already enabled via webhook
        if not request.user.is_premium:
            request.user.is_premium = True
            request.user.is_approved = True
            request.user.save()
            if request.user.blog_absolute_url == request.user.blog_url:
                blog_info = request.user.blog_absolute_url
            else:
                blog_info = (
                    f"{request.user.blog_absolute_url}\n\n{request.user.blog_url}"
                )
            mail_admins(
                f"New premium subscriber from welcome page: {request.user.username}",
                blog_info,
            )
        messages.success(request, "premium subscription enabled")
    elif stripe_intent["status"] == "processing":
        messages.info(request, "payment is currently processing")
    else:
        messages.error(
            request,
            "something is wrong. don't sweat it, worst case you get premium for free",
        )

    return redirect("billing_overview")


@login_required
def billing_card_confirm(request):
    setup_intent = request.GET.get("setup_intent")
    if not setup_intent:
        return redirect("billing_overview")

    stripe.api_key = settings.STRIPE_API_KEY
    stripe_intent = stripe.SetupIntent.retrieve(setup_intent)

    if stripe_intent["status"] == "succeeded":
        messages.success(request, "payment method added")
    elif stripe_intent["status"] == "processing":
        messages.info(request, "payment method addition processing")
    elif stripe_intent["status"] == "requires_payment_method":
        messages.info(request, "error setting up payment method :(")
    else:
        messages.error(
            request,
            "something is wrong. don't sweat it, worst case you get premium for free",
        )

    return redirect("billing_overview")


@csrf_exempt
def billing_stripe_webhook(request):
    """
    Handle Stripe webhooks.
    See: https://stripe.com/docs/webhooks
    """

    # ensure only POST requests are allowed
    if request.method != "POST":
        return HttpResponse(status=405)

    # get Stripe settings
    stripe.api_key = settings.STRIPE_API_KEY
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    try:
        # parse event from Stripe
        if webhook_secret:
            # verify webhook signature
            sig_header = request.headers.get("Stripe-Signature", "")
            event = stripe.Webhook.construct_event(
                payload=request.body,
                sig_header=sig_header,
                secret=webhook_secret,
            )
        else:
            # Development mode: skip signature verification
            data = json.loads(request.body.decode("utf-8"))
            event = stripe.Event.construct_from(data, stripe.api_key)

    except (ValueError, stripe.SignatureVerificationError) as ex:
        # invalid payload or signature
        logger.error(f"Webhook validation failed: {type(ex).__name__}: {str(ex)}")
        return HttpResponse(status=400)
    except Exception as ex:
        logger.error(f"Webhook processing error: {str(ex)}")
        return HttpResponse(status=500)

    # process webhook event types
    try:
        if event.type == "invoice.payment_succeeded":
            invoice = event.data.object
            customer_id = getattr(invoice, "customer", None)
            if customer_id:
                customer_id_str = (
                    customer_id.id if hasattr(customer_id, "id") else str(customer_id)
                )

                try:
                    user = models.User.objects.get(stripe_customer_id=customer_id_str)
                    if not user.is_premium:
                        user.is_premium = True
                        user.is_approved = True
                        user.save()
                        if user.blog_absolute_url == user.blog_url:
                            blog_info = user.blog_absolute_url
                        else:
                            blog_info = f"{user.blog_absolute_url}\n\n{user.blog_url}"
                        mail_admins(
                            f"New premium subscriber from webhook: {user.username}",
                            blog_info,
                        )
                except models.User.DoesNotExist:
                    logger.warning(
                        f"Webhook: user not found for customer_id={customer_id_str}"
                    )

        elif event.type == "customer.subscription.deleted":
            subscription = event.data.object
            customer_id = getattr(subscription, "customer", None)
            if customer_id:
                customer_id_str = (
                    customer_id.id if hasattr(customer_id, "id") else str(customer_id)
                )

                try:
                    user = models.User.objects.get(stripe_customer_id=customer_id_str)
                    if user.is_premium:
                        user.is_premium = False
                        user.stripe_subscription_id = None
                        user.save()
                except models.User.DoesNotExist:
                    logger.warning(
                        f"Webhook: user not found for customer_id={customer_id_str}"
                    )

        elif event.type == "payment_method.attached":
            payment_method = event.data.object
            customer_id = getattr(payment_method, "customer", None)
            if customer_id:
                customer_id_str = (
                    customer_id.id if hasattr(customer_id, "id") else str(customer_id)
                )

                try:
                    # set payment method as default if customer has no default one yet
                    customer = stripe.Customer.retrieve(customer_id_str)
                    if not customer.invoice_settings.default_payment_method:
                        stripe.Customer.modify(
                            customer_id_str,
                            invoice_settings={
                                "default_payment_method": payment_method.id,
                            },
                        )
                        logger.info(
                            f"Set payment method {payment_method.id} as default for customer {customer_id_str}"
                        )
                except stripe.StripeError as ex:
                    logger.error(
                        f"Failed to set default payment method for customer {customer_id_str}: {str(ex)}"
                    )

    except Exception as ex:
        logger.error(f"Webhook event processing error: {str(ex)}")

    return HttpResponse(status=200)
