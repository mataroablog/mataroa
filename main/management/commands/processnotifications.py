from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.core.mail import mail_admins
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from main import models, scheme, text_processing


def get_mail_connection():
    if settings.LOCALDEV:
        return mail.get_connection("django.core.mail.backends.console.EmailBackend")

    # SMPT EmailBackend instantiated with the broadcast-specific email host
    return mail.get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=settings.EMAIL_HOST_BROADCASTS,
    )


def get_email_body_txt(post, notification):
    """Returns the plain text email body as fallback for text-only clients."""
    post_url = scheme.get_protocol() + post.get_proper_url()
    unsubscribe_url = scheme.get_protocol() + notification.get_unsubscribe_url()
    blog_title = post.owner.blog_title or post.owner.username

    body = f"""{blog_title} has published:

# {post.title}

{post_url}

{post.body}

---

Blog post URL:
{post_url}

---

Unsubscribe:
{unsubscribe_url}
"""
    return body


def get_email_body_html(post, notification):
    """Returns the HTML email body with styled content and inline images."""
    post_url = scheme.get_protocol() + post.get_proper_url()
    unsubscribe_url = scheme.get_protocol() + notification.get_unsubscribe_url()
    blog_title = post.owner.blog_title or post.owner.username
    post_body_html = text_processing.md_to_html(post.body)

    published_date = ""
    if post.published_at:
        published_date = post.published_at.strftime("%B %-d, %Y")

    return render_to_string(
        "main/notification_email.html",
        {
            "blog_title": blog_title,
            "post_title": post.title,
            "post_body_html": post_body_html,
            "post_url": post_url,
            "unsubscribe_url": unsubscribe_url,
            "published_date": published_date,
        },
    )


def get_email(post, notification):
    """Returns the email object with both HTML and plain text versions."""

    blog_title = post.owner.username
    # email sender name cannot contain RFC 5322 special characters
    # these cause parsing errors in email headers
    if post.owner.blog_title:
        sanitized_title = post.owner.blog_title
        for char in [
            '"',
            "'",
            ":",
            ",",
            ";",
            "<",
            ">",
            "(",
            ")",
            "[",
            "]",
            "\\",
            "@",
            "\n",
            "\r",
        ]:
            sanitized_title = sanitized_title.replace(char, "")
        if sanitized_title.strip():
            blog_title = sanitized_title

    unsubscribe_url = scheme.get_protocol() + notification.get_unsubscribe_url()
    plain_text_body = get_email_body_txt(post, notification)
    html_body = get_email_body_html(post, notification)

    from_email = f"{post.owner.username}@{settings.EMAIL_FROM_HOST}>"
    from_name = blog_title.replace(".", " ").strip()
    from_phrase = f"{from_name} <{from_email}"
    email = mail.EmailMultiAlternatives(
        subject=post.title,
        body=plain_text_body,
        from_email=from_phrase,
        to=[notification.email],
        headers={
            "X-PM-Message-Stream": "newsletters",
            "List-Unsubscribe": unsubscribe_url,
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    email.attach_alternative(html_body, "text/html")
    return email


class Command(BaseCommand):
    help = "Process new posts and send email to subscribers"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-dryrun",
            action="store_false",
            dest="dryrun",
            help="No dry run. Send actual emails.",
        )
        parser.set_defaults(dryrun=True)

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Processing notifications."))

        yesterday = timezone.now().date() - timedelta(days=1)
        post_list = models.Post.objects.filter(
            owner__notifications_on=True,
            broadcasted_at__isnull=True,
            published_at=yesterday,
        )
        self.stdout.write(self.style.NOTICE(f"Post count to process: {len(post_list)}"))

        count_sent = 0
        connection = get_mail_connection()

        # for all posts that were published yesterday
        for post in post_list:
            try:
                # assume no notification will fail
                no_send_failures = True

                notification_list = models.Notification.objects.filter(
                    blog_user=post.owner,
                    is_active=True,
                )
                msg = (
                    f"Subscriber count for: '{post.title}' (author: {post.owner.username})"
                    f" is {len(notification_list)}."
                )
                self.stdout.write(self.style.NOTICE(msg))
                # for every email address subcribed to the post's blog owner
                for notification in notification_list:
                    try:
                        # don't send if dry run mode
                        if options["dryrun"]:
                            msg = f"Would otherwise sent: '{post.title}' for '{notification.email}'."
                            self.stdout.write(self.style.NOTICE(msg))
                            continue

                        # log record
                        record, created = (
                            models.NotificationRecord.objects.get_or_create(
                                notification=notification,
                                post=post,
                            )
                        )
                        # check if this post id has already been sent to this email
                        # could be because the published_at date has been changed
                        if created:
                            # keep count of all emails of this run
                            count_sent += 1

                            # send out email
                            email = get_email(post, notification)
                            connection.send_messages([email])

                            msg = f"Email sent for '{post.title}' to '{notification.email}'."
                            self.stdout.write(self.style.SUCCESS(msg))
                        else:
                            msg = (
                                f"No email sent for '{post.title}' to '{notification.email}'. "
                                f"Email was sent {record.sent_at}"
                            )
                            self.stdout.write(self.style.NOTICE(msg))
                    except Exception as ex:
                        no_send_failures = False
                        msg = f"Failed to send '{post.title}' to {notification.email}."
                        self.stdout.write(self.style.ERROR(msg))
                        self.stdout.write(self.style.ERROR(str(ex)))

                        # delete record if it was created so it can be retried
                        if "record" in locals() and created:
                            record.delete()

                        # notify admin about the failure
                        try:
                            mail_admins(
                                subject=f"Notification failed: {post.title}",
                                message=(
                                    f"Failed to send notification email.\n\n"
                                    f"Post: {post.title}\n"
                                    f"Author: {post.owner.username}\n"
                                    f"Recipient: {notification.email}\n"
                                    f"Error: {ex}"
                                ),
                            )
                        except Exception:
                            self.stdout.write(
                                self.style.ERROR("Failed to send admin notification.")
                            )

                # broadcast for this post done
                if not options["dryrun"] and no_send_failures:
                    post.broadcasted_at = timezone.now()
                    post.save()

            except Exception as ex:
                # catch any unexpected error at the post level
                msg = f"Failed to process post '{post.title}' (author: {post.owner.username})."
                self.stdout.write(self.style.ERROR(msg))
                self.stdout.write(self.style.ERROR(str(ex)))
                try:
                    mail_admins(
                        subject=f"Post processing failed: {post.title}",
                        message=(
                            f"Failed to process post for notifications.\n\n"
                            f"Post: {post.title}\n"
                            f"Author: {post.owner.username}\n"
                            f"Error: {ex}"
                        ),
                    )
                except Exception:
                    self.stdout.write(
                        self.style.ERROR("Failed to send admin notification.")
                    )

        # broadcast for all posts done
        connection.close()

        # return if send mode is off
        if options["dryrun"]:
            self.stdout.write(
                self.style.SUCCESS("Broadcast dry run done. No emails were sent.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"Broadcast sent. Total {count_sent} emails.")
        )
