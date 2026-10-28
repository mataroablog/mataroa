"""Full-archive recognition of the audited Markdown gallery promotion template."""

import re
from collections import Counter
from urllib.parse import parse_qs, urlsplit

from main import models, moderation_campaigns
from main.views.moderation_inbox import ContentPreview

FAMILY = "automated-gallery-promotion"
KEY = moderation_campaigns.fingerprint([FAMILY, 1])
CTA = re.compile(r"view\s+full\s+gallery", re.I)
CURATED = re.compile(r"curated\s+gallery\s*\|\s*\d+\s+images", re.I)
URL = r"https?://[^\s<>()]{1,2048}"
GALLERY_LINK = re.compile(
    rf"(?<!!)\[\s*view\s+full\s+gallery\s*\]\(\s*<?({URL})>?\s*\)", re.I
)
LINKED_IMAGE = re.compile(
    rf"\[!\[(?:\\.|[^\]\\]){{0,2048}}\]\(\s*<?({URL})>?\s*\)\]"
    rf"\(\s*<?({URL})>?\s*\)",
    re.I,
)
KNOWN_DESTINATIONS = {("go.cbrop.com", "/prse"), ("sea.onlyfuns.win", "/ydt")}
BUTTON_NAME = "cta-1-gradient-pill.png"


def visible_source(body):
    """Ignore quoted code and HTML comments; never render HTML or fetch resources."""
    body = re.sub(r"<!--.*?(?:-->|\Z)", "", body, flags=re.S)
    lines = []
    fence = None
    for line in body.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            continue
        if fence is None and not line.startswith(("    ", "\t")):
            lines.append(line)
    return re.sub(r"`+[^`\n]*`+", "", "\n".join(lines))


def destination(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if not host or moderation_campaigns.shared_host(host):
            return None
        return host, parsed.path, parse_qs(parsed.query)
    except ValueError:
        return None


def match(body, own_domains=()):
    """Require a real gallery CTA, linked thumbnails, and corroborating markers.

    The audited templates use inline Markdown. A common word, image host or
    celebrity name alone cannot link an account to this campaign.
    """
    body = body or ""
    if not CTA.search(body):
        return None
    body = visible_source(body)
    links = [destination(url) for url in GALLERY_LINK.findall(body)]
    links = [link for link in links if link and link[0] not in own_domains]
    if not links:
        return None
    images = []
    for source, target in LINKED_IMAGE.findall(body):
        link = destination(target)
        if link and link[0] not in own_domains:
            images.append((source, link))
    # The button itself is not a content thumbnail.
    thumbnails = [pair for pair in images if BUTTON_NAME not in pair[0].lower()]
    if not thumbnails:
        return None
    curated = bool(CURATED.search(body))
    known = any(
        (host, path.rstrip("/")) in KNOWN_DESTINATIONS for host, path, _ in links
    )
    button = False
    for source, target in images:
        try:
            button |= (
                urlsplit(source).path.rsplit("/", 1)[-1].lower() == BUTTON_NAME
                and target in links
            )
        except ValueError:
            continue
    tracked = any("mataroa" in link[2].get("f", []) for _, link in thumbnails)
    # Changed destinations need both the distinctive button and tracked thumbnails.
    # Missing image-count wording needs the same additional corroboration.
    regular = len(thumbnails) >= 2 and ((curated and known) or (button and tracked))
    single_preview = known and button and tracked
    if not (regular or single_preview):
        return None
    return {
        "domains": sorted({link[0] for link in links}),
        "signals": [
            name
            for name, yes in (
                ("Gallery image-count wording", curated),
                ("Known gallery destination", known),
                ("Matching gallery button", button),
                ("Mataroa referral tracking on thumbnails", tracked),
            )
            if yes
        ],
    }


def example(row, kind, markers=None):
    # Plain escaped text in templates, bounded for saved review evidence.
    source = visible_source(row["body"] or "")
    start = 0
    if markers and (cta := CTA.search(source)):
        start = max(0, cta.start() - 60)
    text = ContentPreview(source[start : start + 4000], "https://mataroa.blog/").text
    return {
        "kind": kind,
        "id": row["pk"],
        "title": row["title"],
        "excerpt": " ".join(text.split())[:500],
        "signals": markers["signals"] if markers else [],
    }


def scan_archives(owners):
    """Discover in full bodies, then inspect every post/page of each candidate.

    Stream rows so large archives do not accumulate in memory. No production
    writes occur here. All drafts, scheduled posts and hidden pages are included.
    """
    by_id = {u.pk: u for u in owners}
    candidates = set()
    for model in (models.Post, models.Page):
        candidates.update(
            model.objects.filter(
                owner_id__in=by_id, body__iregex=r"view\s+full\s+gallery"
            )
            .order_by()
            .values_list("owner_id", flat=True)
            .distinct()
        )
    results = {
        pk: {
            "checked": 0,
            "matched": 0,
            "example": None,
            "counterexample": None,
            "domains": Counter(),
        }
        for pk in candidates
    }
    for model, kind in ((models.Post, "post"), (models.Page, "page")):
        for row in (
            model.objects.filter(owner_id__in=candidates)
            .order_by("pk")
            .values("pk", "owner_id", "title", "body")
            .iterator(chunk_size=50)
        ):
            owner = by_id[row["owner_id"]]
            own = {owner.username + ".mataroa.blog", owner.custom_domain or ""}
            result = results[owner.pk]
            result["checked"] += 1
            markers = match(row["body"], own)
            if markers:
                result["matched"] += 1
                result["domains"].update(markers["domains"])
                if result["example"] is None:
                    result["example"] = example(row, kind, markers)
            elif result["counterexample"] is None:
                result["counterexample"] = example(row, kind)
    return {pk: result for pk, result in results.items() if result["matched"]}


def campaign(result, total):
    complete = result["checked"] == total
    matched = result["matched"]
    return {
        "key": KEY,
        "family": FAMILY,
        "title": "Automated gallery promotion",
        "reason": "Matching gallery templates with corroborating destination, button or referral evidence. Destination changes stay in the same group; pornography is classified separately.",
        "scope": "full_archive",
        "matched": matched,
        "checked": result["checked"],
        "total": total,
        "complete": complete,
        "coverage": min(100, matched * 100 // total) if total else 0,
        "strong": complete and matched == total and matched >= 2,
        "example": result["example"],
        "counterexample": result["counterexample"],
        "domains": [
            {"domain": domain, "count": count}
            for domain, count in result["domains"].most_common(20)
        ],
    }
