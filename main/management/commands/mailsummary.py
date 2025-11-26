from datetime import datetime, timedelta

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from main import models


def build_summary_text(target_date: datetime.date) -> str:
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

    lines: list[str] = []
    lines.append(f"Moderation — Summary {target_date.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("Counts")
    lines.append(f"- New users: {new_users_qs.count()}")
    lines.append(f"- New posts: {new_posts_qs.count()}")
    lines.append(f"- New pages: {new_pages_qs.count()}")
    lines.append(f"- New comments: {new_comments_qs.count()}")
    lines.append(f"- Post visits: {post_visits_count}")
    lines.append("")

    lines.append("Top Posts by Visits")
    if top_posts_by_visits_qs.exists():
        for post in top_posts_by_visits_qs[:20]:
            lines.append(
                f"- {post.title} — {post.visit_count} — {post.owner.username} — {post.get_proper_url}"
            )
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("New Posts")
    if new_posts_qs.exists():
        for p in new_posts_qs:
            lines.append(
                f"- {p.title} by {p.owner.username} ({p.created_at.strftime('%H:%M')}) — {p.get_proper_url}"
            )
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("New Users")
    if new_users_qs.exists():
        for u in new_users_qs:
            lines.append(
                f"- {u.username} ({u.date_joined.strftime('%H:%M')}) — {u.blog_url}"
            )
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("New Pages")
    if new_pages_qs.exists():
        for pg in new_pages_qs:
            lines.append(
                f"- {pg.title} by {pg.owner.username} ({pg.created_at.strftime('%H:%M')}) — {pg.get_absolute_url()}"
            )
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("New Comments")
    if new_comments_qs.exists():
        for c in new_comments_qs:
            pending_note = " pending" if not c.is_approved else ""
            lines.append(
                f"- on {c.post.title} by {c.post.owner.username} ({c.created_at.strftime('%H:%M')}){pending_note} — {c.post.get_proper_url}#comment-{c.id}"
            )
    else:
        lines.append("- None.")

    lines.append("")
    lines.append(
        f"Prev day: {prev_date.strftime('%Y-%m-%d')} | Next day: {next_date.strftime('%Y-%m-%d')}"
    )

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
