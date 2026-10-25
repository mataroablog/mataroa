"""Explicit, signed deletion selections. Account protection is checked at execution."""

from django.core import signing
from django.db.models import Count, Max, Q

from main import models, promotional_spam

PROTECTED = (
    Q(is_premium=True)
    | Q(is_grandfathered=True)
    | Q(is_approved=True)
    | Q(is_staff=True)
    | Q(is_superuser=True)
)
SALT = "moderation-bulk-delete-v1"
MAX_BATCH = 1000
MAX_AGE = 3600


def counts(owners, stats):
    ids = [owner.pk for owner in owners]
    images = {
        row["owner_id"]: row
        for row in models.Image.objects.filter(owner_id__in=ids)
        .order_by()
        .values("owner_id")
        .annotate(count=Count("pk"), last=Max("pk"))
    }
    comments = {
        row["post__owner_id"]: row
        for row in models.Comment.objects.filter(post__owner_id__in=ids)
        .order_by()
        .values("post__owner_id")
        .annotate(count=Count("pk"), last=Max("pk"))
    }
    return {
        owner.pk: {
            "posts": stats["post"].get(owner.pk, {}).get("count", 0),
            "pages": stats["page"].get(owner.pk, {}).get("count", 0),
            "images": images.get(owner.pk, {}).get("count", 0),
            "comments": comments.get(owner.pk, {}).get("count", 0),
            "last_image": images.get(owner.pk, {}).get("last"),
            "last_comment": comments.get(owner.pk, {}).get("last"),
        }
        for owner in owners
    }


def ticket(user, owner, finding, totals):
    return signing.dumps(
        {
            "reviewer": user.pk,
            "owner": owner.pk,
            "source": finding.source_signature,
            "version": promotional_spam.VERSION,
            "review": review_signature(finding),
            "counts": totals,
        },
        salt=SALT,
        compress=True,
    )


def review_signature(finding):
    return promotional_spam.digest(
        [
            finding.source_signature,
            finding.rule_version,
            finding.dismissed_at,
            finding.evidence.get("category_reasons", {}),
        ]
    )
