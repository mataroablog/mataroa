import stripe
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from main import models


class Command(BaseCommand):
    help = "Check Stripe data is in sync with database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help=(
                "Disable premium and clear the subscription ID for eligible users "
                "without a current Stripe subscription. The default is a dry run."
            ),
        )

    def handle(self, *args, **options):
        stripe.api_key = settings.STRIPE_API_KEY

        # Stripe excludes canceled and incomplete-expired subscriptions by default.
        # Keep access for all other states until Stripe ends the subscription.
        stripe_customer_ids = set()
        total_count = 0
        last = None
        while True:
            if last:
                subscription_list = stripe.Subscription.list(
                    limit=100, starting_after=last.id
                )
            else:
                subscription_list = stripe.Subscription.list(limit=100)
            total_count += len(subscription_list)
            self.stdout.write(f"Stripe subscriptions fetched: {total_count}")

            for subscription in subscription_list:
                stripe_customer_ids.add(subscription.customer)
                if not models.User.objects.filter(
                    stripe_customer_id=subscription.customer
                ).exists():
                    self.stdout.write(
                        self.style.NOTICE(
                            "Stripe subscription without DB user: "
                            f"{subscription.customer}"
                        )
                    )

            if not subscription_list.has_more:
                break
            last = list(reversed(subscription_list))[0]

        # Monero and grandfathered users are not governed by Stripe state.
        premium_users = models.User.objects.filter(is_premium=True)
        exempt_users = premium_users.filter(
            Q(is_grandfathered=True)
            | (Q(monero_address__isnull=False) & ~Q(monero_address=""))
        )
        stripe_premium_users = premium_users.filter(is_grandfathered=False).filter(
            Q(monero_address__isnull=True) | Q(monero_address="")
        )

        self.stdout.write(f"\nPremium users in DB: {premium_users.count()}")
        self.stdout.write(
            f"Premium users excluded from Stripe reconciliation: {exempt_users.count()}"
        )
        self.stdout.write(
            f"Stripe customers with current subscriptions: {len(stripe_customer_ids)}"
        )

        stale_users = []
        for user in stripe_premium_users:
            if user.stripe_customer_id not in stripe_customer_ids:
                stale_users.append(user)
                self.stdout.write(
                    self.style.WARNING(
                        f"Premium user without Stripe subscription: "
                        f"{user.username} (customer_id: {user.stripe_customer_id})"
                    )
                )

        self.stdout.write(
            f"Stripe premium users requiring downgrade: {len(stale_users)}"
        )

        if not options["fix"]:
            self.stdout.write(
                self.style.NOTICE(
                    "Dry run: no users changed. Re-run with --fix to apply downgrades."
                )
            )
            return

        with transaction.atomic():
            for user in stale_users:
                user.is_premium = False
                user.stripe_subscription_id = None
                user.save(update_fields=["is_premium", "stripe_subscription_id"])

        self.stdout.write(
            self.style.SUCCESS(f"Downgraded {len(stale_users)} Stripe premium users.")
        )
