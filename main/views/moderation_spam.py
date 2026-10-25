from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from main import (
    blog_classification,
    models,
    moderation_campaigns,
    moderation_deletion,
    promotional_spam,
)
from main.views.moderation_inbox import _canonical_redirect


def filtered_findings(params):
    status = params.get("status", "pending")
    if status not in ("pending", "dismissed", "all"):
        status = "pending"
    category = params.get("category", "promotional")
    if category not in blog_classification.CATEGORIES and category != "all":
        category = "promotional"
    findings = models.PromotionalSpamFinding.objects.filter(
        rule_version=promotional_spam.VERSION
    )
    if status != "all":
        findings = findings.filter(dismissed_at__isnull=status == "pending")
    if category != "all":
        findings = findings.filter(groups__kind="category", groups__key=category)
    recommendation = params.get("recommendation", "all")
    if recommendation in moderation_campaigns.RECOMMENDATIONS:
        findings = findings.filter(
            pk__in=moderation_campaigns.recommendation_sets()[recommendation]
        )
    else:
        recommendation = "all"
    kind = params.get("kind", "")
    key = params.get("group", "")[:253]
    if kind in ("domain", "template") and key:
        findings = findings.filter(groups__kind=kind, groups__key=key)
    else:
        kind = key = ""
    return findings, {
        "category": category,
        "recommendation": recommendation,
        "status": status,
        "group_kind": kind,
        "group_key": key,
    }


@never_cache
@require_GET
def queue(request):
    if response := _canonical_redirect(request):
        return response
    findings, filters = filtered_findings(request.GET)
    groups = list(
        models.PromotionalSpamGroup.objects.filter(
            finding__in=findings, kind__in=["domain", "template"]
        )
        .values("kind", "key")
        .annotate(blogs=Count("finding", distinct=True))
        .filter(blogs__gte=2)
        .order_by("-blogs", "kind", "key")[:20]
    )
    page_obj = Paginator(
        findings.select_related("owner").order_by("-score", "-pk"), 25
    ).get_page(request.GET.get("page"))
    recommendations = moderation_campaigns.recommendation_sets()
    page_ids = [finding.pk for finding in page_obj]
    recommended = {
        pk: label
        for key, label in moderation_campaigns.RECOMMENDATIONS.items()
        for pk in recommendations[key]
        .filter(pk__in=page_ids)
        .values_list("pk", flat=True)
    }
    for finding in page_obj:
        finding.recommendation_label = recommended.get(
            finding.pk, "Awaiting reclassification"
        )
        finding.category_labels = [
            (blog_classification.CATEGORIES.get(key, key), reason)
            for key, reason in finding.evidence.get("category_reasons", {}).items()
        ]
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "main/moderation_spam.html",
        {
            "page_obj": page_obj,
            **filters,
            "groups": groups,
            "categories": blog_classification.CATEGORIES.items(),
            "recommendations": moderation_campaigns.RECOMMENDATIONS.items(),
            "querystring": params.urlencode(),
            "return_query": request.GET.urlencode(),
            "scan": models.PromotionalSpamScan.objects.filter(pk=1).first(),
            "outdated": models.PromotionalSpamFinding.objects.exclude(
                rule_version=promotional_spam.VERSION
            ).count(),
            "eligible": findings.filter(
                owner__in=models.User.objects.exclude(moderation_deletion.PROTECTED)
            ).count(),
            "max_batch": moderation_deletion.MAX_BATCH,
        },
    )


@never_cache
@require_POST
def bulk_preview(request):
    if response := _canonical_redirect(request):
        return response
    findings, filters = filtered_findings(request.POST)
    if request.POST.get("scope") != "matching":
        try:
            ids = {int(value) for value in request.POST.getlist("selected")}
        except ValueError:
            return HttpResponse("Invalid selection.", status=400)
        if len(ids) > moderation_deletion.MAX_BATCH:
            return HttpResponse("Too many selected blogs.", status=400)
        findings = findings.filter(owner_id__in=ids)
    findings = list(
        findings.filter(
            owner__in=models.User.objects.exclude(moderation_deletion.PROTECTED)
        ).order_by("-score", "-pk")[: moderation_deletion.MAX_BATCH]
    )
    if not findings:
        return HttpResponse(
            "No eligible blogs selected. Protected accounts cannot be deleted.",
            status=400,
        )
    by_owner = {f.owner_id: f for f in findings}
    owners = list(promotional_spam.owner_queryset().filter(pk__in=by_owner))
    stats = promotional_spam.metadata(owners)
    counts = moderation_deletion.counts(owners, stats)
    rows = []
    totals = dict.fromkeys(("posts", "pages", "images", "comments"), 0)
    for owner in owners:
        finding = by_owner[owner.pk]
        if finding.source_signature != promotional_spam.source_signature(owner, stats):
            return HttpResponse(
                "Selected content changed since scanning. Wait for the next scan, then review again.",
                status=409,
            )
        rows.append(
            {
                "owner": owner,
                "counts": counts[owner.pk],
                "ticket": moderation_deletion.ticket(
                    request.user, owner, finding, counts[owner.pk]
                ),
            }
        )
        for key in totals:
            totals[key] += counts[owner.pk][key]
    return render(
        request,
        "main/moderation_bulk_delete.html",
        {
            "rows": rows,
            "totals": totals,
            "max_batch": moderation_deletion.MAX_BATCH,
            "category_label": blog_classification.CATEGORIES.get(
                filters["category"], "All categories"
            ),
        },
    )


@never_cache
@require_POST
def bulk_delete(request):
    if response := _canonical_redirect(request):
        return response
    try:
        selection = signing.loads(
            request.POST.get("ticket", ""),
            salt=moderation_deletion.SALT,
            max_age=moderation_deletion.MAX_AGE,
        )
    except signing.BadSignature:
        return JsonResponse(
            {"ok": False, "error": "Selection expired or invalid. Review again."},
            status=400,
        )
    if (
        selection["reviewer"] != request.user.pk
        or selection["version"] != promotional_spam.VERSION
    ):
        return JsonResponse(
            {
                "ok": False,
                "error": "Selection belongs to a different reviewer or rule version.",
            },
            status=409,
        )
    result = moderation_deletion.delete_snapshot(selection)
    return JsonResponse(result, status=200 if result["ok"] else 409)


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
