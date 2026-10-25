"""Explainable campaign fingerprints from content already sampled by the scanner."""

import hashlib
import json
import re
from collections import Counter

# Shared publishing/social hosts are too broad to identify a campaign by hostname.
SHARED_HOSTS = {
    "mataroa.blog",
    "youtube.com",
    "youtu.be",
    "t.me",
    "telegram.me",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "google.com",
    "wikipedia.org",
    "github.com",
    "medium.com",
    "wordpress.com",
    "blogspot.com",
    "wikimedia.org",
    "telegra.ph",
    "graph.org",
    "tumblr.com",
    "notion.site",
    "notepin.co",
    "rentry.co",
    "dev.to",
    "substack.com",
    "blogfreely.net",
    "paper.wf",
    "write.as",
    "reddit.com",
    "quora.com",
    "linkedin.com",
    "paste.rs",
    "e-monsite.com",
    "mystrikingly.com",
}
MIN_GROUP_SIZE = 3
STRONG_COVERAGE = 80


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def shared_host(domain):
    return any(domain == host or domain.endswith("." + host) for host in SHARED_HOSTS)


def item_patterns(preview, labels):
    """Each key combines a content category, a specific layout and destination."""
    patterns = []
    gallery = (
        "pornography" in labels
        and re.search(r"\bcurated gallery\s*\|\s*\d+ images\b", preview.text, re.I)
        and preview.linked_images >= 2
    )
    if gallery:
        for domain in sorted(preview.gallery_destinations):
            if not shared_host(domain):
                patterns.append(
                    {
                        "family": "explicit-gallery",
                        "domain": domain,
                        "layout": "gallery-link / image-count / linked-images",
                        "title": f"Explicit gallery template → {domain}",
                        "reason": "Explicit sexual content, the same gallery wording and linked-image layout, and the same gallery destination.",
                    }
                )
    # A shared link or a mention of gambling is insufficient. Require offers,
    # a repeated article outline and an image, as well as the category rule.
    if "gambling" in labels and len(preview.outline) >= 3 and preview.images:
        layout = "/".join(preview.outline[:4])
        for domain in sorted(preview.outbound):
            if not shared_host(domain):
                patterns.append(
                    {
                        "family": "gambling-article",
                        "domain": domain,
                        "layout": layout,
                        "title": f"Gambling promotion articles → {domain}",
                        "reason": f"Gambling offers, an image, the same heading outline ({layout}), and the same outbound destination.",
                    }
                )
    for pattern in patterns:
        pattern["key"] = fingerprint(
            [pattern["family"], pattern["domain"], pattern["layout"]]
        )
    return patterns


def assign(items):
    """Choose the dominant fingerprint; low coverage remains a review-only match."""
    counts = Counter(pattern["key"] for item in items for pattern in item["patterns"])
    if not counts:
        return {}
    key, matched = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0]
    matching = [
        item for item in items if any(p["key"] == key for p in item["patterns"])
    ]
    other = [item for item in items if item not in matching]
    pattern = next(p for p in matching[0]["patterns"] if p["key"] == key)
    coverage = matched * 100 // len(items)
    # Several snippets from one item never count as several independent matches.
    # Gallery blogs need at least two matching items; single-article gambling
    # blogs still require corroboration from other blogs before group deletion.
    strong = coverage >= STRONG_COVERAGE and (
        matched >= 2 or pattern["family"] == "gambling-article"
    )
    return {
        **pattern,
        "matched": matched,
        "sampled": len(items),
        "coverage": coverage,
        "strong": strong,
        "example": matching[0]["example"],
        "counterexample": other[0]["example"] if other else None,
    }


RECOMMENDATIONS = {
    "strong": "Strong deletion candidate",
    "review": "Needs review",
    "none": "No deletion recommendation",
    "protected": "Protected",
}


def recommendation_sets():
    from django.db.models import Count

    from main import models, moderation_deletion, promotional_spam

    base = models.PromotionalSpamFinding.objects.filter(
        rule_version=promotional_spam.VERSION
    )
    protected = base.filter(
        owner__in=models.User.objects.filter(moderation_deletion.PROTECTED)
    )
    grouped = base.filter(campaign_strong=True, dismissed_at__isnull=True).exclude(
        campaign_key=""
    )
    keys = (
        grouped.values("campaign_key")
        .annotate(n=Count("pk"))
        .filter(n__gte=MIN_GROUP_SIZE)
        .values("campaign_key")
    )
    strong = grouped.filter(campaign_key__in=keys).exclude(pk__in=protected)
    flagged = base.filter(
        dismissed_at__isnull=True,
        groups__kind="category",
        groups__key__in=["pornography", "gambling", "promotional"],
    ).distinct()
    review = flagged.exclude(pk__in=protected).exclude(pk__in=strong)
    return {
        "strong": strong,
        "review": review,
        "protected": protected,
        "none": base.exclude(pk__in=protected)
        .exclude(pk__in=strong)
        .exclude(pk__in=review),
    }
