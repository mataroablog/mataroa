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

from main import blog_classification as classification
from main import gallery_campaigns, models, moderation_campaigns
from main.views.moderation_inbox import ContentPreview

VERSION = 4
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
        self.anchor = None
        self.linked_images = 0
        self.gallery_destinations = set()
        self.outline = []
        super().__init__(body, base_url)
        self.finish_anchor()

    def finish_anchor(self):
        if self.anchor:
            url, text, image = self.anchor
            if text.strip() or not image:
                self.add_domain(url)
            if re.fullmatch(r"\s*view full gallery\s*", text, re.I):
                domain = hostname(self.safe_url(url) or "")
                if domain and domain not in self.own_domains:
                    self.gallery_destinations.add(domain)
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.outline.append(tag)
        if tag == "a":
            self.finish_anchor()
            self.anchor = [values.get("href") or "", "", False]
        else:
            if tag == "img" and self.anchor:
                self.anchor[2] = True
                self.linked_images += 1
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "a":
            self.finish_anchor()

    def handle_data(self, data):
        if self.anchor:
            self.anchor[1] += data
            self.text_parts.append(data)
        else:
            super().handle_data(data)

    def add_domain(self, value):
        if url := self.safe_url(value):
            if re.search(
                r"\.(?:jpe?g|png|gif|webp|avif|svg|heic)$", urlsplit(url).path, re.I
            ):
                return
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
    for model, kind, updated_field in (
        (models.Post, "post", "updated_at"),
        (models.Page, "page", "updated_at"),
        (models.Image, "image", "uploaded_at"),
    ):
        stats[kind] = {
            row["owner_id"]: row
            for row in model.objects.filter(owner_id__in=ids)
            .order_by()
            .values("owner_id")
            .annotate(count=Count("pk"), latest=Max(updated_field), last_id=Max("pk"))
        }
    return stats


def owner_queryset():
    return (
        models.User.objects.order_by("pk")
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
        )
    )


def owner_batch(after, upper_bound, size):
    return list(owner_queryset().filter(pk__gt=after, pk__lte=upper_bound)[:size])


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
            stats["image"].get(owner.pk),
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
    campaign_items = []
    linked = thin = backlinks = calls_to_action = 0
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
            labels = classification.item_labels(
                row["title"], preview.text, bool(preview.outbound), len(preview.images)
            )
            cta = classification.CTA.search(preview.text)
            calls_to_action += bool(
                preview.outbound
                and cta
                and (
                    cta.group().lower() != "view full gallery"
                    or "pornography" in labels
                )
            )
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
                    "categories": sorted(labels),
                    "weight": int(preview.backlink) * 4
                    + int(is_thin) * 2
                    + bool(preview.outbound),
                }
            )
            campaign_items.append(
                {
                    "patterns": moderation_campaigns.item_patterns(preview, labels),
                    "example": examples[-1],
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
    promotion_majority = n >= 3 and calls_to_action / n >= 0.75
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
        (
            promotion_majority,
            20,
            f"{calls_to_action} of {n} sampled items direct readers to outside content or offers.",
        ),
        (
            burst,
            10,
            f"{busiest} posts created on the busiest day, excluding backdated posts.",
        ),
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
            (
                thin_majority
                and promotion_majority
                and (concentrated or total >= 20 or templated)
            )
            or (concentrated and templated and promotion_majority)
        )
    )
    score = max(THRESHOLD, min(score, 100)) if qualifies else 0
    category_reasons = classification.classify(
        examples,
        total=total + stats["image"].get(owner.pk, {}).get("count", 0),
        profile_text=(
            ""
            if not owner.byline_chars
            and (
                not owner.footer_chars
                or owner.footer_hash
                == hashlib.md5(
                    models.User._meta.get_field("footer_note").get_default().encode()
                ).hexdigest()
            )
            else profile.text
        )
        or owner.redirect_domain
        or "",
        profile_labels=classification.item_labels(
            "", profile.text, bool(profile.outbound), len(profile.images)
        ),
        promotional=bool(score),
        thin=thin,
        linked=linked,
    )
    # Show a representative for each label, then the strongest promotion examples.
    selected = []
    for category in category_reasons:
        match = next((e for e in examples if category in e["categories"]), None)
        if match and match not in selected:
            selected.append(match)
    for example in sorted(examples, key=lambda x: -x["weight"])[:3]:
        if example not in selected:
            selected.append(example)
    evidence = {
        "category_reasons": category_reasons,
        "campaign": moderation_campaigns.assign(campaign_items),
        "reasons": reasons,
        "sampled": n,
        "linked": linked,
        "thin": thin,
        "median_chars": int(median(lengths)) if lengths else 0,
        "posts": stats["post"].get(owner.pk, {}).get("count", 0),
        "pages": stats["page"].get(owner.pk, {}).get("count", 0),
        "images": stats["image"].get(owner.pk, {}).get("count", 0),
        "domains": [{"domain": d, "count": c} for d, c in domains.most_common(20)],
        "profile_domains": profile_domains[:20],
        "profile_excerpt": profile.text[:500],
        "examples": selected,
        "templates": sorted(templates)[:20],
    }
    return score, evidence


def baseline(score, evidence):
    return {
        "score": score,
        "categories": sorted(evidence.get("category_reasons", {})),
        "total": evidence.get("posts", 0) + evidence.get("pages", 0),
        "domains": sorted(d["domain"] for d in evidence.get("domains", [])),
        "profile_domains": evidence.get("profile_domains", []),
    }


def substantial_change(old, new):
    """Compare against the dismissal, not the last scan, so changes accumulate."""
    return (
        bool(set(new["categories"]) - set(old.get("categories", [])))
        or new["score"] >= old.get("score", 0) + 20
        or new["total"] >= old.get("total", 0) + max(5, old.get("total", 0) // 10)
        or bool(set(new["domains"]) - set(old.get("domains", [])))
        or bool(set(new["profile_domains"]) - set(old.get("profile_domains", [])))
    )


def scan_batch(owners):
    stats = metadata(owners)
    changed = owners
    galleries = gallery_campaigns.scan_archives(owners)
    posts = sample_batch(models.Post, changed, stats["post"], 8)
    pages = sample_batch(models.Page, changed, stats["page"], 4)
    busiest = defaultdict(int)
    for row in (
        models.Post.objects.filter(owner__in=changed)
        .filter(
            Q(published_at__isnull=True) | Q(published_at__gte=TruncDate("created_at"))
        )
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
        if owner.pk in galleries:
            total = sum(
                stats[k].get(owner.pk, {}).get("count", 0) for k in ("post", "page")
            )
            evidence["campaign"] = gallery_campaigns.campaign(
                galleries[owner.pk], total
            )
            evidence["category_reasons"]["gallery_promotion"] = (
                f"Automated gallery promotion template in {galleries[owner.pk]['matched']} "
                f"of {total} posts and pages; full archive checked."
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
                and (
                    score >= THRESHOLD
                    or "pornography" in evidence["category_reasons"]
                    or "gambling" in evidence["category_reasons"]
                    or "gallery_promotion" in evidence["category_reasons"]
                )
                and substantial_change(
                    finding.dismissed_baseline, baseline(score, evidence)
                )
            ):
                finding.dismissed_at = None
                finding.dismissed_baseline = {}
            campaign = evidence["campaign"]
            finding.campaign_key = campaign.get("key", "")
            finding.campaign_coverage = campaign.get("coverage", 0)
            finding.campaign_strong = campaign.get("strong", False)
            finding.rule_version = VERSION
            finding.score = score
            finding.evidence = evidence
            finding.source_signature = source_signature(owner, stats)
            finding.scanned_at = timezone.now()
            finding.save()
            finding.groups.all().delete()
            groups = [("domain", d["domain"]) for d in evidence["domains"]]
            groups += [("domain", d) for d in evidence["profile_domains"]]
            groups += [("template", key) for key in evidence["templates"]]
            groups += [("category", key) for key in evidence["category_reasons"]]
            models.PromotionalSpamGroup.objects.bulk_create(
                [
                    models.PromotionalSpamGroup(finding=finding, kind=kind, key=key)
                    for kind, key in set(groups)
                ]
            )
    return len(changed)
