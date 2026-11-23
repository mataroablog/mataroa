from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from main import gambling_overview as overview
from main import models, promotional_spam
from main.views.moderation_campaigns import create_selection
from main.views.moderation_inbox import _canonical_redirect


@never_cache
@require_GET
def index(request):
    if response := _canonical_redirect(request):
        return response
    qs = overview.findings()
    raw_term = request.GET.get("term", "")
    term = overview.clean_term(raw_term)
    context = {
        "summary": overview.summary(qs),
        "signups": overview.signups(qs),
        "destinations": overview.destinations(qs),
        "campaigns": overview.campaigns(qs),
        "still_posting": overview.still_posting(qs),
        "most_viewed": overview.most_viewed(qs),
        "scan": models.PromotionalSpamScan.objects.filter(pk=1).first(),
        "outdated": models.PromotionalSpamFinding.objects.exclude(
            rule_version=promotional_spam.VERSION
        ).count(),
        "raw_term": raw_term,
        "term": term,
        "min_term_length": overview.MIN_TERM_LENGTH,
    }
    if term:
        domains = overview.matching_domains(term)
        members = overview.matching(term, domains)
        context.update(
            {
                "term_domains": domains,
                "term_total": members.count(),
                "term_eligible": overview.unprotected(members).count(),
                "term_page": Paginator(
                    members.select_related("owner").order_by("-owner__date_joined"),
                    50,
                ).get_page(request.GET.get("page")),
            }
        )
    return render(request, "main/moderation_gambling.html", context)


@never_cache
@require_POST
def prepare(request):
    if response := _canonical_redirect(request):
        return response
    term = overview.clean_term(request.POST.get("term"))
    if not term:
        return HttpResponse(
            f"Enter a brand or domain of at least {overview.MIN_TERM_LENGTH} "
            "characters (letters, digits, dots or hyphens).",
            status=400,
        )
    candidates = overview.unprotected(overview.matching(term))
    return create_selection(
        request.user, f"Gambling blogs linking domains containing “{term}”", candidates
    )
