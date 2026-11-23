"""Gambling moderation overview, aggregated from saved classifier findings.

Nothing here scans content or fetches URLs; it reads PromotionalSpamFinding and
PromotionalSpamGroup rows written by the scanner.
"""

import re
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from main import models, moderation_campaigns, moderation_deletion, promotional_spam

CATEGORY = "gambling"
MIN_TERM_LENGTH = 4
TERM = re.compile(r"[a-z0-9.-]+")
# Second-level labels that are not brand names, as in mostbet.web.pk or x.co.cz.
GENERIC_SECOND_LEVEL = {
    "ac",
    "biz",
    "co",
    "com",
    "edu",
    "gov",
    "info",
    "net",
    "org",
    "web",
}


def findings(dismissed=False):
    """Gambling findings from the current rule version."""
    gambling = models.PromotionalSpamGroup.objects.filter(
        kind="category", key=CATEGORY
    ).values("finding_id")
    return models.PromotionalSpamFinding.objects.filter(
        rule_version=promotional_spam.VERSION,
        dismissed_at__isnull=not dismissed,
        pk__in=gambling,
    )


def unprotected(qs):
    return qs.exclude(
        owner__in=models.User.objects.filter(moderation_deletion.PROTECTED)
    )


def brand_key(domain):
    """Approximate brand of a destination: mostbet-deu-de.com → mostbet."""
    labels = [
        label for label in domain.lower().removeprefix("www.").split(".") if label
    ]
    if not labels:
        return ""
    if len(labels) > 1:
        labels = labels[:-1]
    if len(labels) > 1 and labels[-1] in GENERIC_SECOND_LEVEL:
        labels = labels[:-1]
    if labels[-1].startswith("xn--"):
        return labels[-1]
    tokens = [token for token in labels[-1].split("-") if token]
    if not tokens:
        return labels[-1]
    brand = tokens[0]
    for token in tokens[1:]:
        if len(brand) >= 3:
            break
        brand += token
    return brand


def clean_term(value):
    """A brand or domain fragment, or "" when too broad or malformed."""
    term = (value or "").strip().lower()
    if len(term) < MIN_TERM_LENGTH or len(term) > 253 or not TERM.fullmatch(term):
        return ""
    return term


def destinations(qs):
    """Brands, destination domains and shared hosts linked by the given findings."""
    pairs = models.PromotionalSpamGroup.objects.filter(
        kind="domain", finding__in=qs
    ).values_list("finding_id", "key")
    domain_blogs = defaultdict(set)
    shared_blogs = defaultdict(set)
    for finding_id, domain in pairs.iterator(chunk_size=5000):
        if moderation_campaigns.shared_host(domain):
            shared_blogs[domain].add(finding_id)
        else:
            domain_blogs[domain].add(finding_id)
    brands = defaultdict(lambda: {"blogs": set(), "domains": set()})
    for domain, blogs in domain_blogs.items():
        brand = brands[brand_key(domain)]
        brand["blogs"] |= blogs
        brand["domains"].add(domain)

    def ranked(groups):
        return sorted(
            ({"domain": d, "blogs": len(b)} for d, b in groups.items()),
            key=lambda row: (-row["blogs"], row["domain"]),
        )

    return {
        "brands": sorted(
            (
                {
                    "brand": name,
                    "blogs": len(brand["blogs"]),
                    "domain_count": len(brand["domains"]),
                    "domains": sorted(
                        brand["domains"], key=lambda d: (-len(domain_blogs[d]), d)
                    )[:5],
                }
                for name, brand in brands.items()
                if name
            ),
            key=lambda row: (-row["blogs"], row["brand"]),
        ),
        "domains": ranked(domain_blogs),
        "shared": ranked(shared_blogs),
    }


def matching_domains(term, qs=None):
    """Destination domains (not shared hosts) of gambling blogs containing term."""
    qs = findings() if qs is None else qs
    domains = (
        models.PromotionalSpamGroup.objects.filter(
            kind="domain", key__contains=term, finding__in=qs
        )
        .values("key")
        .annotate(blogs=Count("finding_id"))
        .order_by("-blogs", "key")
    )
    return [row for row in domains if not moderation_campaigns.shared_host(row["key"])]


def matching(term, domains=None):
    """Gambling findings linking any destination domain that contains term."""
    if domains is None:
        domains = matching_domains(term)
    keys = [row["key"] for row in domains]
    linked = models.PromotionalSpamGroup.objects.filter(
        kind="domain", key__in=keys
    ).values("finding_id")
    return findings().filter(pk__in=linked)


def summary(qs):
    eligible = unprotected(qs).count()
    total = qs.count()
    return {
        "total": total,
        "dismissed": findings(dismissed=True).count(),
        "eligible": eligible,
        "protected": total - eligible,
        "in_strong_campaign": qs.filter(campaign_strong=True)
        .exclude(campaign_key="")
        .count(),
        "posts": models.Post.objects.filter(owner__in=qs.values("owner_id")).count(),
        "recommendations": [
            (
                moderation_campaigns.RECOMMENDATIONS[key],
                key,
                qs.filter(pk__in=members.values("pk")).count(),
            )
            for key, members in moderation_campaigns.recommendation_sets().items()
        ],
    }


def signups(qs, months=24):
    return list(
        models.User.objects.filter(pk__in=qs.values("owner_id"))
        .annotate(month=TruncMonth("date_joined"))
        .values("month")
        .annotate(blogs=Count("pk"))
        .order_by("-month")[:months]
    )


def campaigns(qs, limit=25):
    groups = list(
        qs.exclude(campaign_key="")
        .values("campaign_key")
        .annotate(blogs=Count("pk"), strong=Count("pk", filter=Q(campaign_strong=True)))
        .order_by("-blogs", "campaign_key")[:limit]
    )
    titles = dict(
        qs.filter(campaign_key__in=[g["campaign_key"] for g in groups])
        .order_by("campaign_key", "pk")
        .distinct("campaign_key")
        .values_list("campaign_key", "evidence__campaign__title")
    )
    for group in groups:
        group["title"] = titles.get(group["campaign_key"]) or group["campaign_key"][:8]
        group["actionable"] = group["strong"] >= moderation_campaigns.MIN_GROUP_SIZE
    return groups


def still_posting(qs, days=7, limit=25):
    since = timezone.now() - timedelta(days=days)
    recent = models.Post.objects.filter(
        owner__in=qs.values("owner_id"), created_at__gte=since
    )
    return {
        "days": days,
        "blogs": recent.values("owner_id").distinct().count(),
        "posts": recent.count(),
        "rows": list(
            recent.values("owner_id", "owner__username")
            .annotate(posts=Count("pk"), latest=Max("created_at"))
            .order_by("-latest")[:limit]
        ),
    }


def most_viewed(qs, days=30, limit=25):
    since = timezone.now() - timedelta(days=days)
    owners = qs.values("owner_id")
    views = defaultdict(lambda: {"username": "", "post_views": 0, "page_views": 0})
    for row in (
        models.AnalyticPost.objects.filter(
            created_at__gte=since, post__owner__in=owners
        )
        .values("post__owner_id", "post__owner__username")
        .annotate(n=Count("pk"))
        .order_by("-n")[: limit * 4]
    ):
        entry = views[row["post__owner_id"]]
        entry["username"] = row["post__owner__username"]
        entry["post_views"] = row["n"]
    for row in (
        models.AnalyticPage.objects.filter(created_at__gte=since, user__in=owners)
        .values("user_id", "user__username")
        .annotate(n=Count("pk"))
        .order_by("-n")[: limit * 4]
    ):
        entry = views[row["user_id"]]
        entry["username"] = row["user__username"]
        entry["page_views"] = row["n"]
    rows = [
        {"owner_id": owner_id, "views": e["post_views"] + e["page_views"], **e}
        for owner_id, e in views.items()
    ]
    rows.sort(key=lambda row: (-row["views"], row["username"]))
    return {"days": days, "rows": rows[:limit]}
