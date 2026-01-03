from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from main import models, scheme, text_processing


def get_mail_connection():
    if settings.LOCALDEV:
        return mail.get_connection("django.core.mail.backends.console.EmailBackend")

    return mail.get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=settings.EMAIL_HOST_BROADCASTS,
    )


def get_test_email(post, to_email):
    """Returns a test email object for previewing the newsletter format."""
    blog_title = post.owner.username
    if post.owner.blog_title and "," not in post.owner.blog_title:
        blog_title = post.owner.blog_title

    post_url = scheme.get_protocol() + post.get_proper_url()
    post_body_html = text_processing.md_to_html(post.body)

    plain_text_body = f"""{blog_title} has published:

# {post.title}

{post_url}

{post.body}

---

Blog post URL:
{post_url}

---

Unsubscribe:
https://example.com/unsubscribe
"""

    published_date = ""
    if post.published_at:
        published_date = post.published_at.strftime("%B %-d, %Y")

    html_body = render_to_string(
        "main/notification_email.html",
        {
            "blog_title": blog_title,
            "post_title": post.title,
            "post_body_html": post_body_html,
            "post_url": post_url,
            "unsubscribe_url": "https://example.com/unsubscribe",
            "published_date": published_date,
        },
    )

    from_email = f"{post.owner.username}@{settings.EMAIL_FROM_HOST}>"
    from_name = blog_title.replace(".", " ").strip()
    from_phrase = f"{from_name} <{from_email}"
    email = mail.EmailMultiAlternatives(
        subject=f"[TEST] {post.title}",
        body=plain_text_body,
        from_email=from_phrase,
        to=[to_email],
        headers={
            "X-PM-Message-Stream": "newsletters",
        },
    )
    email.attach_alternative(html_body, "text/html")
    return email


class Command(BaseCommand):
    help = "Send a test notification email for a specific post."

    def add_arguments(self, parser):
        parser.add_argument(
            "post_id",
            type=int,
            help="ID of the post to send as test email.",
        )
        parser.add_argument(
            "email",
            type=str,
            help="Email address to send the test email to.",
        )

    def handle(self, *args, **options):
        post_id = options["post_id"]
        to_email = options["email"]

        try:
            post = models.Post.objects.get(id=post_id)
        except models.Post.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Post with ID {post_id} not found."))
            return

        self.stdout.write(
            self.style.NOTICE(f"Sending test email for '{post.title}' to {to_email}...")
        )

        email = get_test_email(post, to_email)
        connection = get_mail_connection()

        try:
            connection.send_messages([email])
            self.stdout.write(self.style.SUCCESS(f"Test email sent to {to_email}."))
        except Exception as ex:
            self.stdout.write(self.style.ERROR(f"Failed to send test email: {ex}"))
        finally:
            connection.close()
