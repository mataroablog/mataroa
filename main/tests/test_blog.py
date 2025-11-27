import json

from django.conf import settings
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from main import models, scheme


class IndexTestCase(TestCase):
    """Test canonical mataroa.blog works."""

    def test_index(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mataroa")


class BlogIndexTestCase(TestCase):
    """Test blog index works for logged in."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Blog of Alice"
        self.user.save()
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_index(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data["title"])


class BlogIndexAnonTestCase(TestCase):
    """Test blog index works for anon."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Blog of Alice"
        self.user.save()
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_index_non(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data["title"])


class BlogIndexRedirTestCase(TestCase):
    """Test logged in user is redirected from canonical to blog index."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Blog of Alice"
        self.user.save()
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_index_redir(self):
        response = self.client.get(reverse("blog_index"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            f"{self.user.username}.{settings.CANONICAL_HOST}" in response.url
        )


class BlogRetiredRedirTestCase(TestCase):
    """
    Test anon user is redirected to redirect_domain,
    when redirect_domain exists without protocol prefix.
    """

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Blog of Alice"
        self.user.redirect_domain = "example.com"
        self.user.save()
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_retired_redir(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(f"//{self.user.redirect_domain}", response.url)


class BlogRetiredRedirProtocolTestCase(TestCase):
    """
    Test anon user is redirected to redirect_domain,
    when redirect_domain exists with protocol prefix http.
    """

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Blog of Alice"
        self.user.redirect_domain = "http://example.com"
        self.user.save()
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_retired_redir(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.user.redirect_domain, response.url)


class BlogImportTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Blog of Alice"
        self.user.save()
        self.client.force_login(self.user)

    def test_blog_import(self):
        filename = "main/tests/testdata/lorem.md"
        with open(filename) as fp:
            self.client.post(reverse("blog_import"), {"file": fp})
            self.assertTrue(models.Post.objects.filter(title="lorem.md").exists())
            self.assertTrue(
                "Curabitur pretium tincidunt lacus"
                in models.Post.objects.get(title="lorem.md").body
            )


class BlogExportMarkdownTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_export(self):
        response = self.client.post(reverse("export_markdown"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertContains(response, b"export-markdown")
        self.assertContains(response, self.data["slug"].encode("utf-8"))


class BlogExportPrintTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.blog_title = "Alice Blog"
        self.user.blog_byline = "a blog about wonderland"
        self.user.save()
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_export(self):
        response = self.client.post(reverse("export_print"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.blog_title)
        self.assertContains(response, self.user.blog_byline)
        self.assertContains(response, self.user.username)
        self.assertContains(response, self.data["title"].encode("utf-8"))


class BlogExportZolaTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_export(self):
        response = self.client.post(reverse("export_zola"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertContains(response, b"export-zola")
        self.assertContains(response, self.data["slug"].encode("utf-8"))


class BlogExportHugoTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_export(self):
        response = self.client.post(reverse("export_hugo"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertContains(response, b"export-hugo")
        self.assertContains(response, self.data["slug"].encode("utf-8"))


class BlogExportEpubTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_blog_export(self):
        response = self.client.post(reverse("export_epub"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/epub")
        self.assertContains(response, b"OEBPS/titlepage.xhtml")
        self.assertContains(response, b"OEBPS/toc.xhtml")
        self.assertContains(response, b"OEBPS/author.xhtml")


class BlogNotificationListTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.notification = models.Notification.objects.create(
            blog_user=self.user,
            email="s@example.com",
        )

    def test_subscibers_list(self):
        response = self.client.get(reverse("notification_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"s@example.com")


class BlogNotificationSubscribeTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.notifications_on = True
        self.user.save()

    def test_blog_subscribe(self):
        response = self.client.post(
            reverse("notification_subscribe"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
            data={"email": "s@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            models.Notification.objects.filter(
                blog_user=self.user, email="s@example.com", is_active=True
            ).exists()
        )


class BlogNotificationUnsubscribeTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.notification = models.Notification.objects.create(
            blog_user=self.user,
            email="s@example.com",
        )

    def test_blog_unsubscribe(self):
        response = self.client.post(
            reverse("notification_unsubscribe"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
            data={"email": "s@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            models.Notification.objects.get(email="s@example.com").is_active
        )


class BlogNotificationUnsubscribeKeyTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.notification = models.Notification.objects.create(
            blog_user=self.user,
            email="s@example.com",
        )

    def test_blog_unsubscribe_key(self):
        response = self.client.get(
            reverse(
                "notification_unsubscribe_key",
                args=(self.notification.unsubscribe_key,),
            ),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            models.Notification.objects.filter(
                email="s@example.com", is_active=False
            ).exists()
        )


class BlogNotificationResubscribeTestCase(TestCase):
    """Test one can subscribe after having unsubscribed."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.notifications_on = True
        self.user.save()

        self.notification = models.Notification.objects.create(
            blog_user=self.user,
            email="s@example.com",
            is_active=False,
        )

    def test_blog_resubscribe(self):
        response = self.client.post(
            reverse("notification_subscribe"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
            data={"email": "s@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            models.Notification.objects.filter(
                email="s@example.com", is_active=True
            ).exists()
        )


class BlogNotificationUnsubscriberNotShownTestCase(TestCase):
    """Test someone who unsubscribes does not appear on list."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.user.notifications_on = True
        self.user.save()
        self.client.force_login(self.user)

        self.notification_active = models.Notification.objects.create(
            blog_user=self.user,
            email="active@example.com",
            is_active=True,
        )
        self.notification_inactive = models.Notification.objects.create(
            blog_user=self.user,
            email="inactive@example.com",
            is_active=False,
        )

    def test_blog_unsubscribed_not_shown(self):
        response = self.client.get(
            reverse("notification_list"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.notification_active.email)
        self.assertNotContains(response, self.notification_inactive.email)


class BlogNotificationRecordListTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)
        self.notification = models.Notification.objects.create(
            blog_user=self.user,
            email="s@example.com",
        )
        self.notificationrecord = models.NotificationRecord.objects.create(
            notification=self.notification,
            post=self.post,
            sent_at="2020-01-01",
        )

    def test_notificationrecord_list(self):
        response = self.client.get(reverse("notificationrecord_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b"s@example.com")
        self.assertContains(response, b"2020-01-01")


class PostmarkWebhookTestCase(TestCase):
    """Test the Postmark webhook endpoint for creating posts via email."""

    def setUp(self):
        self.user = models.User.objects.create(
            username="alice", email="alice@example.com"
        )

    def test_postmark_webhook_create_post_success(self):
        data = {
            "From": "alice@example.com",
            "To": f"post@{self.user.username}.{settings.CANONICAL_HOST}",
            "Subject": "My New Post",
            "TextBody": "This is the content of my new post.",
            "Headers": [],
        }

        response = self.client.post(
            reverse("postmark_webhook"),
            data=json.dumps(data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(models.Post.objects.filter(title="My New Post").exists())

        post = models.Post.objects.get(title="My New Post")
        self.assertEqual(post.owner, self.user)
        self.assertEqual(post.body, "This is the content of my new post.")
        self.assertIsNotNone(post.published_at)

        # check that a reply email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice@example.com"])
        self.assertIn(post.slug, mail.outbox[0].body)

    def test_postmark_webhook_nonexistent_user(self):
        data = {
            "From": "nonexistent@example.com",
            "To": f"post@{self.user.username}.{settings.CANONICAL_HOST}",
            "Subject": "Test Post",
            "TextBody": "This is the body of the test post.",
            "Headers": [],
        }

        response = self.client.post(
            reverse("postmark_webhook"),
            data=json.dumps(data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(models.Post.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_postmark_webhook_reply_email_contains_post_url(self):
        data = {
            "From": "alice@example.com",
            "To": f"post@{self.user.username}.{settings.CANONICAL_HOST}",
            "Subject": "Test Post",
            "TextBody": "This is the body of the test post.",
            "Headers": [],
        }

        response = self.client.post(
            reverse("postmark_webhook"),
            data=json.dumps(data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        post = models.Post.objects.first()

        self.assertEqual(len(mail.outbox), 1)
        expected_url = scheme.get_protocol() + post.get_proper_url()
        self.assertIn(expected_url, mail.outbox[0].body)

    def test_postmark_webhook_create_draft_success(self):
        data = {
            "From": "alice@example.com",
            "To": f"draft@{self.user.username}.{settings.CANONICAL_HOST}",
            "Subject": "My Draft Post",
            "TextBody": "This is the content of my draft post.",
            "Headers": [],
        }

        response = self.client.post(
            reverse("postmark_webhook"),
            data=json.dumps(data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(models.Post.objects.filter(title="My Draft Post").exists())

        post = models.Post.objects.get(title="My Draft Post")
        self.assertEqual(post.owner, self.user)
        self.assertEqual(post.body, "This is the content of my draft post.")
        self.assertIsNone(post.published_at)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice@example.com"])
        self.assertIn(post.slug, mail.outbox[0].body)

    def test_postmark_webhook_cannot_post_to_another_users_blog(self):
        bob = models.User.objects.create(username="bob", email="bob@example.com")

        data = {
            "From": "alice@example.com",
            "To": f"post@{bob.username}.{settings.CANONICAL_HOST}",
            "Subject": "Trying to Post to Bob's Blog",
            "TextBody": "This should not be allowed.",
            "Headers": [],
        }

        response = self.client.post(
            reverse("postmark_webhook"),
            data=json.dumps(data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        # no post created
        self.assertFalse(models.Post.objects.exists())
        # no email sent
        self.assertEqual(len(mail.outbox), 0)
