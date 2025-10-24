import json
import logging
from datetime import datetime

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import mail_admins
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.edit import FormView

from main import forms, models, util

logger = logging.getLogger(__name__)


def _safe_get(obj, key, default=None):
    """Safely get attribute or dict key from Stripe objects or dicts."""
    if obj is None:
        return default
    try:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default


def _create_setup_intent(customer_id):
    stripe.api_key = settings.STRIPE_API_KEY

    try:
        stripe_setup_intent = stripe.SetupIntent.create(
            automatic_payment_methods={"enabled": True},
            customer=customer_id,
        )
    except stripe.error.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to create setup intent on Stripe.") from ex

    return {
        "stripe_client_secret": stripe_setup_intent["client_secret"],
    }


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
            expand=["latest_invoice.payment_intent"],
        )
        logger.info(f"Created subscription: {stripe_subscription.get('id')}")
    except stripe.error.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to create subscription on Stripe.") from ex

    client_secret = None
    latest_invoice = _safe_get(stripe_subscription, "latest_invoice")
    logger.info(f"Latest invoice: {latest_invoice}")

    try:
        if latest_invoice:
            # If latest_invoice is expanded, try to read client_secret directly
            payment_intent = _safe_get(latest_invoice, "payment_intent")
            logger.info(f"Payment intent from invoice: {payment_intent}")
            if payment_intent:
                # Payment intent may be an object or an ID
                client_secret = _safe_get(payment_intent, "client_secret")
                logger.info(f"Client secret from payment intent: {client_secret}")
                if not client_secret:
                    pi_id = _safe_get(payment_intent, "id") or (
                        payment_intent if isinstance(payment_intent, str) else None
                    )
                    logger.info(f"Payment intent ID: {pi_id}")
                    if pi_id:
                        pi = stripe.PaymentIntent.retrieve(pi_id)
                        client_secret = _safe_get(pi, "client_secret")
                        logger.info(f"Client secret after retrieval: {client_secret}")
            else:
                # latest_invoice may be an ID; retrieve with expand
                inv_id = _safe_get(latest_invoice, "id") or (
                    latest_invoice if isinstance(latest_invoice, str) else None
                )
                if inv_id:
                    inv = stripe.Invoice.retrieve(inv_id, expand=["payment_intent"])
                    pi = _safe_get(inv, "payment_intent")
                    if pi:
                        client_secret = _safe_get(pi, "client_secret")
                        if not client_secret:
                            pi_id = _safe_get(pi, "id") or (
                                pi if isinstance(pi, str) else None
                            )
                            if pi_id:
                                pi_obj = stripe.PaymentIntent.retrieve(pi_id)
                                client_secret = _safe_get(pi_obj, "client_secret")
        # As a final fallback, refetch the subscription expanded
        if not client_secret:
            sub = stripe.Subscription.retrieve(
                _safe_get(stripe_subscription, "id"),
                expand=["latest_invoice.payment_intent"],
            )
            li = _safe_get(sub, "latest_invoice")
            pi = _safe_get(li, "payment_intent") if li else None
            if pi:
                client_secret = _safe_get(pi, "client_secret")
                if not client_secret:
                    pi_id = _safe_get(pi, "id") or (pi if isinstance(pi, str) else None)
                    if pi_id:
                        pi_obj = stripe.PaymentIntent.retrieve(pi_id)
                        client_secret = _safe_get(pi_obj, "client_secret")
            else:
                # No payment intent exists, create one for the invoice
                logger.info("No payment intent found, creating one for the invoice")
                if li:
                    invoice_id = _safe_get(li, "id")
                    if invoice_id:
                        try:
                            pi_obj = stripe.PaymentIntent.create(
                                amount=_safe_get(li, "amount_due"),
                                currency=_safe_get(li, "currency", "usd"),
                                customer=_safe_get(stripe_subscription, "customer"),
                                automatic_payment_methods={"enabled": True},
                                metadata={"invoice_id": invoice_id},
                            )
                            client_secret = _safe_get(pi_obj, "client_secret")
                            logger.info(f"Created payment intent: {_safe_get(pi_obj, 'id')}, client_secret: {'present' if client_secret else 'missing'}")
                        except stripe.error.StripeError as pi_ex:
                            logger.error(f"Failed to create payment intent for invoice {invoice_id}: {str(pi_ex)}")
    except stripe.error.StripeError as ex:
        logger.error(
            "Failed to retrieve payment_intent for subscription %s: %s",
            _safe_get(stripe_subscription, "id"),
            str(ex),
        )

    logger.info(f"Returning subscription data - ID: {_safe_get(stripe_subscription, 'id')}, client_secret: {'present' if client_secret else 'missing'}")
    return {
        "stripe_subscription_id": _safe_get(stripe_subscription, "id"),
        "stripe_client_secret": client_secret,
    }


def _get_stripe_subscription(stripe_subscription_id):
    stripe.api_key = settings.STRIPE_API_KEY

    try:
        stripe_subscription = stripe.Subscription.retrieve(stripe_subscription_id)
    except stripe.error.InvalidRequestError as ex:
        # Subscription doesn't exist
        logger.warning("Subscription %s not found: %s", stripe_subscription_id, str(ex))
        return None
    except stripe.error.StripeError as ex:
        logger.error("Failed to get subscription %s from Stripe: %s", stripe_subscription_id, str(ex))
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
    except stripe.error.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to retrieve customer data from Stripe.") from ex

    # get payment methods
    try:
        stripe_payment_methods = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type="card",
        )
    except stripe.error.StripeError as ex:
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
    except stripe.error.StripeError as ex:
        logger.error(str(ex))
        raise Exception("Failed to retrieve invoices data from Stripe.") from ex

    # normalise invoices objects
    invoice_list = []
    for stripe_inv in stripe_invoices.data:
        invoice_list.append(
            {
                "id": stripe_inv.id,
                "url": stripe_inv.hosted_invoice_url,
                "pdf": stripe_inv.invoice_pdf,
                "period_start": datetime.fromtimestamp(stripe_inv.period_start),
                "period_end": datetime.fromtimestamp(stripe_inv.period_end),
                "created": datetime.fromtimestamp(stripe_inv.created),
            }
        )

    return invoice_list


@login_required
def billing_index(request):
    """
    View method that shows the billing index, a summary of subscription and
    payment methods.
    """
    # respond for grandfathered users first
    if request.user.is_grandfathered:
        return render(
            request,
            "main/billing_index.html",
            {
                "is_grandfathered": True,
            },
        )

    # respond for monero case
    if request.user.monero_address:
        return render(request, "main/billing_index.html")

    stripe.api_key = settings.STRIPE_API_KEY

    # create stripe customer for user if it does not exist
    if not request.user.stripe_customer_id:
        try:
            stripe_response = stripe.Customer.create()
        except stripe.error.StripeError as ex:
            logger.error(str(ex))
            raise Exception("Failed to create customer on Stripe.") from ex
        request.user.stripe_customer_id = stripe_response["id"]
        request.user.save()

    # get subscription if exists
    current_period_start = None
    current_period_end = None
    if request.user.stripe_subscription_id:
        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        # Only access period fields if subscription is active and has these fields
        if subscription and subscription.get("status") == "active":
            if subscription.get("current_period_start"):
                current_period_start = datetime.utcfromtimestamp(
                    subscription["current_period_start"]
                )
            if subscription.get("current_period_end"):
                current_period_end = datetime.utcfromtimestamp(
                    subscription["current_period_end"]
                )

    # transform into list of values
    payment_methods = _get_payment_methods(request.user.stripe_customer_id).values()

    return render(
        request,
        "main/billing_index.html",
        {
            "stripe_customer_id": request.user.stripe_customer_id,
            "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
            "stripe_price_id": settings.STRIPE_PRICE_ID,
            "current_period_end": current_period_end,
            "current_period_start": current_period_start,
            "payment_methods": payment_methods,
            "invoice_list": _get_invoices(request.user.stripe_customer_id),
        },
    )


class BillingSubscribe(LoginRequiredMixin, FormView):
    form_class = forms.StripeForm
    template_name = "main/billing_subscribe.html"
    success_url = reverse_lazy("billing_index")
    success_message = "payment is processing; premium will be enabled once the charge succeeds"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stripe_public_key"] = settings.STRIPE_PUBLIC_KEY
        return context

    def get(self, request, *args, **kwargs):
        stripe.api_key = settings.STRIPE_API_KEY

        # Ensure customer exists
        if not request.user.stripe_customer_id:
            try:
                created = stripe.Customer.create()
                request.user.stripe_customer_id = _safe_get(created, "id")
                request.user.save()
            except stripe.error.StripeError as ex:
                logger.error("Failed creating customer before subscribe: %s", str(ex))
                messages.error(request, "payment processor unavailable; please try again later")
                return redirect("billing_index")

        data = _create_stripe_subscription(request.user.stripe_customer_id)
        request.user.stripe_subscription_id = data["stripe_subscription_id"]
        request.user.save()

        url = f"{util.get_protocol()}//{settings.CANONICAL_HOST}"
        url += reverse_lazy("billing_welcome")

        context = self.get_context_data()
        if not data.get("stripe_client_secret"):
            # Retry fetching client secret once by refetching invoice->payment_intent
            try:
                sub = stripe.Subscription.retrieve(
                    request.user.stripe_subscription_id,
                    expand=["latest_invoice.payment_intent"],
                )
                latest_invoice = _safe_get(sub, "latest_invoice")
                pi = _safe_get(latest_invoice, "payment_intent") if latest_invoice else None
                client_secret_retry = _safe_get(pi, "client_secret") if pi else None
            except stripe.error.StripeError as ex:
                logger.error("Retry to fetch payment_intent failed: %s", str(ex))
                client_secret_retry = None

            if client_secret_retry:
                data["stripe_client_secret"] = client_secret_retry

        if not data.get("stripe_client_secret"):
            messages.error(
                request,
                "payment could not be initialized; please try again or use a different card",
            )
            return redirect("billing_index")
        context["stripe_client_secret"] = data.get("stripe_client_secret")
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


class BillingCard(LoginRequiredMixin, FormView):
    form_class = forms.StripeForm
    template_name = "main/billing_card.html"
    success_url = reverse_lazy("billing_index")
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

        url = f"{util.get_protocol()}//{settings.CANONICAL_HOST}"
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


class BillingCardDelete(LoginRequiredMixin, View):
    """View that deletes a card from a user on Stripe."""

    template_name = "main/billing_card_confirm_delete.html"
    success_url = reverse_lazy("billing_index")
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
        except stripe.error.StripeError as ex:
            logger.error(str(ex))
            messages.error(request, "payment processor unresponsive; please try again")
            return redirect(reverse_lazy("billing_index"))

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


@login_required
def billing_card_default(request, stripe_payment_method_id):
    """View method that changes the default card of a user on Stripe."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

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
    except stripe.error.StripeError as ex:
        logger.error(str(ex))
        return HttpResponse("Could not change default card.", status=503)

    messages.success(request, "default card updated")
    return redirect("billing_index")


class BillingCancel(LoginRequiredMixin, View):
    """View that cancels a user subscription on Stripe."""

    template_name = "main/billing_subscription_cancel.html"
    success_url = reverse_lazy("billing_index")
    success_message = "premium subscription canceled"

    def post(self, request, *args, **kwargs):
        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        try:
            stripe.Subscription.delete(subscription["id"])
        except stripe.error.StripeError as ex:
            logger.error(str(ex))
            return HttpResponse("Subscription could not be canceled.", status=503)
        request.user.is_premium = False
        request.user.stripe_subscription_id = None
        request.user.save()
        mail_admins(
            f"Cancellation premium subscriber: {request.user.username}",
            f"{request.user.blog_absolute_url}\n",
        )
        messages.success(request, self.success_message)
        return HttpResponseRedirect(self.success_url)

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)

    def dispatch(self, request, *args, **kwargs):
        # redirect grandfathered users to dashboard
        if request.user.is_grandfathered:
            return redirect("dashboard")

        # if user has no customer id, redirect to billing_index to have it generated
        if not request.user.stripe_customer_id:
            return redirect("billing_index")

        # if user is not premium, redirect
        if not request.user.is_premium:
            return redirect("billing_index")

        subscription = _get_stripe_subscription(request.user.stripe_subscription_id)
        if not subscription:
            return redirect("billing_index")

        return super().dispatch(request, *args, **kwargs)


@login_required
def billing_subscription(request):
    """
    View that creates a new subscription for user on Stripe,
    given they already have a card registered.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    # redirect grandfathered users to dashboard
    if request.user.is_grandfathered:
        return redirect("dashboard")

    data = _create_stripe_subscription(request.user.stripe_customer_id)
    request.user.stripe_subscription_id = data["stripe_subscription_id"]
    request.user.save()

    # Do NOT enable premium here. Wait for payment confirmation via webhook.
    messages.info(
        request,
        "payment is currently processing; premium will be enabled once the charge succeeds",
    )
    return redirect("billing_index")


@login_required
def billing_welcome(request):
    """
    View that Stripe returns to if subscription initialisation is successful.
    Adds a message alert and redirects to billing_index.
    """
    payment_intent = request.GET.get("payment_intent")

    stripe.api_key = settings.STRIPE_API_KEY
    stripe_intent = stripe.PaymentIntent.retrieve(payment_intent)

    if stripe_intent["status"] == "succeeded":
        # Charge succeeded during client-side confirmation flow. Enable premium if not already enabled via webhook.
        if not request.user.is_premium:
            request.user.is_premium = True
            request.user.is_approved = True
            request.user.save()
            mail_admins(
                f"New premium subscriber: {request.user.username}",
                f"{request.user.blog_absolute_url}\n\n{request.user.blog_url}",
            )
        messages.success(request, "premium subscription enabled")
    elif stripe_intent["status"] == "processing":
        messages.info(request, "payment is currently processing")
    else:
        messages.error(
            request,
            "something is wrong. don't sweat it, worst case you get premium for free",
        )

    return redirect("billing_index")


@login_required
def billing_card_confirm(request):
    setup_intent = request.GET.get("setup_intent")

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

    return redirect("billing_index")


@csrf_exempt
def billing_stripe_webhook(request):
    """
    Handle Stripe webhooks.
    See: https://stripe.com/docs/webhooks
    """
    
    # Ensure only POST requests are allowed
    if request.method != "POST":
        return HttpResponse(status=405)
    
    # Get Stripe settings
    stripe.api_key = settings.STRIPE_API_KEY
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    
    try:
        # Try to parse the event from Stripe
        if webhook_secret:
            # Verify webhook signature
            sig_header = request.headers.get("Stripe-Signature", "")
            event = stripe.Webhook.construct_event(
                payload=request.body,
                sig_header=sig_header,
                secret=webhook_secret,
            )
        else:
            # Development mode - skip signature verification
            data = json.loads(request.body.decode("utf-8"))
            event = stripe.Event.construct_from(data, stripe.api_key)
            
    except (ValueError, stripe.error.SignatureVerificationError) as ex:
        # Invalid payload or signature
        logger.error(f"Webhook validation failed: {str(ex)}")
        return HttpResponse(status=400)
    except Exception as ex:
        # Unexpected error
        logger.error(f"Webhook processing error: {str(ex)}")
        return HttpResponse(status=500)

    # Process different webhook event types
    try:
        if event.type == "invoice.payment_succeeded":
            # Handle successful invoice payment (initial or renewals)
            invoice = event.data.object
            customer_id = getattr(invoice, "customer", None)
            if customer_id:
                # Extract customer ID string
                customer_id_str = customer_id.id if hasattr(customer_id, "id") else str(customer_id)
                
                try:
                    user = models.User.objects.get(stripe_customer_id=customer_id_str)
                    # Mark user as premium and approved
                    if not user.is_premium:
                        user.is_premium = True
                        user.is_approved = True
                        user.save()
                        mail_admins(
                            f"New premium subscriber (webhook): {user.username}",
                            f"{user.blog_absolute_url}\n\n{user.blog_url}",
                        )
                except models.User.DoesNotExist:
                    logger.warning(f"Webhook: user not found for customer_id={customer_id_str}")

        elif event.type == "customer.subscription.deleted":
            # Handle subscription cancellations initiated from Stripe
            subscription = event.data.object
            customer_id = getattr(subscription, "customer", None)
            if customer_id:
                customer_id_str = customer_id.id if hasattr(customer_id, "id") else str(customer_id)
                
                try:
                    user = models.User.objects.get(stripe_customer_id=customer_id_str)
                    if user.is_premium:
                        user.is_premium = False
                        user.stripe_subscription_id = None
                        user.save()
                except models.User.DoesNotExist:
                    logger.warning(f"Webhook: user not found for customer_id={customer_id_str}")

    except Exception as ex:
        # Log processing errors but still return 200 to avoid webhook retries
        logger.error(f"Webhook event processing error: {str(ex)}")

    return HttpResponse(status=200)
