"""Filters for the bulk selection page, over saved classifier findings."""

from datetime import date, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from main import (
    blog_classification,
    gambling_overview,
    models,
    moderation_campaigns,
    moderation_deletion,
    promotional_spam,
)

PAGE_SIZE = 500
ACTIVE_DAYS = 7
# Categories that place a finding in "Needs review" (see recommendation_sets).
FLAGGED = [
    "pornography",
    "gambling",
    "promotional",
    "gallery_promotion",
    "support_impersonation",
]


def strong_keys():
    """Campaign keys with enough corroborating strong blogs for group deletion."""
    return list(
        models.PromotionalSpamFinding.objects.filter(
            rule_version=promotional_spam.VERSION,
            campaign_strong=True,
            dismissed_at__isnull=True,
        )
        .exclude(campaign_key="")
        .values("campaign_key")
        .annotate(n=Count("pk"))
        .filter(n__gte=moderation_campaigns.MIN_GROUP_SIZE)
        .values_list("campaign_key", flat=True)
    )


def protected_owners():
    return models.User.objects.filter(moderation_deletion.PROTECTED)


def strong_q(keys):
    return Q(campaign_strong=True, dismissed_at__isnull=True, campaign_key__in=keys)


def flagged_q():
    flagged = models.PromotionalSpamGroup.objects.filter(
        kind="category", key__in=FLAGGED
    ).values("finding_id")
    return Q(dismissed_at__isnull=True, pk__in=flagged)


def parse_date(value):
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def filters(params):
    """Normalized filter values; anything invalid falls back to its default."""
    category = params.get("category", "all")
    if category != "all" and category not in blog_classification.CATEGORIES:
        category = "all"
    recommendation = params.get("recommendation", "all")
    if recommendation not in moderation_campaigns.RECOMMENDATIONS:
        recommendation = "all"
    status = params.get("status", "pending")
    if status not in ("pending", "dismissed", "all"):
        status = "pending"
    joined_from = parse_date(params.get("joined_from"))
    joined_to = parse_date(params.get("joined_to"))
    return {
        "category": category,
        "recommendation": recommendation,
        "status": status,
        "term": gambling_overview.clean_term(params.get("term")),
        "joined_from": joined_from.isoformat() if joined_from else "",
        "joined_to": joined_to.isoformat() if joined_to else "",
        "active": params.get("active") == "1",
    }


def findings(values, keys=None):
    """Current findings matching the normalized filter values."""
    qs = models.PromotionalSpamFinding.objects.filter(
        rule_version=promotional_spam.VERSION
    )
    if values["status"] != "all":
        qs = qs.filter(dismissed_at__isnull=values["status"] == "pending")
    if values["category"] != "all":
        qs = qs.filter(
            pk__in=models.PromotionalSpamGroup.objects.filter(
                kind="category", key=values["category"]
            ).values("finding_id")
        )
    if values["term"]:
        domains = gambling_overview.matching_domains(values["term"], qs)
        qs = qs.filter(
            pk__in=models.PromotionalSpamGroup.objects.filter(
                kind="domain", key__in=[row["key"] for row in domains]
            ).values("finding_id")
        )
    if values["joined_from"]:
        qs = qs.filter(owner__date_joined__date__gte=values["joined_from"])
    if values["joined_to"]:
        qs = qs.filter(owner__date_joined__date__lte=values["joined_to"])
    if values["active"]:
        since = timezone.now() - timedelta(days=ACTIVE_DAYS)
        qs = qs.filter(
            owner_id__in=models.Post.objects.filter(created_at__gte=since).values(
                "owner_id"
            )
        )
    recommendation = values["recommendation"]
    if recommendation == "all":
        return qs
    protected = protected_owners()
    if recommendation == "protected":
        return qs.filter(owner__in=protected)
    keys = strong_keys() if keys is None else keys
    qs = qs.exclude(owner__in=protected)
    if recommendation == "strong":
        return qs.filter(strong_q(keys))
    qs = qs.exclude(strong_q(keys))
    if recommendation == "review":
        return qs.filter(flagged_q())
    return qs.exclude(flagged_q())


def rows(page, keys):
    """Display rows for one page of findings (with owners selected)."""
    ids = [finding.pk for finding in page]
    flagged = set(
        models.PromotionalSpamGroup.objects.filter(
            finding_id__in=ids, kind="category", key__in=FLAGGED
        ).values_list("finding_id", flat=True)
    )
    keys = set(keys)
    result = []
    for finding in page:
        owner = finding.owner
        protection = owner.moderation_protection
        if protection:
            recommendation = "protected"
        elif (
            finding.campaign_strong
            and not finding.dismissed_at
            and finding.campaign_key in keys
        ):
            recommendation = "strong"
        elif not finding.dismissed_at and finding.pk in flagged:
            recommendation = "review"
        else:
            recommendation = "none"
        evidence = finding.evidence
        domains = evidence.get("domains") or []
        result.append(
            {
                "owner_id": owner.pk,
                "username": owner.username,
                "blog_url": owner.blog_absolute_url,
                "joined": owner.date_joined,
                "posts": evidence.get("posts", 0),
                "pages": evidence.get("pages", 0),
                "categories": [
                    blog_classification.CATEGORIES.get(key, key)
                    for key in evidence.get("category_reasons", {})
                ],
                "domain": domains[0]["domain"] if domains else "",
                "domain_count": len(domains),
                "recommendation": moderation_campaigns.RECOMMENDATIONS[recommendation],
                "protection": protection,
                "dismissed": bool(finding.dismissed_at),
            }
        )
    return result
