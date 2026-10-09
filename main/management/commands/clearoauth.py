"""Remove expired OAuth families, retaining rotated hashes while renewal is possible."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from main.models import OAuthGrant


class Command(BaseCommand):
    help = "Delete OAuth grants whose code and all refresh tokens have expired."

    def handle(self, *args, **options):
        now = timezone.now()
        count = 0
        candidates = (
            OAuthGrant.objects.filter(code_expires__lte=now)
            .exclude(tokens__refresh_expires__gt=now)
            .values_list("pk", flat=True)
        )
        for pk in candidates.iterator():
            with transaction.atomic():
                grant = OAuthGrant.objects.select_for_update().filter(pk=pk).first()
                # Renewal may have extended this family since the initial query.
                if grant and not grant.tokens.filter(refresh_expires__gt=now).exists():
                    removed, _ = grant.delete()
                    count += removed
        self.stdout.write(f"Deleted {count} expired OAuth records.")
