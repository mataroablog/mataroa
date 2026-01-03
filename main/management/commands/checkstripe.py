import stripe
from django.conf import settings
from django.core.management.base import BaseCommand

from main import models


class Command(BaseCommand):
    help = "Check Stripe data is in sync with database."

    def handle(self, *args, **options):
        stripe.api_key = settings.STRIPE_API_KEY

        # Collect all Stripe customer IDs with active subscriptions
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
            print(f"Stripe subscriptions fetched: {total_count}")

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

        # Check for premium users in DB without Stripe subscription
        premium_users = models.User.objects.filter(is_premium=True)
        print(f"\nPremium users in DB: {premium_users.count()}")
        print(f"Stripe subscriptions: {len(stripe_customer_ids)}")

        for user in premium_users:
            if user.stripe_customer_id not in stripe_customer_ids:
                self.stdout.write(
                    self.style.WARNING(
                        f"Premium user without Stripe subscription: "
                        f"{user.username} (customer_id: {user.stripe_customer_id})"
                    )
                )
