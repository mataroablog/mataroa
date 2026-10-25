"""Explicit, signed deletion selections. Account protection is checked at execution."""

from django.core import signing
from django.db import transaction
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


def snapshot(user, owner, finding, totals):
    return {
        "reviewer": user.pk,
        "owner": owner.pk,
        "source": finding.source_signature,
        "version": promotional_spam.VERSION,
        "review": review_signature(finding),
        "counts": totals,
    }


def ticket(user, owner, finding, totals):
    return signing.dumps(
        snapshot(user, owner, finding, totals), salt=SALT, compress=True
    )


def review_signature(finding):
    return promotional_spam.digest(
        [
            finding.source_signature,
            finding.rule_version,
            finding.dismissed_at,
            finding.evidence.get("category_reasons", {}),
            finding.evidence.get("campaign", {}),
        ]
    )


def delete_snapshot(selection, campaign_key=""):
    """Delete exactly one reviewed account, checking live protection and evidence."""
    if selection["version"] != promotional_spam.VERSION:
        return {"ok": False, "error": "Classification rules changed. Review again."}
    with transaction.atomic():
        owner = (
            models.User.objects.select_for_update()
            .filter(pk=selection["owner"])
            .first()
        )
        if not owner:
            return {"ok": True, "already_deleted": True}
        if owner.moderation_protection:
            return {
                "ok": False,
                "error": "Protected account: " + owner.moderation_protection,
            }
        finding = (
            models.PromotionalSpamFinding.objects.select_for_update()
            .filter(owner=owner)
            .first()
        )
        if (
            not finding
            or review_signature(finding) != selection["review"]
            or (
                campaign_key
                and (
                    finding.campaign_key != campaign_key
                    or not finding.campaign_strong
                    or finding.dismissed_at
                )
            )
        ):
            return {"ok": False, "error": "Classification changed. Review again."}
        owner = promotional_spam.owner_queryset().get(pk=owner.pk)
        stats = promotional_spam.metadata([owner])
        if (
            promotional_spam.source_signature(owner, stats) != selection["source"]
            or counts([owner], stats)[owner.pk] != selection["counts"]
        ):
            return {
                "ok": False,
                "error": "Content changed. Review again after scanning.",
            }
        owner.delete()
    return {"ok": True}
