from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from main import models
from main.views.moderation_inbox import ContentPreview


class ContentPreviewTest(SimpleTestCase):
    def test_extracts_markdown_html_and_plain_links_without_rendering_html(self):
        parsed = ContentPreview(
            "[Offer](https://offers.example/path) "
            '<a href="https://other.example">Other</a> '
            "https://plain.example/path. "
            "![Picture](https://images.example/picture.jpg) "
            "[Local](/about/) [Unsafe](javascript:alert(1))",
            "https://alice.mataroa.blog",
        )
        self.assertEqual(
            parsed.domains, {"offers.example", "other.example", "plain.example"}
        )
        self.assertEqual(parsed.images, ["https://images.example/picture.jpg"])
        self.assertIn("Offer", parsed.text)
        self.assertNotIn("<a", parsed.text)

    def test_rejects_unsafe_and_malformed_image_urls(self):
        parsed = ContentPreview(
            '<img src="javascript:alert(1)"><img src="data:image/svg+xml,bad">'
            '<img src="http://[broken"><img src="https://user:password@example.com/x">'
            '<img src="/image/photo.png"><img src="/image/photo.png">',
            "https://alice.mataroa.blog",
        )
        self.assertEqual(parsed.images, ["https://alice.mataroa.blog/image/photo.png"])


class ModerationInboxTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = models.User.objects.create(username="moderator", is_superuser=True)
        cls.alice = models.User.objects.create(
            username="alice", blog_byline="A personal blog"
        )
        cls.bob = models.User.objects.create(username="bob", is_approved=True)
        cls.post = models.Post.objects.create(
            owner=cls.alice,
            title="A post",
            slug="post",
            body="An excerpt with [an offer](https://offers.example/path).",
        )
        cls.page = models.Page.objects.create(
            owner=cls.bob,
            title="A page",
            slug="page",
            body="Page-only search needle",
            is_hidden=True,
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("moderation_inbox")

    def get_users(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return [u.username for u in response.context["users"]]

    def test_inbox_and_previews_are_superuser_only(self):
        urls = [
            self.url,
            reverse("moderation_inbox_preview", args=["post", self.post.pk]),
            reverse("moderation_inbox_preview", args=["profile", self.alice.pk]),
        ]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.alice)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.alice.is_staff = True
        self.alice.save()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_read_only_and_non_cacheable(self):
        for url in [
            self.url,
            reverse("moderation_inbox_preview", args=["post", self.post.pk]),
        ]:
            self.assertEqual(self.client.post(url).status_code, 405)
            self.assertIn("no-store", self.client.get(url)["Cache-Control"])

    def test_subdomain_redirect_preserves_filters(self):
        response = self.client.get(
            self.url,
            {"q": "offers.example", "activity": "new"},
            HTTP_HOST=f"alice.{settings.CANONICAL_HOST}",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"//{settings.CANONICAL_HOST}{self.url}?q=offers.example&activity=new",
        )

    def test_includes_content_and_excludes_empty_accounts(self):
        models.User.objects.create(username="empty")
        image_owner = models.User.objects.create(username="images")
        models.Image.objects.create(
            owner=image_owner,
            name="photo",
            slug="photo",
            data=b"image",
            extension="png",
        )
        self.assertCountEqual(self.get_users(), ["alice", "bob", "images"])
        response = self.client.get(self.url)
        self.assertContains(response, "An excerpt with")
        self.assertContains(response, "offers.example")
        self.assertContains(response, "Hidden page")

    def test_profiles_with_only_custom_footer_or_redirect_are_searchable(self):
        models.User.objects.create(
            username="footer-only", footer_note="https://footer.example"
        )
        models.User.objects.create(
            username="redirect-only", redirect_domain="redirect.example"
        )
        self.assertEqual(self.get_users(q="footer.example"), ["footer-only"])
        self.assertEqual(self.get_users(q="redirect.example"), ["redirect-only"])

    def test_search_covers_profile_content_and_destinations_without_duplicates(self):
        for query, expected in [
            ("alice", ["alice"]),
            ("personal", ["alice"]),
            ("offers.example", ["alice"]),
            ("needle", ["bob"]),
        ]:
            with self.subTest(query=query):
                self.assertEqual(self.get_users(q=query), expected)
        self.alice.footer_note = "Footer keyword"
        self.alice.custom_domain = "custom.example"
        self.alice.redirect_domain = "retired.example"
        self.alice.save()
        for query in ("Footer keyword", "custom.example", "retired.example"):
            self.assertEqual(self.get_users(q=query), ["alice"])
        models.Post.objects.create(
            owner=self.alice,
            title="Another offer",
            slug="offer",
            body="https://offers.example",
        )
        self.assertEqual(self.get_users(q="offers.example"), ["alice"])

    def test_search_surfaces_older_matches_and_excerpt_at_match(self):
        self.post.body = ("padding " * 1000) + "needle-in-old-post https://old.example"
        self.post.save()
        for i in range(4):
            models.Post.objects.create(
                owner=self.alice, title=f"New post {i}", slug=f"post-{i}"
            )
        response = self.client.get(self.url, {"q": "needle-in-old-post"})
        samples = response.context["users"][0].samples
        self.assertEqual(samples[0]["item"].pk, self.post.pk)
        self.assertEqual(len(samples), 3)
        self.assertContains(response, "needle-in-old-post")
        self.assertContains(response, "old.example")

    def test_activity_and_approval_filters(self):
        old = timezone.now() - timedelta(days=30)
        models.User.objects.filter(pk=self.bob.pk).update(date_joined=old)
        models.Page.objects.filter(pk=self.page.pk).update(created_at=old)
        self.assertEqual(
            self.get_users(approval="approved", activity="edited"), ["bob"]
        )
        self.assertEqual(self.get_users(activity="new"), ["alice"])
        self.assertEqual(self.get_users(approval="unapproved"), ["alice"])
        self.assertEqual(self.get_users(activity="burst"), [])
        models.Post.objects.bulk_create(
            [
                models.Post(owner=self.alice, title=f"Burst {i}", slug=f"burst-{i}")
                for i in range(19)
            ]
        )
        self.assertEqual(self.get_users(activity="burst"), ["alice"])
        response = self.client.get(self.url, {"activity": "burst"})
        self.assertContains(response, "20 posts created in the last 24 hours")
        models.Post.objects.filter(owner=self.alice).update(
            created_at=old, updated_at=old
        )
        self.assertEqual(self.get_users(activity="burst"), [])

    def test_latest_edits_sort_ahead_of_newer_accounts(self):
        earlier = timezone.now() - timedelta(days=1)
        models.Post.objects.filter(pk=self.post.pk).update(updated_at=earlier)
        self.assertEqual(self.get_users()[0], "bob")
        self.post.save()
        self.assertEqual(self.get_users()[0], "alice")

    def test_pagination_is_bounded_and_preserves_filters(self):
        models.User.objects.bulk_create(
            [
                models.User(username=f"blog-{i}", blog_byline="A sample")
                for i in range(28)
            ]
        )
        response = self.client.get(
            self.url, {"q": "sample", "activity": "new", "page": "oops"}
        )
        self.assertEqual(len(response.context["users"]), 25)
        self.assertTrue(response.context["has_next"])
        self.assertContains(response, "page=2&amp;q=sample&amp;activity=new")
        second = self.client.get(self.url, {"q": "sample", "page": 2})
        self.assertEqual(len(second.context["users"]), 3)
        self.assertFalse(second.context["has_next"])
        self.assertEqual(self.client.get(self.url, {"page": -9}).context["page"], 1)

    def test_bounded_queries_and_no_image_blobs_or_full_post_bodies(self):
        models.Post.objects.bulk_create(
            [
                models.Post(owner=self.alice, title=f"Post {i}", slug=f"many-{i}")
                for i in range(40)
            ]
        )
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured), 8)
        self.assertNotIn('"main_image"."data"', " ".join(q["sql"] for q in captured))
        alice = next(u for u in response.context["users"] if u.pk == self.alice.pk)
        self.assertEqual(len(alice.sample_posts), 3)
        self.assertEqual(alice.content_counts["posts"], 41)
        self.assertIn("body", alice.sample_posts[0].get_deferred_fields())

    def test_previews_escape_html_and_never_load_images_automatically(self):
        self.post.body = '<script>alert(1)</script><img src="https://external.example/photo.jpg" onerror="alert(2)">'
        self.post.save()
        response = self.client.get(
            reverse("moderation_inbox_preview", args=["post", self.post.pk])
        )
        self.assertNotContains(response, "<script>")
        self.assertNotContains(response, "<img")
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(
            response, 'data-image-url="https://external.example/photo.jpg"'
        )
        inbox = self.client.get(self.url)
        self.assertNotContains(inbox, "<img")

    def test_profile_uploads_do_not_fetch_image_blobs_and_long_text_is_bounded(self):
        self.alice.footer_note = "x" * 50001
        self.alice.save()
        models.Image.objects.create(
            owner=self.alice,
            name="A photo",
            slug="upload",
            data=b"image",
            extension="png",
        )
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                reverse("moderation_inbox_preview", args=["profile", self.alice.pk])
            )
        self.assertContains(response, "Preview limited to the first 50,000 characters")
        self.assertContains(response, "A photo")
        self.assertNotContains(response, "<img")
        self.assertNotIn('"main_image"."data"', " ".join(q["sql"] for q in captured))

    def test_invalid_preview_kind_or_id(self):
        for kind, pk in [("unknown", self.post.pk), ("post", 999999)]:
            self.assertEqual(
                self.client.get(
                    reverse("moderation_inbox_preview", args=[kind, pk])
                ).status_code,
                404,
            )

    def test_relative_images_resolve_against_the_content_path(self):
        self.post.body = "![Image](relative.png)"
        self.post.save()
        response = self.client.get(
            reverse("moderation_inbox_preview", args=["post", self.post.pk])
        )
        self.assertContains(
            response, f"alice.{settings.CANONICAL_HOST}/blog/post/relative.png"
        )
