from datetime import timedelta
from unittest.mock import patch
from xml.etree import ElementTree

from django.test import Client, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from main import models
from mataroa import settings


class RSSFeedIsolationTestCase(TestCase):
    def setUp(self):
        self.users = [
            models.User.objects.create(
                username="alice", blog_title="Alice's blog", blog_byline="About Alice"
            ),
            models.User.objects.create(
                username="bob",
                blog_title="Bob's blog",
                blog_byline="About Bob",
                custom_domain="bob.example.com",
            ),
        ]
        self.posts = [
            models.Post.objects.create(
                owner=user,
                title=f"Post by {user.username}",
                slug=f"{user.username}-post",
                body=f"Content by {user.username}.",
                published_at=timezone.now().date(),
            )
            for user in self.users
        ]

    def test_overlap_before_metadata(self):
        self.assert_overlapping_feeds_isolated("link")

    def test_overlap_before_items(self):
        self.assert_overlapping_feeds_isolated("items")

    def assert_overlapping_feeds_isolated(self, pause_at):
        feed = resolve(reverse("rss_feed")).func
        original_get_dynamic_attr = feed._get_dynamic_attr
        hosts = [
            f"alice.{settings.CANONICAL_HOST}",
            self.users[1].custom_domain,
        ]

        # Interleave requests before metadata and before posts are read. This
        # deterministically exercises thread-switch points without timing races.
        other_responses = []
        interrupted = False

        def get_dynamic_attr(name, obj, default=None):
            nonlocal interrupted
            if name == pause_at and not interrupted:
                interrupted = True
                other_responses.append(
                    Client().get(reverse("rss_feed"), HTTP_HOST=hosts[1], secure=True)
                )
            return original_get_dynamic_attr(name, obj, default)

        with patch.object(feed, "_get_dynamic_attr", get_dynamic_attr):
            response = self.client.get(
                reverse("rss_feed"), HTTP_HOST=hosts[0], secure=True
            )

        self.assertTrue(interrupted)
        self.assertEqual(len(other_responses), 1)
        responses = [response, other_responses[0]]
        for user, post, host, response in zip(
            self.users, self.posts, hosts, responses, strict=True
        ):
            self.assertEqual(response.status_code, 200)
            channel = ElementTree.fromstring(response.content).find("channel")
            self.assertEqual(channel.findtext("title"), user.blog_title)
            self.assertEqual(channel.findtext("link"), user.blog_url)
            self.assertEqual(channel.findtext("description"), user.blog_byline_as_text)
            self.assertEqual(
                channel.find("{http://www.w3.org/2005/Atom}link").get("href"),
                f"https://{host}{reverse('rss_feed')}",
            )
            items = channel.findall("item")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].findtext("title"), post.title)
            self.assertEqual(items[0].findtext("description"), post.body_as_html)
            for field in ("link", "guid"):
                self.assertEqual(
                    items[0].findtext(field), f"https:{post.get_proper_url()}"
                )

        for user in self.users:
            self.assertEqual(
                models.AnalyticPage.objects.filter(user=user, path="rss").count(), 1
            )


class RSSFeedTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
            "published_at": timezone.now(),
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_rss_feed(self):
        response = self.client.get(
            reverse("rss_feed"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/rss+xml; charset=utf-8")
        self.assertContains(response, self.data["title"])
        self.assertContains(response, self.data["slug"])
        self.assertContains(response, self.data["body"])
        self.assertContains(
            response,
            f"//{self.user.username}.{settings.CANONICAL_HOST}/blog/{self.data['slug']}/</link>",
        )


class RSSFeedDraftsTestCase(TestCase):
    """Tests draft posts do not appear in the RSS feed."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.post_published = {
            "title": "Welcome post",
            "slug": "welcome-post",
            "body": "Content sentence.",
            "published_at": timezone.now(),
        }
        models.Post.objects.create(owner=self.user, **self.post_published)
        self.post_draft = {
            "title": "Hidden post",
            "slug": "hidden-post",
            "body": "Hidden sentence.",
            "published_at": None,
        }
        models.Post.objects.create(owner=self.user, **self.post_draft)

    def test_rss_feed(self):
        response = self.client.get(
            reverse("rss_feed"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/rss+xml; charset=utf-8")
        self.assertContains(response, self.post_published["title"])
        self.assertContains(response, self.post_published["slug"])
        self.assertContains(response, self.post_published["body"])
        self.assertNotContains(response, self.post_draft["title"])
        self.assertNotContains(response, self.post_draft["slug"])
        self.assertNotContains(response, self.post_draft["body"])
        self.assertNotContains(
            response,
            f"//{self.user.username}.{settings.CANONICAL_HOST}/blog/{self.post_draft['slug']}/</link>",
        )


class RSSFeedFuturePostTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New Future Post",
            "slug": "new-future-post",
            "body": "future post body",
            "published_at": timezone.now() + timedelta(1),
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_future_post_hidden(self):
        response = self.client.get(
            reverse("rss_feed"),
            # needs HTTP_HOST because we need to request it on the subdomain
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/rss+xml; charset=utf-8")
        self.assertNotContains(response, self.data["title"])
        self.assertNotContains(response, self.data["slug"])
        self.assertNotContains(response, self.data["body"])
        self.assertNotContains(
            response,
            f"//{self.user.username}.{settings.CANONICAL_HOST}/blog/{self.data['slug']}/</link>",
        )


class RSSFeedFormatTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(
            username="alice", blog_title="test title", blog_byline="test about text"
        )

    def test_feed_valid(self):
        response = self.client.get(
            reverse("rss_feed"),
            # needs HTTP_HOST because we need to request it on the subdomain
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<?xml version="1.0" encoding="utf-8"?>')
        self.assertContains(
            response,
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        )
        self.assertContains(response, f"<title>{self.user.blog_title}</title>")
        self.assertContains(
            response, f"<description>{self.user.blog_byline}</description>"
        )
