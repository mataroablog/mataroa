import random
from datetime import timedelta

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, IntegerField, Q, Sum
from django.db.models.functions import Cast
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from main import models
from main import moderation_campaigns as campaigns
from main import moderation_deletion as deletion
from main import promotional_spam as scanner
from main.views.moderation_inbox import _canonical_redirect


def findings():
    return models.PromotionalSpamFinding.objects.filter(
        rule_version=scanner.VERSION, dismissed_at__isnull=True
    )


def eligible(qs):
    return qs.filter(
        campaign_strong=True, owner__in=models.User.objects.exclude(deletion.PROTECTED)
    )


def example(finding):
    evidence = finding.evidence
    campaign = evidence.get("campaign", {})
    return {
        "owner_id": finding.owner_id,
        "username": finding.owner.username,
        "coverage": finding.campaign_coverage,
        "matched": campaign.get("matched", 0),
        "sampled": campaign.get("sampled", 0),
        "full_archive": campaign.get("scope") == "full_archive",
        "checked": campaign.get("checked", 0),
        "total": campaign.get("total", 0),
        "gallery_domains": campaign.get("domains", []),
        "posts": evidence.get("posts", 0),
        "pages": evidence.get("pages", 0),
        "example": campaign.get("example", {}),
        "counterexample": campaign.get("counterexample"),
    }


def examples(qs):
    """Independent random examples plus median-strength and lowest-coverage matches."""
    ordered = list(qs.order_by("campaign_coverage", "pk").values_list("pk", flat=True))
    if not ordered:
        return {}
    middle = ordered[max(0, len(ordered) // 2 - 1) : len(ordered) // 2 + 2]
    random_ids = random.SystemRandom().sample(ordered, min(3, len(ordered)))
    groups = {
        "Representative": middle,
        "Random": random_ids,
        "Weakest matches": ordered[:3],
    }
    selected = {
        f.pk: example(f)
        for f in qs.filter(
            pk__in=set(middle + random_ids + ordered[:3])
        ).select_related("owner")
    }
    return {
        label: [selected[pk] for pk in ids if pk in selected]
        for label, ids in groups.items()
    }


@never_cache
@require_GET
def index(request):
    if response := _canonical_redirect(request):
        return response
    qs = findings()
    grouped = (
        qs.exclude(campaign_key="")
        .values("campaign_key")
        .annotate(
            blogs=Count("pk"),
            strong=Count("pk", filter=Q(campaign_strong=True)),
            posts=Sum(Cast("evidence__posts", IntegerField())),
        )
    )
    page = Paginator(
        grouped.order_by("-strong", "-blogs", "campaign_key"), 25
    ).get_page(request.GET.get("page"))
    for group in page:
        members = qs.filter(campaign_key=group["campaign_key"])
        sample = members.order_by("pk").first()
        group["campaign"] = (
            sample.evidence["campaign"]
            if sample
            else {"title": "Group changed; reload"}
        )
        group["eligible"] = (
            eligible(members).count()
            if group["strong"] >= campaigns.MIN_GROUP_SIZE
            else 0
        )
        group["weak"] = group["blogs"] - group["strong"]
    return render(
        request,
        "main/moderation_campaigns.html",
        {
            "page_obj": page,
            "ungrouped": qs.filter(campaign_key="")
            .filter(
                groups__kind="category",
                groups__key__in=[
                    "pornography",
                    "gambling",
                    "promotional",
                    "gallery_promotion",
                    "support_impersonation",
                ],
            )
            .distinct()
            .count(),
            "outdated": models.PromotionalSpamFinding.objects.exclude(
                rule_version=scanner.VERSION
            ).count(),
            "recommendations": [
                (campaigns.RECOMMENDATIONS[key], qs.count(), key)
                for key, qs in campaigns.recommendation_sets().items()
            ],
            "selections": models.ModerationDeletionSelection.objects.filter(
                reviewer=request.user
            ).order_by("-created_at")[:10],
        },
    )


@never_cache
@require_GET
def detail(request, key):
    if response := _canonical_redirect(request):
        return response
    members = findings().filter(campaign_key=key)
    first = members.order_by("pk").first()
    if not first:
        raise Http404()
    strong = members.filter(campaign_strong=True).count()
    candidates = (
        eligible(members) if strong >= campaigns.MIN_GROUP_SIZE else members.none()
    )
    return render(
        request,
        "main/moderation_campaign.html",
        {
            "key": key,
            "campaign": first.evidence["campaign"],
            "total": members.count(),
            "eligible": candidates.count(),
            "weak": members.filter(campaign_strong=False).count(),
            "protected": members.filter(
                owner__in=models.User.objects.filter(deletion.PROTECTED)
            ).count(),
            "examples": examples(candidates),
            "weak_examples": examples(members.filter(campaign_strong=False)),
            "page_obj": Paginator(
                members.select_related("owner").order_by("campaign_coverage", "pk"), 100
            ).get_page(request.GET.get("page")),
        },
    )


@never_cache
@require_POST
def prepare(request, key):
    if response := _canonical_redirect(request):
        return response
    members = findings().filter(campaign_key=key)
    if members.filter(campaign_strong=True).count() < campaigns.MIN_GROUP_SIZE:
        return HttpResponse(
            "This pattern needs more corroborating blogs before group deletion.",
            status=409,
        )
    candidates = eligible(members)
    ids = list(candidates.order_by("owner_id").values_list("owner_id", flat=True))
    if not ids:
        return HttpResponse(
            "No unprotected strong candidates in this group.", status=409
        )
    # Freeze IDs first. Later additions to the group cannot enter this selection.
    with transaction.atomic():
        first = candidates.first()
        if not first:
            return HttpResponse("The group changed. Review it again.", status=409)
        selection = models.ModerationDeletionSelection.objects.create(
            reviewer=request.user,
            title=first.evidence["campaign"]["title"],
            campaign_key=key,
            rule_version=scanner.VERSION,
        )
        totals = dict.fromkeys(
            ("blogs", "posts", "pages", "images", "comments", "excluded_changed"), 0
        )
        for offset in range(0, len(ids), 100):
            owner_ids = ids[offset : offset + 100]
            current = {
                f.owner_id: f
                for f in eligible(
                    findings().filter(campaign_key=key, owner_id__in=owner_ids)
                )
            }
            owners = list(scanner.owner_queryset().filter(pk__in=current))
            stats = scanner.metadata(owners)
            counts = deletion.counts(owners, stats)
            rows = []
            totals["excluded_changed"] += len(owner_ids) - len(owners)
            for owner in owners:
                finding = current[owner.pk]
                if scanner.source_signature(owner, stats) != finding.source_signature:
                    totals["excluded_changed"] += 1
                    continue
                rows.append(
                    models.ModerationDeletionItem(
                        selection=selection,
                        owner_id_snapshot=owner.pk,
                        username=owner.username,
                        snapshot=deletion.snapshot(
                            request.user, owner, finding, counts[owner.pk]
                        ),
                    )
                )
                totals["blogs"] += 1
                for field in ("posts", "pages", "images", "comments"):
                    totals[field] += counts[owner.pk][field]
            models.ModerationDeletionItem.objects.bulk_create(rows)
        if not totals["blogs"]:
            selection.delete()
            return HttpResponse(
                "All selected accounts changed. Review again after scanning.",
                status=409,
            )
        selection.totals = totals
        selected_ids = selection.items.values_list("owner_id_snapshot", flat=True)
        selection.examples = examples(candidates.filter(owner_id__in=selected_ids))
        selection.save(update_fields=["totals", "examples"])
    return redirect("moderation_campaign_selection", selection_id=selection.pk)


def expired(selection):
    start = selection.confirmed_at or selection.created_at
    lifetime = timedelta(hours=24 if selection.confirmed_at else 1)
    return (
        timezone.now() - start > lifetime or selection.rule_version != scanner.VERSION
    )


def progress(selection):
    return {
        "deleted": selection.deleted,
        "skipped": selection.skipped,
        "remaining": selection.totals["blogs"] - selection.deleted - selection.skipped,
        "finished": bool(selection.finished_at),
        "cancelled": bool(selection.cancelled_at),
    }


@never_cache
@require_GET
def selection_detail(request, selection_id):
    if response := _canonical_redirect(request):
        return response
    selection = get_object_or_404(
        models.ModerationDeletionSelection, pk=selection_id, reviewer=request.user
    )
    return render(
        request,
        "main/moderation_campaign_selection.html",
        {
            "selection": selection,
            "examples": selection.examples,
            "progress": progress(selection),
            "expired": expired(selection),
            "page_obj": Paginator(selection.items.order_by("pk"), 100).get_page(
                request.GET.get("page")
            ),
        },
    )


@never_cache
@require_POST
def action(request, selection_id):
    if response := _canonical_redirect(request):
        return response
    with transaction.atomic():
        # Serialize processing, confirmation and cancellation, including retries
        # from multiple tabs. One HTTP request processes at most one account.
        selection = get_object_or_404(
            models.ModerationDeletionSelection.objects.select_for_update(),
            pk=selection_id,
            reviewer=request.user,
        )
        command = request.POST.get("action")
        if command == "cancel":
            if not selection.finished_at:
                selection.cancelled_at = timezone.now()
                selection.save(update_fields=["cancelled_at"])
            return JsonResponse(progress(selection))
        if selection.cancelled_at or expired(selection):
            return JsonResponse(
                {
                    "error": "Selection cancelled, expired, or rules changed. Review again."
                },
                status=409,
            )
        if command == "confirm":
            if request.POST.get("acknowledge") != "yes":
                return JsonResponse(
                    {"error": "Explicit confirmation is required."}, status=400
                )
            if not selection.confirmed_at:
                selection.confirmed_at = timezone.now()
                selection.save(update_fields=["confirmed_at"])
            return JsonResponse(progress(selection))
        if command != "process" or not selection.confirmed_at:
            return JsonResponse(
                {"error": "Confirm this exact selection before deleting."}, status=400
            )
        item = selection.items.filter(status="pending").order_by("pk").first()
        if item:
            outcome = deletion.delete_snapshot(
                item.snapshot, campaign_key=selection.campaign_key
            )
            deleted = outcome["ok"] and not outcome.get("already_deleted")
            item.status = "deleted" if deleted else "skipped"
            item.result = (
                "Deleted" if deleted else outcome.get("error", "Already deleted")
            )
            item.save(update_fields=["status", "result"])
            selection.deleted += int(deleted)
            selection.skipped += int(not deleted)
        if selection.deleted + selection.skipped == selection.totals["blogs"]:
            selection.finished_at = timezone.now()
        selection.save(update_fields=["deleted", "skipped", "finished_at"])
        return JsonResponse(
            {
                **progress(selection),
                "last": {"username": item.username, "result": item.result}
                if item
                else None,
            }
        )
