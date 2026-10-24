from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from main import models, promotional_spam
from main.views.moderation_inbox import _canonical_redirect


@never_cache
@require_GET
def queue(request):
    if response := _canonical_redirect(request):
        return response
    status = request.GET.get("status", "pending")
    if status not in ("pending", "dismissed"):
        status = "pending"
    findings = models.PromotionalSpamFinding.objects.filter(
        score__gte=promotional_spam.THRESHOLD
    )
    findings = findings.filter(dismissed_at__isnull=status == "pending")
    group_kind = request.GET.get("kind", "")
    group_key = request.GET.get("group", "")[:253]
    if group_kind in ("domain", "template") and group_key:
        findings = findings.filter(groups__kind=group_kind, groups__key=group_key)
    else:
        group_kind = group_key = ""
    groups = list(
        models.PromotionalSpamGroup.objects.filter(finding__in=findings)
        .values("kind", "key")
        .annotate(blogs=Count("finding", distinct=True))
        .filter(blogs__gte=2)
        .order_by("-blogs", "kind", "key")[:20]
    )
    page_obj = Paginator(
        findings.select_related("owner").order_by("-score", "-scanned_at", "-pk"), 25
    ).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "main/moderation_spam.html",
        {
            "page_obj": page_obj,
            "status": status,
            "groups": groups,
            "group_kind": group_kind,
            "group_key": group_key,
            "querystring": params.urlencode(),
            "return_query": request.GET.urlencode(),
            "scan": models.PromotionalSpamScan.objects.filter(pk=1).first(),
        },
    )


@never_cache
@require_POST
def dismiss(request, user_id):
    if response := _canonical_redirect(request):
        return response
    with transaction.atomic():
        finding = get_object_or_404(
            models.PromotionalSpamFinding.objects.select_for_update(), owner_id=user_id
        )
        if request.POST.get("revision") != finding.source_signature:
            return HttpResponse(
                "This finding changed since you opened it. Reload the queue before reviewing it.",
                status=409,
            )
        if request.POST.get("action") == "restore":
            finding.dismissed_at = None
            finding.dismissed_baseline = {}
        else:
            finding.dismissed_at = timezone.now()
            finding.dismissed_baseline = promotional_spam.baseline(
                finding.score, finding.evidence
            )
        finding.save(update_fields=["dismissed_at", "dismissed_baseline"])
    return redirect(
        reverse("moderation_spam") + "?" + request.POST.get("return_query", "")[:2000]
    )
