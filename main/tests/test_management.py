from datetime import datetime
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from main import models
from main.management.commands import mailexports, processnotifications


class ProcessNotificationsTest(TestCase):
    """
    Test processnotifications sends emails to the blog's subscibers.
    """

    def setUp(self):
        self.user = models.User.objects.create(
            username="alice", email="alice@mataroa.blog", notifications_on=True
        )

        post_data = {
            "title": "Yesterday post",
            "slug": "yesterday-post",
            "body": "Content sentence.",
            "published_at": timezone.make_aware(datetime(2020, 1, 1)),
        }
        self.post_yesterday = models.Post.objects.create(owner=self.user, **post_data)

        post_data = {
            "title": "Today post",
            "slug": "today-post",
            "body": "Content sentence.",
            "published_at": timezone.make_aware(datetime(2020, 1, 2)),
        }
        self.post_today = models.Post.objects.create(owner=self.user, **post_data)

        self.notification = models.Notification.objects.create(
            blog_user=self.user, email="subscriber@example.com"
        )

    def test_command(self):
        output = StringIO()

        with (
            patch.object(timezone, "now", return_value=datetime(2020, 1, 2, 13, 00)),
            patch.object(
                # Django default test runner overrides SMTP EmailBackend with locmem,
                # but because we re-import the SMTP backend in
                # processnotifications.get_mail_connection, we need to mock it here too.
                processnotifications,
                "get_mail_connection",
                return_value=mail.get_connection(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ),
        ):
            call_command("processnotifications", "--no-dryrun", stdout=output)

        # notification records
        records = models.NotificationRecord.objects.all()
        self.assertEqual(len(records), 1)
        record = records[0]

        # notification record for yesterday's post
        self.assertEqual(record.notification.email, self.notification.email)
        self.assertEqual(record.post.title, "Yesterday post")

        # logging
        self.assertIn("Processing notifications.", output.getvalue())
        self.assertIn(
            "Email sent for 'Yesterday post' to 'subscriber@example.com'",
            output.getvalue(),
        )

        # email
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Yesterday post")
        self.assertIn("Unsubscribe", mail.outbox[0].body)

        # email headers
        self.assertEqual(mail.outbox[0].to, [self.notification.email])
        self.assertEqual(mail.outbox[0].reply_to, [])
        self.assertEqual(
            mail.outbox[0].from_email,
            f"{self.user.username} <{self.user.username}@{settings.EMAIL_FROM_HOST}>",
        )

        self.assertEqual(
            mail.outbox[0].extra_headers["X-PM-Message-Stream"], "newsletters"
        )
        self.assertIn(
            "/newsletter/unsubscribe/",
            mail.outbox[0].extra_headers["List-Unsubscribe"],
        )
        self.assertEqual(
            mail.outbox[0].extra_headers["List-Unsubscribe-Post"],
            "List-Unsubscribe=One-Click",
        )

    def tearDown(self):
        models.User.objects.all().delete()
        models.Post.objects.all().delete()


class MailExportsTest(TestCase):
    """
    Test mail_export sends emails to users with `mail_export_on` enabled.
    """

    def setUp(self):
        self.user = models.User.objects.create(
            username="alice", email="alice@mataroa.blog", mail_export_on=True
        )

        post_data = {
            "title": "A post",
            "slug": "a-post",
            "body": "Content sentence.",
            "published_at": timezone.make_aware(datetime(2020, 1, 1)),
        }
        self.post_a = models.Post.objects.create(owner=self.user, **post_data)

        post_data = {
            "title": "Second post",
            "slug": "second-post",
            "body": "Content sentence two.",
            "published_at": timezone.make_aware(datetime(2020, 1, 2)),
        }
        self.post_b = models.Post.objects.create(owner=self.user, **post_data)

    def test_mail_backend(self):
        connection = mailexports.get_mail_connection()
        self.assertEqual(connection.host, settings.EMAIL_HOST_BROADCASTS)

    def test_command(self):
        output = StringIO()

        with (
            patch.object(timezone, "now", return_value=datetime(2020, 1, 1, 00, 00)),
            patch.object(
                # Django default test runner overrides SMTP EmailBackend with locmem,
                # but because we re-import the SMTP backend in
                # processnotifications.get_mail_connection, we need to mock it here too.
                mailexports,
                "get_mail_connection",
                return_value=mail.get_connection(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ),
        ):
            call_command("mailexports", stdout=output)

        # export records
        records = models.ExportRecord.objects.all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].user, self.user)
        self.assertIn("export-markdown-", records[0].name)

        # logging
        self.assertIn("Processing email exports.", output.getvalue())
        self.assertIn(f"Processing user {self.user.username}.", output.getvalue())
        self.assertIn(f"Export sent to {self.user.username}.", output.getvalue())
        self.assertIn(
            f"Logging export record for '{records[0].name}'.", output.getvalue()
        )
        self.assertIn("Emailing all exports complete.", output.getvalue())

        # email
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Mataroa export", mail.outbox[0].subject)
        self.assertIn("Stop receiving exports", mail.outbox[0].body)

        # email headers
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertEqual(
            mail.outbox[0].from_email,
            settings.DEFAULT_FROM_EMAIL,
        )

        self.assertEqual(mail.outbox[0].extra_headers["X-PM-Message-Stream"], "exports")
        self.assertIn(
            "/export/unsubscribe/",
            mail.outbox[0].extra_headers["List-Unsubscribe"],
        )
        self.assertEqual(
            mail.outbox[0].extra_headers["List-Unsubscribe-Post"],
            "List-Unsubscribe=One-Click",
        )

    def tearDown(self):
        models.User.objects.all().delete()
        models.Post.objects.all().delete()
