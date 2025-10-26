from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from main import models, scheme


def get_mail_connection():
    if settings.DEBUG:
        return mail.get_connection("django.core.mail.backends.console.EmailBackend")

    # SMPT EmailBackend instantiated with the broadcast-specific email host
    return mail.get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=settings.EMAIL_HOST_BROADCASTS,
    )


def get_email_body(post, notification):
    """Returns the email body (which contains the post body) along with titles and links."""
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


def get_email(post, notification):
    """Returns the email object, containing all info needed to be sent."""

    blog_title = post.owner.username
    # email sender name cannot contain commas
    if post.owner.blog_title and "," not in post.owner.blog_title:
        blog_title = post.owner.blog_title

    unsubscribe_url = scheme.get_protocol() + notification.get_unsubscribe_url()
    body = get_email_body(post, notification)
    email = mail.EmailMessage(
        subject=post.title,
        body=body,
        from_email=f"{blog_title} <{post.owner.username}@{settings.EMAIL_FROM_HOST}>",
        to=[notification.email],
        headers={
            "X-PM-Message-Stream": "newsletters",
            "List-Unsubscribe": unsubscribe_url,
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
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
                # don't send if dry run mode
                if options["dryrun"]:
                    msg = f"Would otherwise sent: '{post.title}' for '{notification.email}'."
                    self.stdout.write(self.style.NOTICE(msg))
                    continue

                # log record
                record, created = models.NotificationRecord.objects.get_or_create(
                    notification=notification,
                    post=post,
                )
                # check if this post id has already been sent to this email
                # could be because the published_at date has been changed
                if created:
                    # keep count of all emails of this run
                    count_sent += 1

                    # sent out email
                    email = get_email(post, notification)
                    try:
                        connection.send_messages([email])
                    except Exception as ex:
                        no_send_failures = False
                        msg = f"Failed to send '{post.title}' to {notification.email}."
                        self.stdout.write(self.style.ERROR(msg))
                        record.delete()
                        self.stdout.write(self.style.ERROR(ex))

                    msg = f"Email sent for '{post.title}' to '{notification.email}'."
                    self.stdout.write(self.style.SUCCESS(msg))
                else:
                    msg = (
                        f"No email sent for '{post.title}' to '{notification.email}'. "
                        f"Email was sent {record.sent_at}"
                    )
                    self.stdout.write(self.style.NOTICE(msg))

            # broadcast for this post done
            if not options["dryrun"] and no_send_failures:
                post.broadcasted_at = timezone.now()
                post.save()

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
