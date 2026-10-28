from datetime import datetime, timedelta

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from main import models, scheme


def build_summary_text(target_date: datetime.date) -> str:
    new_users_qs = models.User.objects.filter(date_joined__date=target_date)
    new_posts_qs = models.Post.objects.filter(created_at__date=target_date)
    new_pages_qs = models.Page.objects.filter(created_at__date=target_date)
    new_comments_qs = models.Comment.objects.filter(created_at__date=target_date)

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

    summary_url = f"https://{settings.CANONICAL_HOST}/moderation/summary/{target_date.strftime('%Y-%m-%d')}"
    lines: list[str] = []
    lines.append(f"# Mataroa Summary {target_date.strftime('%Y-%m-%d')}")
    lines.append(summary_url)
    lines.append("")
    lines.append("## Counts")
    lines.append(f"- New users: {new_users_qs.count()}")
    lines.append(f"- New posts: {new_posts_qs.count()}")
    lines.append(f"- New pages: {new_pages_qs.count()}")
    lines.append(f"- New comments: {new_comments_qs.count()}")
    lines.append(f"- Post visits: {post_visits_count}")
    lines.append("")

    lines.append("## Top 5 Posts by Visits")
    if top_posts_by_visits_qs.exists():
        for post in top_posts_by_visits_qs[:5]:
            lines.append(
                f"* {post.title} [{post.visit_count}] {scheme.get_protocol()}{post.get_proper_url()}"
            )
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Full Activity")
    lines.append(f"- New posts: {summary_url}#new-posts")
    lines.append(f"- New users: {summary_url}#new-users")
    lines.append(f"- New pages: {summary_url}#new-pages")
    lines.append(f"- New comments: {summary_url}#new-comments")
    lines.append("")

    return "\n".join(lines)


class Command(BaseCommand):
    help = "Email daily moderation summary to admins."

    def handle(self, *args, **options):
        # Run for the previous day
        target_date = (datetime.utcnow().date()) - timedelta(days=1)

        self.stdout.write(self.style.NOTICE(f"Building summary for {target_date}."))
        body = build_summary_text(target_date)

        subject = f"Mataroa moderation summary {target_date.isoformat()} — {settings.CANONICAL_HOST}"

        to_addresses = list(settings.ADMINS)
        if not to_addresses:
            self.stdout.write(
                self.style.ERROR("No admin addresses configured (ADMINS).")
            )
            return

        email = mail.EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to_addresses,
        )

        connection = mail.get_connection()
        connection.send_messages([email])
        self.stdout.write(self.style.SUCCESS("Daily moderation summary sent."))
