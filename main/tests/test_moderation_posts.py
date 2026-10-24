from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from main import models


class PostInspectionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = models.User.objects.create(username="moderator", is_superuser=True)
        cls.owner = models.User.objects.create(username="big-blog")
        cls.other = models.User.objects.create(username="other")
        cls.posts = models.Post.objects.bulk_create(
            [
                models.Post(
                    owner=cls.owner,
                    title=f"Post {i}",
                    slug=f"post-{i}",
                    body="Read [an offer](https://offer.example).",
                )
                for i in range(30)
            ]
        )
        cls.first_day = timezone.now().replace(
            hour=12, minute=0, second=0, microsecond=0
        ) - timedelta(days=30)
        for i, post in enumerate(cls.posts):
            models.Post.objects.filter(pk=post.pk).update(
                created_at=cls.first_day + timedelta(days=i // 3)
            )
        models.Post.objects.filter(pk=cls.posts[0].pk).update(published_at=None)
        models.Post.objects.filter(pk=cls.posts[-1].pk).update(
            published_at=timezone.now().date() + timedelta(days=10)
        )
        models.Post.objects.create(
            owner=cls.other,
            title="Private to other account",
            slug="other",
            body="offer.example",
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("moderation_post_inspect", args=[self.owner.pk])

    def test_spaced_sample_includes_start_and_end_without_other_accounts(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        posts = response.context["posts"]
        self.assertEqual(len(posts), 12)
        self.assertEqual(posts[0].pk, self.posts[0].pk)
        self.assertEqual(posts[-1].pk, self.posts[-1].pk)
        self.assertEqual(
            [p.position for p in posts], [1 + i * 29 // 11 for i in range(12)]
        )
        self.assertTrue(all("body" in p.get_deferred_fields() for p in posts))
        self.assertEqual(len(captured), 7)
        self.assertNotContains(response, "Private to other account")
        self.assertEqual(response.context["stats"]["published"], 28)
        self.assertEqual(response.context["stats"]["drafts"], 1)
        self.assertEqual(response.context["stats"]["scheduled"], 1)
        self.assertEqual(response.context["domains"], [("offer.example", 12)])

    def test_busiest_days_link_to_exact_created_day(self):
        response = self.client.get(self.url)
        days = response.context["busiest_days"]
        self.assertEqual(len(days), 10)
        self.assertEqual(days[0]["total"], 3)
        filtered = self.client.get(self.url + days[0]["url"])
        self.assertEqual(filtered.context["matching_count"], 3)
        self.assertEqual(filtered.context["mode"], "oldest")
        for post in filtered.context["posts"]:
            self.assertEqual(post.created_at.date(), days[0]["day"])

    def test_browse_paginates_and_preserves_filters(self):
        first = self.client.get(self.url, {"view": "oldest", "q": "offer.example"})
        self.assertEqual(len(first.context["posts"]), 25)
        self.assertEqual(first.context["posts"][0].pk, self.posts[0].pk)
        self.assertContains(first, "view=oldest&amp;q=offer.example&amp;page=2")
        second = self.client.get(
            self.url, {"view": "oldest", "q": "offer.example", "page": 2}
        )
        self.assertEqual(len(second.context["posts"]), 5)
        newest = self.client.get(self.url, {"view": "newest"})
        self.assertEqual(newest.context["posts"][0].pk, self.posts[-1].pk)
        models.Post.objects.filter(pk=self.posts[0].pk).update(
            updated_at=timezone.now()
        )
        edited = self.client.get(self.url, {"view": "edited"})
        self.assertEqual(edited.context["posts"][0].pk, self.posts[0].pk)

    def test_search_extracts_excerpt_near_a_late_body_match(self):
        models.Post.objects.filter(pk=self.posts[5].pk).update(
            body="padding " * 1000 + "rare-needle"
        )
        response = self.client.get(self.url, {"q": "rare-needle"})
        self.assertEqual(response.context["matching_count"], 1)
        self.assertEqual(response.context["posts"][0].pk, self.posts[5].pk)
        self.assertContains(response, "rare-needle")

    def test_status_and_created_date_filters(self):
        for status, expected in [("published", 28), ("draft", 1), ("scheduled", 1)]:
            response = self.client.get(self.url, {"status": status})
            self.assertEqual(response.context["matching_count"], expected)
        day = (self.first_day + timedelta(days=2)).date().isoformat()
        response = self.client.get(self.url, {"start": day, "end": day})
        self.assertEqual(response.context["matching_count"], 3)

    def test_invalid_filters_show_errors_and_no_posts(self):
        for params in [
            {"start": "invalid"},
            {"view": "unknown"},
            {"status": "unknown"},
            {"start": "2026-02-01", "end": "2026-01-01"},
        ]:
            response = self.client.get(self.url, params)
            self.assertTrue(response.context["form"].errors)
            self.assertEqual(response.context["posts"], [])

    def test_empty_account_and_invalid_page(self):
        empty = models.User.objects.create(username="empty")
        response = self.client.get(reverse("moderation_post_inspect", args=[empty.pk]))
        self.assertContains(response, "No matching posts.")
        self.assertEqual(response.context["stats"]["total"], 0)
        response = self.client.get(self.url, {"view": "newest", "page": "oops"})
        self.assertEqual(response.context["page_obj"].number, 1)

    def test_requires_superuser_and_preserves_query_on_redirect(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        response = self.client.get(
            self.url,
            {"view": "oldest"},
            HTTP_HOST=f"big-blog.{settings.CANONICAL_HOST}",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query), {"view": ["oldest"]})
        self.assertIn("no-store", self.client.get(self.url)["Cache-Control"])
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 404)


class LargestBlogsTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(username="moderator", is_superuser=True)
        self.client.force_login(self.admin)
        for i in range(1, 7):
            owner = models.User.objects.create(username=f"blog-{i}")
            models.Post.objects.bulk_create(
                [
                    models.Post(owner=owner, title=f"Post {j}", slug=f"post-{j}")
                    for j in range(i)
                ]
            )
        self.url = reverse("moderation_posts")

    def test_top_five_total_and_inspection_links(self):
        response = self.client.get(self.url, {"limit": 5})
        self.assertEqual(response.context["largest_total"], 20)
        self.assertEqual(
            [u.posts_total for u in response.context["user_list"]], [6, 5, 4, 3, 2]
        )
        self.assertContains(response, "Inspect posts", count=6)
        other_sort = self.client.get(self.url, {"limit": 1, "mode": "reverse"})
        self.assertEqual(other_sort.context["largest_total"], 20)

    def test_published_counts_exclude_scheduled_and_limits_are_capped(self):
        owner = models.User.objects.get(username="blog-6")
        models.Post.objects.filter(owner=owner).update(
            published_at=timezone.now().date() + timedelta(days=5)
        )
        response = self.client.get(self.url, {"limit": 99999})
        self.assertEqual(response.context["top_limit"], 5000)
        largest = response.context["user_list"][0]
        self.assertEqual(largest.posts_published, 0)
        self.assertEqual(largest.posts_scheduled, 6)
        self.assertIsNone(largest.last_post_date)


class InboxDeleteTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(username="moderator", is_superuser=True)
        self.owner = models.User.objects.create(username="disposable")
        self.post = models.Post.objects.create(
            owner=self.owner, title="Spam", slug="spam"
        )
        models.Page.objects.create(owner=self.owner, title="Page", slug="page")
        models.Image.objects.create(
            owner=self.owner, name="Image", slug="image", extension="png", data=b"image"
        )
        models.Comment.objects.create(post=self.post, name="Commenter", body="Comment")
        self.url = reverse("moderation_user_delete", args=[self.owner.pk])

    def test_csrf_protected_delete_cascades_only_target_account(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.get(reverse("moderation_inbox"))
        self.assertContains(response, f'data-delete-url="{self.url}"')
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())
        csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(self.url, HTTP_X_CSRFTOKEN=csrf)
        self.assertJSONEqual(response.content, {"ok": True})
        for model in [models.Post, models.Page, models.Image, models.Comment]:
            self.assertEqual(model.objects.count(), 0)
        self.assertTrue(models.User.objects.filter(pk=self.admin.pk).exists())

    def test_delete_requires_superuser_and_post_and_protects_staff(self):
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        for target in [
            self.admin,
            models.User.objects.create(username="staff", is_staff=True),
        ]:
            response = self.client.post(
                reverse("moderation_user_delete", args=[target.pk])
            )
            self.assertEqual(response.status_code, 400)
            self.assertTrue(models.User.objects.filter(pk=target.pk).exists())
