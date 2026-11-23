from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from main import blog_classification, models, moderation_campaigns, promotional_spam
from main import moderation_bulk as bulk
from main.views.moderation_campaigns import create_selection
from main.views.moderation_inbox import _canonical_redirect

MAX_SELECTED = 50000


@never_cache
@require_GET
def index(request):
    if response := _canonical_redirect(request):
        return response
    values = bulk.filters(request.GET)
    keys = bulk.strong_keys()
    qs = bulk.findings(values, keys)
    page_obj = Paginator(
        qs.select_related("owner").order_by("-owner_id"), bulk.PAGE_SIZE
    ).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "main/moderation_bulk.html",
        {
            "page_obj": page_obj,
            "rows": bulk.rows(page_obj, keys),
            "values": values,
            "eligible": qs.exclude(owner__in=bulk.protected_owners()).count(),
            "categories": blog_classification.CATEGORIES.items(),
            "recommendations": moderation_campaigns.RECOMMENDATIONS.items(),
            "querystring": params.urlencode(),
            "active_days": bulk.ACTIVE_DAYS,
        },
    )


@never_cache
@require_POST
def prepare(request):
    if response := _canonical_redirect(request):
        return response
    current = models.PromotionalSpamFinding.objects.filter(
        rule_version=promotional_spam.VERSION
    )
    if request.POST.get("scope") == "matching":
        candidates = bulk.findings(bulk.filters(request.POST))
        title = "Bulk selection: all blogs matching filters"
    else:
        try:
            ids = {
                int(value)
                for value in request.POST.get("ids", "").split(",")
                if value.strip()
            }
        except ValueError:
            return HttpResponse("Invalid selection.", status=400)
        if not ids:
            return HttpResponse("Select at least one blog.", status=400)
        if len(ids) > MAX_SELECTED:
            return HttpResponse(
                f"Select at most {MAX_SELECTED} blogs at a time.", status=400
            )
        candidates = current.filter(owner_id__in=ids)
        title = (
            f"Bulk selection: {len(ids)} selected blog{'s' if len(ids) != 1 else ''}"
        )
    candidates = candidates.exclude(owner__in=bulk.protected_owners())
    return create_selection(request.user, title, candidates)
