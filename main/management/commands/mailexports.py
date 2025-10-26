import io
import uuid
import zipfile
from datetime import datetime

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from main import models, scheme


def get_mail_connection():
    """Returns the default EmailBackend but instantiated with a custom host."""
    return mail.get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=settings.EMAIL_HOST_BROADCASTS,
    )


def get_unsubscribe_url(user):
    return scheme.get_protocol() + user.get_export_unsubscribe_url()


def get_email_body(user):
    """
    Returns the email body (which contains the post body) for the automated
    export email.
    """
    today = datetime.now().date().strftime("%B %d, %Y")
    body = f"""Greetings,

This is the {today} edition of your Mataroa blog export.

Please find your blog’s zip archive in markdown format attached.

---

Stop receiving exports:
{get_unsubscribe_url(user)}
"""
    return body


class Command(BaseCommand):
    help = "Generate zip account exports and email them to users."

    def handle(self, *args, **options):
        if timezone.now().day != 1:
            msg = "No action. Not the first day of the month."
            self.stdout.write(self.style.NOTICE(msg))
            return

        self.stdout.write(self.style.NOTICE("Processing email exports."))

        # gather all user posts for all users
        users = models.User.objects.filter(mail_export_on=True)
        for user in users:
            self.stdout.write(self.style.NOTICE(f"Processing user {user.username}."))

            user_posts = models.Post.objects.filter(owner=user)
            export_posts = []
            for p in user_posts:
                pub_date = p.published_at or p.created_at
                title = p.slug + ".md"
                body = (
                    f"# {p.title}\n\n"
                    f"> Published on {pub_date.strftime('%b %-d, %Y')}\n\n"
                    f"{p.body}\n"
                )
                export_posts.append((title, io.BytesIO(body.encode())))

            # write zip archive in /tmp/
            export_name = "export-markdown-" + str(uuid.uuid4())[:8]
            container_dir = f"{user.username}-mataroa-blog"
            zip_outfile = f"/tmp/{export_name}.zip"
            with zipfile.ZipFile(
                zip_outfile, "a", zipfile.ZIP_DEFLATED, False
            ) as export_archive:
                for file_name, data in export_posts:
                    export_archive.writestr(
                        export_name + f"/{container_dir}/" + file_name, data.getvalue()
                    )

            # reopen zipfile and load in memory
            with open(zip_outfile, "rb") as f:
                # create emails
                today = datetime.now().date().isoformat()
                email = mail.EmailMessage(
                    subject=f"Mataroa export {today} — {user.username}.{settings.CANONICAL_HOST}",
                    body=get_email_body(user),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user.email],
                    headers={
                        "X-PM-Message-Stream": "exports",  # postmark-specific header
                        "List-Unsubscribe": get_unsubscribe_url(user),
                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                    },
                    attachments=[(f"{export_name}.zip", f.read(), "application/zip")],
                )

            # sent out messages
            connection = get_mail_connection()
            connection.send_messages([email])
            self.stdout.write(self.style.SUCCESS(f"Export sent to {user.username}."))

            # log export record
            name = f"{export_name}.zip"
            record = models.ExportRecord.objects.create(name=name, user=user)
            self.stdout.write(
                self.style.SUCCESS(f"Logging export record for '{record.name}'.")
            )

        # log all users mailing is complete
        self.stdout.write(self.style.SUCCESS("Emailing all exports complete."))
