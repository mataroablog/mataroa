"""Read-only, content-first moderation. Signals are review aids, not verdicts."""

import re
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import markdown
from django.conf import settings
from django.db.models import (
    Count,
    Exists,
    F,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    prefetch_related_objects,
)
from django.db.models.functions import (
    Coalesce,
    Greatest,
    Length,
    Lower,
    StrIndex,
    Substr,
)
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from main import models, scheme

SAMPLE_LENGTH = 4000
PREVIEW_LENGTH = 50000
PAGE_SIZE = 25


def _canonical_redirect(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()
    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.get_full_path()}")


class ContentPreview(HTMLParser):
    """Extract text and URLs; never insert user HTML into the moderation UI."""

    def __init__(self, body, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.text_parts = []
        self.domains = set()
        self.images = []
        self.feed(markdown.markdown(body or ""))
        self.text = " ".join(" ".join(self.text_parts).split())

    def safe_url(self, value):
        try:
            url = urljoin(self.base_url, value)
            parsed = urlsplit(url)
            if (
                parsed.scheme in ("http", "https")
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            ):
                return url
        except ValueError:
            pass
        return None

    def add_domain(self, value):
        if url := self.safe_url(value):
            host = urlsplit(url).hostname
            if host not in {
                urlsplit(self.base_url).hostname,
                urlsplit(f"https://{settings.CANONICAL_HOST}").hostname,
            }:
                self.domains.add(host)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.add_domain(attrs["href"])
        elif tag == "img" and attrs.get("src"):
            if (url := self.safe_url(attrs["src"])) and url not in self.images:
                self.images.append(url)
            self.text_parts.append(attrs.get("alt", ""))

    def handle_data(self, data):
        self.text_parts.append(data)
        for url in re.findall(r"https?://[^\s<>\"\)]+", data):
            self.add_domain(url.rstrip(".,;"))


def _latest(queryset, field):
    return Subquery(
        queryset.order_by()
        .values("owner_id")
        .annotate(latest=Max(field))
        .values("latest")
    )


def _sample_queryset(model, query, limit):
    qs = model.objects.defer("body")
    sample_start = Value(1)
    ordering = ["-updated_at", "-id"]
    if query:
        qs = qs.annotate(
            matches=Coalesce(
                Q(title__icontains=query) | Q(body__icontains=query), False
            )
        )
        sample_start = Greatest(
            StrIndex(Lower("body"), Value(query.lower())) - 120, Value(1)
        )
        ordering.insert(0, "-matches")
    return qs.annotate(sample=Substr("body", sample_start, SAMPLE_LENGTH)).order_by(
        *ordering
    )[:limit]


@never_cache
@require_GET
def inbox(request):
    if response := _canonical_redirect(request):
        return response

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(days=1)
    query = request.GET.get("q", "").strip()[:200]
    approval = request.GET.get("approval", "all")
    if approval not in ("all", "unapproved", "approved"):
        approval = "all"
    activity = request.GET.get("activity", "all")
    if activity not in ("all", "new", "edited", "burst"):
        activity = "all"

    posts = models.Post.objects.filter(owner_id=OuterRef("pk"))
    pages = models.Page.objects.filter(owner_id=OuterRef("pk"))
    images = models.Image.objects.filter(owner_id=OuterRef("pk"))
    edited_posts = posts.filter(
        updated_at__gte=week_ago,
        # auto_now timestamps differ slightly even on the initial insert.
        updated_at__gt=F("created_at") + timedelta(seconds=1),
    )
    edited_pages = pages.filter(
        updated_at__gte=week_ago,
        updated_at__gt=F("created_at") + timedelta(seconds=1),
    )
    users = models.User.objects.annotate(
        recently_edited=Exists(edited_posts) | Exists(edited_pages),
        posts_today=Coalesce(
            Subquery(
                posts.filter(created_at__gte=day_ago)
                .order_by()
                .values("owner_id")
                .annotate(total=Count("pk"))
                .values("total")
            ),
            0,
        ),
        last_activity=Greatest(
            Coalesce(_latest(posts, "updated_at"), F("date_joined")),
            Coalesce(_latest(pages, "updated_at"), F("date_joined")),
            Coalesce(_latest(images, "uploaded_at"), F("date_joined")),
        ),
    ).filter(
        Exists(posts)
        | Exists(pages)
        | Exists(images)
        | (Q(blog_byline__isnull=False) & ~Q(blog_byline=""))
        | (Q(redirect_domain__isnull=False) & ~Q(redirect_domain=""))
        | (Q(custom_domain__isnull=False) & ~Q(custom_domain=""))
        | (
            Q(footer_note__isnull=False)
            & ~Q(footer_note="")
            & ~Q(footer_note=models.User._meta.get_field("footer_note").get_default())
        )
    )
    if approval != "all":
        users = users.filter(is_approved=approval == "approved")
    if activity == "new":
        users = users.filter(date_joined__gte=week_ago)
    elif activity == "edited":
        users = users.filter(recently_edited=True)
    elif activity == "burst":
        users = users.filter(posts_today__gte=20)
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(blog_title__icontains=query)
            | Q(blog_byline__icontains=query)
            | Q(footer_note__icontains=query)
            | Q(custom_domain__icontains=query)
            | Q(redirect_domain__icontains=query)
            | Exists(posts.filter(Q(title__icontains=query) | Q(body__icontains=query)))
            | Exists(pages.filter(Q(title__icontains=query) | Q(body__icontains=query)))
        )

    # Fetch one extra row instead of counting the entire search result on every request.
    try:
        page = max(1, min(int(request.GET.get("page", 1)), 10000))
    except ValueError:
        page = 1
    start = (page - 1) * PAGE_SIZE
    users = list(
        users.order_by("-last_activity", "-id")
        .only(
            "id",
            "username",
            "blog_title",
            "date_joined",
            "is_approved",
            "is_premium",
            "custom_domain",
            "redirect_domain",
        )
        .annotate(
            byline_sample=Substr("blog_byline", 1, SAMPLE_LENGTH),
            footer_sample=Substr("footer_note", 1, SAMPLE_LENGTH),
        )[start : start + PAGE_SIZE + 1]
    )
    has_next = len(users) > PAGE_SIZE
    users = users[:PAGE_SIZE]
    prefetch_related_objects(
        users,
        Prefetch(
            "post_set",
            queryset=_sample_queryset(models.Post, query, 3),
            to_attr="sample_posts",
        ),
        Prefetch(
            "page_set",
            queryset=_sample_queryset(models.Page, query, 2),
            to_attr="sample_pages",
        ),
    )
    counts = {}
    for model, key in (
        (models.Post, "posts"),
        (models.Page, "pages"),
        (models.Image, "images"),
    ):
        counts[key] = dict(
            model.objects.filter(owner_id__in=[u.pk for u in users])
            .order_by()
            .values("owner_id")
            .annotate(total=Count("pk"))
            .values_list("owner_id", "total")
        )
    for user in users:
        user.content_counts = {
            key: values.get(user.pk, 0) for key, values in counts.items()
        }
        domains = set()
        user.samples = []
        for kind, items in (("post", user.sample_posts), ("page", user.sample_pages)):
            for item in items:
                preview = ContentPreview(item.sample, user.blog_absolute_url)
                domains.update(preview.domains)
                user.samples.append(
                    {
                        "kind": kind,
                        "item": item,
                        "excerpt": preview.text[:350],
                        "url": reverse(
                            "moderation_inbox_preview", args=[kind, item.pk]
                        ),
                    }
                )
        byline = ContentPreview(user.byline_sample, user.blog_absolute_url)
        footer_body = user.footer_sample
        if footer_body == models.User._meta.get_field("footer_note").get_default():
            footer_body = ""
        footer = ContentPreview(footer_body, user.blog_absolute_url)
        user.byline_excerpt = byline.text[:350]
        domains.update(byline.domains | footer.domains)
        domains.update(filter(None, [user.custom_domain, user.redirect_domain]))
        user.link_domains = sorted(domains)
        user.signals = []
        if user.date_joined >= week_ago:
            user.signals.append("Joined in the last 7 days")
        if user.recently_edited:
            user.signals.append("Edited content in the last 7 days")
        if user.posts_today >= 20:
            user.signals.append(
                f"{user.posts_today} posts created in the last 24 hours"
            )

    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "main/moderation_inbox.html",
        {
            "users": users,
            "query": query,
            "approval": approval,
            "activity": activity,
            "page": page,
            "previous_page": page - 1,
            "next_page": page + 1,
            "has_next": has_next,
            "querystring": params.urlencode(),
        },
    )


@never_cache
@require_GET
def preview(request, kind, object_id):
    if response := _canonical_redirect(request):
        return response
    model = {"post": models.Post, "page": models.Page, "profile": models.User}.get(kind)
    if model is None:
        raise Http404()
    fields = ("blog_byline", "footer_note") if kind == "profile" else ("body",)
    qs = model.objects.defer(*fields)
    for field in fields:
        qs = qs.annotate(
            **{
                f"{field}_preview": Substr(field, 1, PREVIEW_LENGTH),
                f"{field}_length": Length(field),
            }
        )
    if kind != "profile":
        qs = qs.select_related("owner")
    obj = get_object_or_404(qs, pk=object_id)
    owner = obj if kind == "profile" else obj.owner
    base_url = owner.blog_absolute_url
    if kind != "profile":
        base_url = scheme.get_protocol() + obj.get_absolute_url()
    sections = []
    for field in fields:
        body = getattr(obj, f"{field}_preview") or ""
        parsed = ContentPreview(body, base_url)
        sections.append(
            {
                "name": field.replace("_", " ").capitalize(),
                "body": body,
                "truncated": (getattr(obj, f"{field}_length") or 0) > PREVIEW_LENGTH,
                "domains": sorted(parsed.domains),
                "images": parsed.images[:20],
            }
        )
    uploads = []
    if kind == "profile":
        uploads = list(
            owner.image_set.defer("data").order_by("-uploaded_at", "-id")[:12]
        )
    return render(
        request,
        "main/moderation_inbox_preview.html",
        {
            "sections": sections,
            "uploads": uploads,
            "kind": kind,
        },
    )
