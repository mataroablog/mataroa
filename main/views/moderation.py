from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Avg, Count, F, Max, Q, Sum
from django.db.models.expressions import Func
from django.db.models.functions import (
    Coalesce,
    Length,
    TruncDay,
    TruncMonth,
    TruncWeek,
)
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from main import models


def index(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    return render(request, "main/moderation_index.html")


def user_list(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # build base queryset
    user_qs = models.User.objects.all()

    # handle filters via mode param
    mode_param = request.GET.get("mode")
    if mode_param:
        mode = mode_param.split(",")
        if "noapprove" in mode:
            user_qs = user_qs.filter(is_approved=False)
        if "noempty" in mode:
            user_qs = user_qs.annotate(count=Count("post")).filter(count__gt=0)
        if "premium" in mode:
            user_qs = user_qs.filter(is_premium=True)
        # ordering
        if "reverse" in mode:
            user_qs = user_qs.order_by("id")
        else:
            user_qs = user_qs.order_by("-id")
    else:
        # default ordering for simple case
        user_qs = user_qs.order_by("-id")
    current_modes = mode if mode_param else []

    # prefetch posts for listed users
    user_qs = user_qs.prefetch_related("post_set")

    # pagination
    per_page_default = 100
    try:
        per_page = int(request.GET.get("per_page", per_page_default))
    except (TypeError, ValueError):
        per_page = per_page_default
    paginator = Paginator(user_qs, per_page)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # preserve existing non-page query params in pagination links
    params = request.GET.copy()
    params.pop("page", None)
    querystring = params.urlencode()

    # Build clickable filter links
    def link_for_modes(modes_list: list[str]) -> str:
        query: dict[str, str] = {}
        # preserve per_page
        if per_page != per_page_default:
            query["per_page"] = str(per_page)
        if modes_list:
            query["mode"] = ",".join(modes_list)
        return f"?{urlencode(query)}" if query else "?"

    all_filter_keys = ["noapprove", "noempty", "premium", "reverse"]
    filters = []
    for key in all_filter_keys:
        is_active = key in current_modes
        if is_active:
            new_modes = [m for m in current_modes if m != key]
        else:
            new_modes = current_modes + [key]
        filters.append(
            {
                "key": key,
                "active": is_active,
                "url": link_for_modes(new_modes),
            }
        )
    clear_filters_url = link_for_modes([])

    return render(
        request,
        "main/moderation_user_list.html",
        {
            "page_obj": page_obj,
            "paginator": paginator,
            "is_paginated": paginator.num_pages > 1,
            "user_list": page_obj.object_list,
            "querystring": querystring,
            "filters": filters,
            "clear_filters_url": clear_filters_url,
            "DEBUG": "true" if settings.DEBUG else "false",
        },
    )


def user_delete(request, user_id):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    user = get_object_or_404(models.User, id=user_id)
    if request.method == "POST":
        user.delete()
        return JsonResponse({"ok": True})

    raise Http404()


def user_approve(request, user_id):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    user = get_object_or_404(models.User, id=user_id)
    if request.method == "POST":
        user.is_approved = True
        user.save()
        return JsonResponse({"ok": True})

    raise Http404()


def user_unapprove(request, user_id):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    user = get_object_or_404(models.User, id=user_id)
    if request.method == "POST":
        user.is_approved = False
        user.save()
        return JsonResponse({"ok": True})

    raise Http404()


def images_leaderboard(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # determine sort mode from querystring similar to moderation users page
    mode_param = request.GET.get("mode")
    current_modes: list[str] = mode_param.split(",") if mode_param else []

    sort_by_mb = "bymb" in current_modes
    reverse = "reverse" in current_modes

    users_with_counts = models.User.objects.annotate(
        image_count=Count("image"),
        image_bytes=Sum(Func(F("image__data"), function="octet_length")),
    ).filter(image_count__gt=0)

    if sort_by_mb:
        ordering = ["image_bytes", "id"] if reverse else ["-image_bytes", "-id"]
    else:
        ordering = ["image_count", "id"] if reverse else ["-image_count", "-id"]

    users_with_counts = users_with_counts.order_by(*ordering)

    # Keep it lightweight; show top 200 by default
    top_limit = 200
    user_list = list(users_with_counts[:top_limit])
    for u in user_list:
        total_bytes = u.image_bytes or 0
        u.image_megabytes = round(total_bytes / (1024 * 1024), 2)

    # build filter links
    def link_for_modes(modes_list: list[str]) -> str:
        query: dict[str, str] = {}
        if modes_list:
            query["mode"] = ",".join(modes_list)
        from urllib.parse import urlencode as _urlencode  # local alias

        return f"?{_urlencode(query)}" if query else "?"

    # build filters ensuring only one of byimages/bymb is active at once
    filters: list[dict[str, str | bool]] = []
    filters.append(
        {
            "key": "byimages",
            "active": "byimages" in current_modes or (not current_modes),
            "url": link_for_modes(
                [m for m in current_modes if m not in ["byimages", "bymb"]]
                + ["byimages"]
            ),
        }
    )
    # bymb
    filters.append(
        {
            "key": "bymb",
            "active": "bymb" in current_modes,
            "url": link_for_modes(
                [m for m in current_modes if m not in ["byimages", "bymb"]] + ["bymb"]
            ),
        }
    )
    # reverse toggle
    filters.append(
        {
            "key": "reverse",
            "active": "reverse" in current_modes,
            "url": link_for_modes(
                [m for m in current_modes if m != "reverse"]
                if "reverse" in current_modes
                else current_modes + ["reverse"]
            ),
        }
    )
    clear_filters_url = link_for_modes([])

    return render(
        request,
        "main/moderation_images.html",
        {
            "user_list": user_list,
            "top_limit": top_limit,
            "filters": filters,
            "clear_filters_url": clear_filters_url,
        },
    )


def posts_leaderboard(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # sort modes
    mode_param = request.GET.get("mode")
    current_modes: list[str] = mode_param.split(",") if mode_param else []

    # default sort is by total posts desc
    sort_key = "byposts"
    if "bypublished" in current_modes:
        sort_key = "bypublished"
    elif "bydrafts" in current_modes:
        sort_key = "bydrafts"
    reverse = "reverse" in current_modes

    users_with_post_stats = models.User.objects.annotate(
        posts_total=Count("post"),
        posts_published=Count("post", filter=Q(post__published_at__isnull=False)),
        posts_drafts=Count("post", filter=Q(post__published_at__isnull=True)),
        last_post_date=Max("post__published_at"),
    ).filter(posts_total__gt=0)

    if sort_key == "bypublished":
        ordering = ["posts_published", "id"] if reverse else ["-posts_published", "-id"]
    elif sort_key == "bydrafts":
        ordering = ["posts_drafts", "id"] if reverse else ["-posts_drafts", "-id"]
    else:  # byposts
        ordering = ["posts_total", "id"] if reverse else ["-posts_total", "-id"]

    users_with_post_stats = users_with_post_stats.order_by(*ordering)

    # limit (configurable via ?limit=)
    default_limit = 200
    try:
        top_limit = int(request.GET.get("limit", default_limit))
    except (TypeError, ValueError):
        top_limit = default_limit
    if top_limit <= 0:
        top_limit = default_limit
    user_list = list(users_with_post_stats[:top_limit])

    # filters
    def link_for_modes(modes_list: list[str]) -> str:
        query: dict[str, str] = {}
        if modes_list:
            query["mode"] = ",".join(modes_list)
        from urllib.parse import urlencode as _urlencode  # local alias

        return f"?{_urlencode(query)}" if query else "?"

    filters: list[dict[str, str | bool]] = []
    # byposts
    filters.append(
        {
            "key": "byposts",
            "active": ("byposts" in current_modes)
            or (not current_modes)
            or not ("bypublished" in current_modes or "bydrafts" in current_modes),
            "url": link_for_modes(
                [
                    m
                    for m in current_modes
                    if m not in ["byposts", "bypublished", "bydrafts"]
                ]
                + ["byposts"]
            ),
        }
    )
    # bypublished
    filters.append(
        {
            "key": "bypublished",
            "active": "bypublished" in current_modes,
            "url": link_for_modes(
                [
                    m
                    for m in current_modes
                    if m not in ["byposts", "bypublished", "bydrafts"]
                ]
                + ["bypublished"]
            ),
        }
    )
    # bydrafts
    filters.append(
        {
            "key": "bydrafts",
            "active": "bydrafts" in current_modes,
            "url": link_for_modes(
                [
                    m
                    for m in current_modes
                    if m not in ["byposts", "bypublished", "bydrafts"]
                ]
                + ["bydrafts"]
            ),
        }
    )
    # reverse
    filters.append(
        {
            "key": "reverse",
            "active": "reverse" in current_modes,
            "url": link_for_modes(
                [m for m in current_modes if m != "reverse"]
                if "reverse" in current_modes
                else current_modes + ["reverse"]
            ),
        }
    )
    clear_filters_url = link_for_modes([])

    return render(
        request,
        "main/moderation_posts.html",
        {
            "user_list": user_list,
            "top_limit": top_limit,
            "filters": filters,
            "clear_filters_url": clear_filters_url,
        },
    )


def stats(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # Users
    total_users = models.User.objects.count()
    approved_users = models.User.objects.filter(is_approved=True).count()
    premium_users = models.User.objects.filter(is_premium=True).count()
    users_with_custom_domain = (
        models.User.objects.exclude(custom_domain__isnull=True)
        .exclude(custom_domain__exact="")
        .count()
    )

    # Posts
    total_posts = models.Post.objects.count()
    published_posts = models.Post.objects.filter(published_at__isnull=False).count()
    draft_posts = models.Post.objects.filter(published_at__isnull=True).count()
    latest_post_date = models.Post.objects.aggregate(last=Max("published_at"))

    # Pages
    total_pages = models.Page.objects.count()

    # Images
    image_stats = models.Image.objects.aggregate(
        count=Count("id"),
        total_bytes=Sum(Func(F("data"), function="octet_length")),
    )
    total_images = image_stats["count"] or 0
    total_image_megabytes = round((image_stats["total_bytes"] or 0) / (1024 * 1024), 2)

    # Comments
    total_comments = models.Comment.objects.count()
    approved_comments = models.Comment.objects.filter(is_approved=True).count()

    # Notifications (subscribers) and sends
    total_subscribers = models.Notification.objects.count()
    active_subscribers = models.Notification.objects.filter(is_active=True).count()
    total_sends = models.NotificationRecord.objects.count()

    # Snapshots
    total_snapshots = models.Snapshot.objects.count()

    # Averages
    avg_posts_per_user = models.User.objects.annotate(c=Count("post")).aggregate(
        avg=Avg("c")
    )
    avg_posts_per_user_val = round(float(avg_posts_per_user["avg"] or 0), 2)

    context = {
        "totals": {
            "users": total_users,
            "users_approved": approved_users,
            "users_premium": premium_users,
            "users_with_custom_domain": users_with_custom_domain,
            "posts": total_posts,
            "posts_published": published_posts,
            "posts_draft": draft_posts,
            "pages": total_pages,
            "images": total_images,
            "images_mb": total_image_megabytes,
            "comments": total_comments,
            "comments_approved": approved_comments,
            "subscribers": total_subscribers,
            "subscribers_active": active_subscribers,
            "notification_sends": total_sends,
            "snapshots": total_snapshots,
        },
        "averages": {
            "posts_per_user": avg_posts_per_user_val,
        },
        "latest": {
            "last_post_date": latest_post_date["last"],
        },
        # leave heavy sections to dedicated pages for performance
    }

    return render(request, "main/moderation_stats.html", context)


def activity(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    now = timezone.now()
    d90 = now - timedelta(days=90)

    # New users/posts per day/week/month
    new_users_daily_qs = (
        models.User.objects.filter(date_joined__gte=d90)
        .annotate(period=TruncDay("date_joined"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )
    new_posts_daily_qs = (
        models.Post.objects.filter(created_at__gte=d90)
        .annotate(period=TruncDay("created_at"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )
    new_users_weekly_qs = (
        models.User.objects.filter(date_joined__gte=now - timedelta(weeks=26))
        .annotate(period=TruncWeek("date_joined"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )
    new_posts_weekly_qs = (
        models.Post.objects.filter(created_at__gte=now - timedelta(weeks=26))
        .annotate(period=TruncWeek("created_at"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )

    # helper to build cumulative series
    def cumulative_points(qs):
        points = []
        total = 0
        for row in qs:
            total += row["count"]
            points.append({"period": row["period"], "cumulative": total})
        return points

    cum_users_daily = cumulative_points(new_users_daily_qs)
    cum_posts_daily = cumulative_points(new_posts_daily_qs)

    # Prepare SVG bar chart data (similar style to transparency page)
    def prepare_chart(rows_qs, limit):
        rows = list(rows_qs)
        if not rows:
            return []
        # take the last N items (most recent)
        subset = rows[-limit:] if len(rows) > limit else rows
        # scale within the subset
        highest = max((r["count"] for r in subset), default=0) or 1
        chart = []
        x_offset = 0
        # iterate in chronological order (oldest on the left, newest on the right)
        for r in subset:
            c = r["count"] or 0
            count_percent = 1
            if highest and c:
                count_percent = (c * 100) / highest
                if count_percent < 1:
                    count_percent = 1
            chart.append(
                {
                    "period": r["period"],
                    "count": c,
                    "x_offset": x_offset,
                    "count_percent": count_percent,
                    "negative_count_percent": 100 - count_percent,
                }
            )
            x_offset += 20
        return chart

    chart_new_users_daily = prepare_chart(new_users_daily_qs, limit=20)
    chart_new_posts_daily = prepare_chart(new_posts_daily_qs, limit=20)
    chart_new_users_weekly = prepare_chart(new_users_weekly_qs, limit=12)
    chart_new_posts_weekly = prepare_chart(new_posts_weekly_qs, limit=12)

    # dynamic widths so charts don't leave big empty space at the end
    chart_new_users_daily_width = max(len(chart_new_users_daily) * 20, 1)
    chart_new_posts_daily_width = max(len(chart_new_posts_daily) * 20, 1)
    chart_new_users_weekly_width = max(len(chart_new_users_weekly) * 20, 1)
    chart_new_posts_weekly_width = max(len(chart_new_posts_weekly) * 20, 1)

    # Cumulative posts by month from 1 May 2020 (counts based on created_at)
    start_month = date(2020, 5, 1)
    # aggregate counts per month for posts created after start_month
    monthly_counts_qs = (
        models.Post.objects.filter(created_at__gte=start_month)
        .annotate(period=TruncMonth("created_at"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )
    # map first-of-month -> count for that month
    monthly_counts_map: dict[date, int] = {}
    for row in monthly_counts_qs:
        period_dt = row["period"]
        month_key = date(period_dt.year, period_dt.month, 1)
        monthly_counts_map[month_key] = row["count"] or 0

    # generate continuous month sequence up to current month
    today = timezone.now().date()
    end_month = date(today.year, today.month, 1)

    def _add_one_month(d: date) -> date:
        if d.month == 12:
            return date(d.year + 1, 1, 1)
        return date(d.year, d.month + 1, 1)

    cumulative_posts_monthly: list[dict[str, object]] = []
    running_total = 0
    cursor = start_month
    while cursor <= end_month:
        running_total += monthly_counts_map.get(cursor, 0)
        cumulative_posts_monthly.append({"period": cursor, "cumulative": running_total})
        cursor = _add_one_month(cursor)

    # Build chart data for cumulative monthly posts
    if cumulative_posts_monthly:
        highest_cum = max(r["cumulative"] for r in cumulative_posts_monthly) or 1
    else:
        highest_cum = 1
    chart_cumulative_posts_monthly: list[dict[str, object]] = []
    x_offset = 0
    for r in cumulative_posts_monthly:
        v = r["cumulative"] or 0
        count_percent = 1
        if highest_cum and v:
            count_percent = (v * 100) / highest_cum
            if count_percent < 1:
                count_percent = 1
        chart_cumulative_posts_monthly.append(
            {
                "period": r["period"],
                "count": v,
                "x_offset": x_offset,
                "count_percent": count_percent,
                "negative_count_percent": 100 - count_percent,
            }
        )
        x_offset += 20
    chart_cumulative_posts_monthly_width = max(
        len(chart_cumulative_posts_monthly) * 20, 1
    )

    context = {
        "cum_users_daily": cum_users_daily,
        "cum_posts_daily": cum_posts_daily,
        "chart_new_users_daily": chart_new_users_daily,
        "chart_new_posts_daily": chart_new_posts_daily,
        "chart_new_users_weekly": chart_new_users_weekly,
        "chart_new_posts_weekly": chart_new_posts_weekly,
        "chart_new_users_daily_width": chart_new_users_daily_width,
        "chart_new_posts_daily_width": chart_new_posts_daily_width,
        "chart_new_users_weekly_width": chart_new_users_weekly_width,
        "chart_new_posts_weekly_width": chart_new_posts_weekly_width,
        "cumulative_posts_monthly_from_2020": cumulative_posts_monthly,
        "chart_cumulative_posts_monthly": chart_cumulative_posts_monthly,
        "chart_cumulative_posts_monthly_width": chart_cumulative_posts_monthly_width,
    }
    return render(request, "main/moderation_activity.html", context)


def cohorts(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    now = timezone.now()
    d30 = now - timedelta(days=30)

    most_published_30 = list(
        models.User.objects.annotate(
            cnt=Count(
                "post",
                filter=Q(
                    post__published_at__isnull=False, post__published_at__gte=d30.date()
                ),
            )
        )
        .filter(cnt__gt=0)
        .order_by("-cnt", "-id")
        .values("id", "username", "cnt")[:20]
    )
    largest_blogs_by_bytes = list(
        models.User.objects.annotate(total_bytes=Sum(Coalesce(Length("post__body"), 0)))
        .filter(total_bytes__gt=0)
        .order_by("-total_bytes", "-id")
        .values("id", "username", "total_bytes")[:20]
    )

    context = {
        "leaders": {
            "most_published_30": most_published_30,
            "largest_blogs_by_bytes": largest_blogs_by_bytes,
        },
        "CANONICAL_HOST": settings.CANONICAL_HOST,
    }
    return render(request, "main/moderation_cohorts.html", context)


def summary(request, date_str: str):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError) as err:
        raise Http404() from err

    prev_date = target_date - timedelta(days=1)
    next_date = target_date + timedelta(days=1)

    new_users_qs = models.User.objects.filter(date_joined__date=target_date).order_by(
        "-id"
    )
    new_posts_qs = (
        models.Post.objects.filter(created_at__date=target_date)
        .select_related("owner")
        .order_by("-created_at")
    )
    new_pages_qs = (
        models.Page.objects.filter(created_at__date=target_date)
        .select_related("owner")
        .order_by("-created_at")
    )
    new_comments_qs = (
        models.Comment.objects.filter(created_at__date=target_date)
        .select_related("post", "post__owner")
        .order_by("-created_at")
    )

    post_visits_count = models.AnalyticPost.objects.filter(
        created_at__date=target_date
    ).count()
    top_posts_by_visits_qs = (
        models.Post.objects.filter(analyticpost__created_at__date=target_date)
        .annotate(
            visit_count=Count(
                "analyticpost", filter=Q(analyticpost__created_at__date=target_date)
            )
        )
        .select_related("owner")
        .order_by("-visit_count", "-id")
    )

    context = {
        "target_date": target_date,
        "prev_date": prev_date,
        "next_date": next_date,
        "counts": {
            "users": new_users_qs.count(),
            "posts": new_posts_qs.count(),
            "pages": new_pages_qs.count(),
            "comments": new_comments_qs.count(),
            "post_visits": post_visits_count,
        },
        "new_users": list(new_users_qs),
        "new_posts": list(new_posts_qs),
        "new_pages": list(new_pages_qs),
        "new_comments": list(new_comments_qs),
        "top_posts_by_visits": list(top_posts_by_visits_qs[:20]),
    }

    return render(request, "main/moderation_summary.html", context)


def top_posts_alltime(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # limit (configurable via ?limit=)
    default_limit = 100
    try:
        top_limit = int(request.GET.get("limit", default_limit))
    except (TypeError, ValueError):
        top_limit = default_limit
    if top_limit <= 0:
        top_limit = default_limit

    # Aggregate from AnalyticPost directly - faster than annotating all Posts
    visits_by_post = (
        models.AnalyticPost.objects.values("post_id")
        .annotate(visit_count=Count("id"))
        .order_by("-visit_count")[:top_limit]
    )

    # Build lookup of post_id -> visit_count
    post_ids = []
    visit_counts = {}
    for row in visits_by_post:
        post_id = row["post_id"]
        post_ids.append(post_id)
        visit_counts[post_id] = row["visit_count"]

    # Fetch posts with owners
    posts = models.Post.objects.filter(id__in=post_ids).select_related("owner")

    # Attach visit counts and sort by visits desc
    post_list = []
    for p in posts:
        p.visit_count = visit_counts.get(p.id, 0)
        post_list.append(p)
    post_list.sort(key=lambda x: (-x.visit_count, -x.id))

    # total visits count
    total_visits = models.AnalyticPost.objects.count()

    context = {
        "post_list": post_list,
        "top_limit": top_limit,
        "total_visits": total_visits,
    }

    return render(request, "main/moderation_top_posts.html", context)


def top_blogs_alltime(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # limit (configurable via ?limit=)
    default_limit = 100
    try:
        top_limit = int(request.GET.get("limit", default_limit))
    except (TypeError, ValueError):
        top_limit = default_limit
    if top_limit <= 0:
        top_limit = default_limit

    # Aggregate from AnalyticPost directly - much faster than joining through User
    visits_by_owner = (
        models.AnalyticPost.objects.values("post__owner_id")
        .annotate(visit_count=Count("id"))
        .order_by("-visit_count")[:top_limit]
    )

    # Build lookup of owner_id -> visit_count
    owner_ids = []
    visit_counts = {}
    for row in visits_by_owner:
        owner_id = row["post__owner_id"]
        owner_ids.append(owner_id)
        visit_counts[owner_id] = row["visit_count"]

    # Fetch users
    users = models.User.objects.filter(id__in=owner_ids)

    # Attach visit counts and sort by visits desc
    user_list = []
    for u in users:
        u.visit_count = visit_counts.get(u.id, 0)
        user_list.append(u)
    user_list.sort(key=lambda x: (-x.visit_count, -x.id))

    # total visits count
    total_visits = models.AnalyticPost.objects.count()

    context = {
        "user_list": user_list,
        "top_limit": top_limit,
        "total_visits": total_visits,
    }

    return render(request, "main/moderation_top_blogs.html", context)


def top_pages_alltime(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise Http404()

    if hasattr(request, "subdomain"):
        return redirect(f"//{settings.CANONICAL_HOST}{request.path}")

    # limit (configurable via ?limit=)
    default_limit = 100
    try:
        top_limit = int(request.GET.get("limit", default_limit))
    except (TypeError, ValueError):
        top_limit = default_limit
    if top_limit <= 0:
        top_limit = default_limit

    # Aggregate from AnalyticPage by user and path
    visits_by_page = (
        models.AnalyticPage.objects.values("user_id", "path")
        .annotate(visit_count=Count("id"))
        .order_by("-visit_count")[:top_limit]
    )

    # Build list with user info
    user_ids = set(row["user_id"] for row in visits_by_page)
    users = {u.id: u for u in models.User.objects.filter(id__in=user_ids)}

    page_list = []
    for row in visits_by_page:
        user = users.get(row["user_id"])
        if user:
            page_list.append(
                {
                    "user": user,
                    "path": row["path"],
                    "visit_count": row["visit_count"],
                }
            )

    # total page visits count
    total_visits = models.AnalyticPage.objects.count()

    context = {
        "page_list": page_list,
        "top_limit": top_limit,
        "total_visits": total_visits,
    }

    return render(request, "main/moderation_top_pages.html", context)
