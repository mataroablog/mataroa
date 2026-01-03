import logging
from datetime import UTC, datetime, timedelta

import stripe
from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand

from main import models, scheme

logger = logging.getLogger(__name__)


def get_email_body(renewal_date):
    billing_url = f"{scheme.get_protocol()}//{settings.CANONICAL_HOST}/billing/"
    formatted_date = renewal_date.strftime("%B %d, %Y")

    body = f"""Hello,

This is a reminder that your Mataroa premium subscription will
automatically renew on {formatted_date}.

If you wish to manage your subscription or update your payment
method, you can do so here:
{billing_url}

If you have any questions, reply to this email.
"""
    return body


class Command(BaseCommand):
    help = "Send email reminders to premium subscribers whose subscription renews in 7 days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-dryrun",
            action="store_false",
            dest="dryrun",
            help="No dry run. Send actual emails.",
        )
        parser.set_defaults(dryrun=True)

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Processing renewal reminders."))

        stripe.api_key = settings.STRIPE_API_KEY

        # Target date is 7 days from now
        target_date = (datetime.now(UTC) + timedelta(days=7)).date()
        self.stdout.write(
            self.style.NOTICE(f"Looking for subscriptions renewing on {target_date}.")
        )

        # Get all premium users with a Stripe subscription
        users = models.User.objects.filter(
            is_premium=True,
            stripe_subscription_id__isnull=False,
        ).exclude(stripe_subscription_id="")

        self.stdout.write(
            self.style.NOTICE(
                f"Found {users.count()} premium users with subscriptions."
            )
        )

        count_sent = 0
        count_skipped_no_email = 0
        count_skipped_no_renewal = 0
        count_errors = 0

        for user in users:
            # Skip users without email
            if not user.email:
                count_skipped_no_email += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping {user.username}: no email address configured."
                    )
                )
                continue

            # Retrieve subscription from Stripe
            try:
                subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)
            except stripe.InvalidRequestError as ex:
                count_errors += 1
                logger.warning(
                    "Subscription %s not found for user %s: %s",
                    user.stripe_subscription_id,
                    user.username,
                    str(ex),
                )
                continue
            except stripe.StripeError as ex:
                count_errors += 1
                logger.error(
                    "Failed to retrieve subscription %s for user %s: %s",
                    user.stripe_subscription_id,
                    user.username,
                    str(ex),
                )
                continue

            # Skip if subscription is not active
            if subscription.status not in ("active", "trialing"):
                continue

            # Get renewal date from subscription items
            items = subscription.get("items") or {}
            item_data = items.get("data") or []
            if not item_data:
                continue

            first_item = item_data[0]
            current_period_end = first_item.get("current_period_end")
            if not current_period_end:
                continue

            renewal_date = datetime.fromtimestamp(current_period_end, tz=UTC).date()

            # Check if renewal is exactly 7 days away
            if renewal_date != target_date:
                count_skipped_no_renewal += 1
                continue

            # Prepare and send email
            if options["dryrun"]:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Would send renewal reminder to {user.username} ({user.email}) "
                        f"for renewal on {renewal_date}."
                    )
                )
                continue

            subject = f"Your Mataroa premium subscription renews on {renewal_date.strftime('%d %B %Y')}"
            body = get_email_body(renewal_date)

            email = mail.EmailMessage(
                subject=subject,
                body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )

            try:
                connection = mail.get_connection()
                connection.send_messages([email])
                count_sent += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Renewal reminder sent to {user.username} ({user.email})."
                    )
                )
            except Exception as ex:
                count_errors += 1
                logger.error(
                    "Failed to send renewal reminder to %s: %s",
                    user.username,
                    str(ex),
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"Failed to send reminder to {user.username}: {str(ex)}"
                    )
                )

        # Summary
        self.stdout.write(self.style.NOTICE("---"))
        self.stdout.write(
            self.style.NOTICE(f"Skipped (no email): {count_skipped_no_email}")
        )
        self.stdout.write(
            self.style.NOTICE(
                f"Skipped (not renewing in 7 days): {count_skipped_no_renewal}"
            )
        )
        self.stdout.write(self.style.NOTICE(f"Errors: {count_errors}"))

        if options["dryrun"]:
            self.stdout.write(
                self.style.SUCCESS("Dry run complete. No emails were sent.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Renewal reminders sent: {count_sent}")
            )
