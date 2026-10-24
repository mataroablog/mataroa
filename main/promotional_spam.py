"""Bounded, explainable promotional-content sampling. Never fetch external URLs."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from statistics import median
from urllib.parse import parse_qs, urlsplit

from django.db import transaction
from django.db.models import Count, F, Max, Q, TextField, Value, Window
from django.db.models.functions import (
    MD5,
    Coalesce,
    Length,
    Right,
    RowNumber,
    Substr,
    TruncDate,
)
from django.utils import timezone

from main import models
from main.views.moderation_inbox import ContentPreview

VERSION = 1
THRESHOLD = 50
HEAD_LENGTH = 8000
TAIL_LENGTH = 4000


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def hostname(value):
    try:
        host = urlsplit(value).hostname or ""
        host = host.lower().removeprefix("www.").encode("idna").decode("ascii")
        if len(host) <= 253 and re.fullmatch(r"[a-z0-9.-]+", host) and "." in host:
            return host
    except (ValueError, UnicodeError):
        pass
    return ""


class LinkPreview(ContentPreview):
    def __init__(self, body, base_url, own_domains):
        self.outbound = set()
        self.own_domains = own_domains
        self.backlink = False
        super().__init__(body, base_url)

    def add_domain(self, value):
        if url := self.safe_url(value):
            domain = hostname(url)
            if domain and domain not in self.own_domains:
                self.outbound.add(domain)
                params = parse_qs(urlsplit(url).query)
                if any(v.lower() == "backlink" for v in params.get("utm_medium", [])):
                    self.backlink = True


def metadata(owners):
    """Cheap metadata sweep catches additions, edits, deletions and profile changes."""
    ids = [u.pk for u in owners]
    stats = {}
    for model, kind in ((models.Post, "post"), (models.Page, "page")):
        stats[kind] = {
            row["owner_id"]: row
            for row in model.objects.filter(owner_id__in=ids)
            .order_by()
            .values("owner_id")
            .annotate(count=Count("pk"), latest=Max("updated_at"), last_id=Max("pk"))
        }
    return stats


def owner_batch(after, upper_bound, size):
    return list(
        models.User.objects.filter(pk__gt=after, pk__lte=upper_bound)
        .order_by("pk")
        .only("pk", "username", "custom_domain", "redirect_domain")
        .annotate(
            byline_hash=MD5(
                Coalesce("blog_byline", Value("", output_field=TextField()))
            ),
            footer_hash=MD5(
                Coalesce("footer_note", Value("", output_field=TextField()))
            ),
            byline_head=Substr("blog_byline", 1, HEAD_LENGTH),
            byline_tail=Right("blog_byline", TAIL_LENGTH),
            byline_chars=Length(
                Coalesce("blog_byline", Value("", output_field=TextField()))
            ),
            footer_head=Substr("footer_note", 1, HEAD_LENGTH),
            footer_tail=Right("footer_note", TAIL_LENGTH),
            footer_chars=Length(
                Coalesce("footer_note", Value("", output_field=TextField()))
            ),
        )[:size]
    )


def source_signature(owner, stats):
    return digest(
        [
            VERSION,
            owner.username,
            owner.custom_domain,
            owner.redirect_domain,
            owner.byline_hash,
            owner.footer_hash,
            stats["post"].get(owner.pk),
            stats["page"].get(owner.pk),
        ]
    )


def sample_batch(model, owners, stats, archive_size):
    """Select IDs with window functions before fetching bounded body excerpts."""
    eligible = {u.pk: stats[u.pk]["count"] for u in owners if u.pk in stats}
    if not eligible:
        return {}
    positions = Q()
    for owner_id, count in eligible.items():
        size = min(archive_size, count)
        positions |= Q(
            owner_id=owner_id,
            position__in=[1 + i * (count - 1) // max(1, size - 1) for i in range(size)],
        )
    qs = model.objects.filter(owner_id__in=eligible).order_by()
    archive = (
        qs.annotate(
            position=Window(
                RowNumber(), partition_by=F("owner_id"), order_by=F("id").asc()
            )
        )
        .filter(positions)
        .values_list("pk", flat=True)
    )
    recent = (
        qs.annotate(
            position=Window(
                RowNumber(),
                partition_by=F("owner_id"),
                order_by=[F("updated_at").desc(), F("id").desc()],
            )
        )
        .filter(position__lte=4)
        .values_list("pk", flat=True)
    )
    ids = set(archive) | set(recent)
    grouped = defaultdict(list)
    for row in (
        model.objects.filter(pk__in=ids)
        .order_by("pk")
        .annotate(
            head=Substr("body", 1, HEAD_LENGTH),
            tail=Right("body", TAIL_LENGTH),
            chars=Length(Coalesce("body", Value("", output_field=TextField()))),
        )
        .values("pk", "owner_id", "title", "head", "tail", "chars")
    ):
        grouped[row["owner_id"]].append(row)
    return grouped


def body_source(row):
    head = row["head"] or ""
    tail = row["tail"] or ""
    if row["chars"] <= HEAD_LENGTH:
        return head
    # Avoid overlapping head/tail text in moderately sized documents.
    return head + "\n" + tail[-min(TAIL_LENGTH, row["chars"] - HEAD_LENGTH) :]


def template_key(text, title):
    text = text.lower().replace(title.lower(), " ") if title else text.lower()
    text = re.sub(r"https?://\S+", " URL ", text)
    text = re.sub(r"\d+", "#", text)
    text = " ".join(text.split())
    return digest(text) if len(text) >= 80 else ""


def analyze(owner, posts, pages, stats, busiest):
    base_url = owner.blog_absolute_url
    own = {
        hostname(base_url),
        hostname("https://" + (owner.custom_domain or "")),
        hostname("https://mataroa.blog"),
    }
    domains, templates = Counter(), Counter()
    lengths, examples = [], []
    linked = thin = backlinks = 0
    titles = Counter()
    for kind, rows in (("post", posts), ("page", pages)):
        for row in rows:
            preview = LinkPreview(body_source(row), base_url, own)
            lengths.append(row["chars"])
            domains.update(preview.outbound)
            linked += bool(preview.outbound)
            is_thin = (
                bool(preview.outbound)
                and row["chars"] <= 2000
                and len(preview.text) <= 800
            )
            thin += is_thin
            backlinks += preview.backlink
            titles[" ".join(row["title"].lower().split())] += 1
            key = template_key(preview.text, row["title"]) if preview.outbound else ""
            if key:
                templates[key] += 1
            examples.append(
                {
                    "kind": kind,
                    "id": row["pk"],
                    "title": row["title"],
                    "excerpt": preview.text[:500],
                    "domains": sorted(preview.outbound)[:10],
                    "weight": int(preview.backlink) * 4
                    + int(is_thin) * 2
                    + bool(preview.outbound),
                }
            )
    profile = LinkPreview(
        "\n".join(
            body_source(
                {
                    "head": getattr(owner, f"{field}_head"),
                    "tail": getattr(owner, f"{field}_tail"),
                    "chars": getattr(owner, f"{field}_chars"),
                }
            )
            for field in ("byline", "footer")
        ),
        base_url,
        own,
    )
    profile_domains = sorted(profile.outbound)
    n = len(lengths)
    total = sum(stats[k].get(owner.pk, {}).get("count", 0) for k in ("post", "page"))
    common = domains.most_common(1)
    concentrated = n >= 3 and common and common[0][1] / n >= 0.75
    thin_majority = n >= 3 and thin / n >= 0.75
    repeated = n >= 3 and max(titles.values(), default=0) >= max(3, n / 2)
    templated = max(templates.values(), default=0) >= 3
    burst = busiest >= 20
    explicit = backlinks > 0 or profile.backlink
    reasons = []
    score = 0
    for condition, points, reason in [
        (
            thin_majority,
            30,
            f"{thin} of {n} sampled items are short and link to outside sites.",
        ),
        (
            concentrated,
            25,
            f"{common[0][1] if common else 0} of {n} sampled items link to {common[0][0] if common else ''}.",
        ),
        (
            explicit,
            35,
            f"Explicit backlink tracking in {backlinks} sampled items"
            + (" and blog settings." if profile.backlink else "."),
        ),
        (burst, 15, f"{busiest} posts created on the busiest day."),
        (repeated, 15, "Repeated titles across the content sample."),
        (templated, 15, "Repeated wording after removing titles, URLs and numbers."),
        (
            thin_majority and total >= 20,
            10,
            f"{total} posts and pages in this archive.",
        ),
    ]:
        if condition:
            score += points
            reasons.append(reason)
    qualifies = explicit or (
        n
        and linked / n >= 0.75
        and (
            (thin_majority and (concentrated or total >= 20 or repeated or templated))
            or (concentrated and (burst or repeated or templated))
        )
    )
    score = max(THRESHOLD, min(score, 100)) if qualifies else 0
    evidence = {
        "reasons": reasons,
        "sampled": n,
        "linked": linked,
        "thin": thin,
        "median_chars": int(median(lengths)) if lengths else 0,
        "posts": stats["post"].get(owner.pk, {}).get("count", 0),
        "pages": stats["page"].get(owner.pk, {}).get("count", 0),
        "domains": [{"domain": d, "count": c} for d, c in domains.most_common(20)],
        "profile_domains": profile_domains[:20],
        "profile_excerpt": profile.text[:500],
        "examples": sorted(examples, key=lambda x: -x["weight"])[:3],
        "templates": sorted(templates)[:20],
    }
    return score, evidence


def baseline(score, evidence):
    return {
        "score": score,
        "total": evidence.get("posts", 0) + evidence.get("pages", 0),
        "domains": sorted(d["domain"] for d in evidence.get("domains", [])),
        "profile_domains": evidence.get("profile_domains", []),
    }


def substantial_change(old, new):
    """Compare against the dismissal, not the last scan, so changes accumulate."""
    return (
        new["score"] >= old.get("score", 0) + 20
        or new["total"] >= old.get("total", 0) + max(5, old.get("total", 0) // 10)
        or bool(set(new["domains"]) - set(old.get("domains", [])))
        or bool(set(new["profile_domains"]) - set(old.get("profile_domains", [])))
    )


def scan_batch(owners, force=False):
    stats = metadata(owners)
    existing = {
        f.owner_id: f
        for f in models.PromotionalSpamFinding.objects.filter(owner__in=owners)
    }
    changed = [
        u
        for u in owners
        if force
        or u.pk not in existing
        or existing[u.pk].source_signature != source_signature(u, stats)
    ]
    if not changed:
        return 0
    posts = sample_batch(models.Post, changed, stats["post"], 8)
    pages = sample_batch(models.Page, changed, stats["page"], 4)
    busiest = defaultdict(int)
    for row in (
        models.Post.objects.filter(owner__in=changed)
        .order_by()
        .values("owner_id")
        .annotate(day=TruncDate("created_at"))
        .values("owner_id", "day")
        .annotate(count=Count("pk"))
    ):
        busiest[row["owner_id"]] = max(busiest[row["owner_id"]], row["count"])
    for owner in changed:
        score, evidence = analyze(
            owner,
            posts.get(owner.pk, []),
            pages.get(owner.pk, []),
            stats,
            busiest[owner.pk],
        )
        with transaction.atomic():
            # A blog may have been deleted while its sample was being analyzed.
            if not models.User.objects.filter(pk=owner.pk).select_for_update().exists():
                continue
            finding, _ = (
                models.PromotionalSpamFinding.objects.select_for_update().get_or_create(
                    owner=owner, defaults={"scanned_at": timezone.now()}
                )
            )
            if (
                finding.dismissed_at
                and score >= THRESHOLD
                and substantial_change(
                    finding.dismissed_baseline, baseline(score, evidence)
                )
            ):
                finding.dismissed_at = None
                finding.dismissed_baseline = {}
            finding.score = score
            finding.evidence = evidence
            finding.source_signature = source_signature(owner, stats)
            finding.scanned_at = timezone.now()
            finding.save()
            finding.groups.all().delete()
            groups = [("domain", d["domain"]) for d in evidence["domains"]]
            groups += [("domain", d) for d in evidence["profile_domains"]]
            groups += [("template", key) for key in evidence["templates"]]
            models.PromotionalSpamGroup.objects.bulk_create(
                [
                    models.PromotionalSpamGroup(finding=finding, kind=kind, key=key)
                    for kind, key in set(groups)
                ]
            )
    return len(changed)
