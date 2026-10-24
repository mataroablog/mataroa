"""Inspect one account's posting volume and content without loading its archive."""

from collections import Counter
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Min, Q, Value, Window
from django.db.models.functions import (
    Greatest,
    Lower,
    RowNumber,
    StrIndex,
    Substr,
    TruncDate,
)
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from main import forms, models
from main.views.moderation_inbox import (
    SAMPLE_LENGTH,
    ContentPreview,
    _canonical_redirect,
)


@never_cache
@require_GET
def inspect(request, user_id):
    if response := _canonical_redirect(request):
        return response

    owner = get_object_or_404(models.User, pk=user_id)
    posts = models.Post.objects.filter(owner=owner)
    today = timezone.now().date()
    stats = posts.aggregate(
        total=Count("pk"),
        published=Count("pk", filter=Q(published_at__lte=today)),
        drafts=Count("pk", filter=Q(published_at__isnull=True)),
        scheduled=Count("pk", filter=Q(published_at__gt=today)),
        first_created=Min("created_at"),
        last_created=Max("created_at"),
    )
    busiest_days = list(
        posts.order_by()
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("pk"))
        .order_by("-total", "-day")[:10]
    )
    for day in busiest_days:
        day["url"] = "?" + urlencode(
            {"start": day["day"], "end": day["day"], "view": "oldest"}
        )
        day["percent"] = round(day["total"] / busiest_days[0]["total"] * 100)

    params = request.GET.copy()
    if not params.get("view"):
        params["view"] = "sample"
    form = forms.ModerationPostFilterForm(params)
    filtered = posts
    query = ""
    mode = "sample"
    if form.is_valid():
        values = form.cleaned_data
        query = values["q"]
        mode = values["view"] or "sample"
        if query:
            filtered = filtered.filter(
                Q(title__icontains=query) | Q(body__icontains=query)
            )
        if values["start"]:
            filtered = filtered.filter(created_at__date__gte=values["start"])
        if values["end"]:
            filtered = filtered.filter(created_at__date__lte=values["end"])
        if values["status"] == "published":
            filtered = filtered.filter(published_at__lte=today)
        elif values["status"] == "draft":
            filtered = filtered.filter(published_at__isnull=True)
        elif values["status"] == "scheduled":
            filtered = filtered.filter(published_at__gt=today)
    else:
        filtered = posts.none()

    start = Value(1)
    if query:
        start = Greatest(StrIndex(Lower("body"), Value(query.lower())) - 120, Value(1))
    filtered = filtered.defer("body").annotate(
        excerpt_source=Substr("body", start, SAMPLE_LENGTH)
    )
    matching_count = filtered.count()
    page_obj = None
    if mode == "sample":
        # Equally spaced positions in creation order include both ends of the archive.
        # Window filtering happens in SQL, so at most 12 excerpts reach Python.
        size = min(12, matching_count)
        positions = [
            1 + i * (matching_count - 1) // max(size - 1, 1) for i in range(size)
        ]
        selected = list(
            filtered.annotate(
                position=Window(
                    RowNumber(), order_by=[F("created_at").asc(), F("id").asc()]
                )
            )
            .filter(position__in=positions)
            .order_by("created_at", "id")
        )
    else:
        ordering = {
            "newest": ("-created_at", "-id"),
            "oldest": ("created_at", "id"),
            "edited": ("-updated_at", "-id"),
        }[mode]
        paginator = Paginator(filtered.order_by(*ordering), 25)
        # Reuse the count above instead of running another archive count.
        paginator.count = matching_count
        page_obj = paginator.get_page(request.GET.get("page"))
        selected = list(page_obj.object_list)

    domains = Counter()
    for post in selected:
        post.owner = owner
        preview = ContentPreview(post.excerpt_source, owner.blog_absolute_url)
        post.excerpt = preview.text[:500]
        domains.update(preview.domains)

    params.pop("page", None)
    browse_params = params.copy()
    browse_params["view"] = "newest"
    return render(
        request,
        "main/moderation_post_inspect.html",
        {
            "owner": owner,
            "stats": stats,
            "busiest_days": busiest_days,
            "form": form,
            "posts": selected,
            "mode": mode,
            "matching_count": matching_count,
            "page_obj": page_obj,
            "querystring": params.urlencode(),
            "browse_url": "?" + browse_params.urlencode(),
            "domains": domains.most_common(20),
        },
    )
