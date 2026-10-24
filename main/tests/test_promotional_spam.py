from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from main import models
from main import promotional_spam as spam
from main.management.commands.scanpromotionalspam import LOCK_ID


def make_posts(owner, count=4, domain="offers.example", long=False):
    return models.Post.objects.bulk_create(
        [
            models.Post(
                owner=owner,
                title=f"Article {i}",
                slug=f"article-{i}",
                body=(
                    (
                        f"Original essay about subject {chr(97 + i)}. " * 250
                        if long
                        else f"Brief introduction to topic {i}. "
                    )
                    + f"Read the full article at https://{domain}/articles/{i}"
                ),
            )
            for i in range(count)
        ]
    )


def scan():
    owners = spam.owner_batch(0, 10**12, 200)
    return spam.scan_batch(owners)


class ScannerTest(TestCase):
    def setUp(self):
        self.owner = models.User.objects.create(username="promoter")

    def test_short_repeated_destination_is_flagged_with_safe_evidence(self):
        posts = make_posts(self.owner)
        posts[0].title = '<script>alert("bad")</script>'
        posts[0].save()
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertGreaterEqual(finding.score, spam.THRESHOLD)
        self.assertEqual(finding.evidence["sampled"], 4)
        self.assertEqual(
            finding.evidence["domains"], [{"domain": "offers.example", "count": 4}]
        )
        self.assertEqual(len(finding.evidence["examples"]), 3)
        self.assertTrue(
            finding.groups.filter(kind="domain", key="offers.example").exists()
        )

    def test_volume_and_legitimate_external_links_alone_do_not_qualify(self):
        make_posts(self.owner, 10, long=True)
        other = models.User.objects.create(username="prolific")
        models.Post.objects.bulk_create(
            [
                models.Post(
                    owner=other,
                    title=f"Diary {i}",
                    slug=f"diary-{i}",
                    body="Personal notes",
                )
                for i in range(30)
            ]
        )
        scan()
        self.assertFalse(
            models.PromotionalSpamFinding.objects.filter(
                score__gte=spam.THRESHOLD
            ).exists()
        )

    def test_rotating_destinations_with_many_thin_posts_are_candidates(self):
        make_posts(self.owner, 25)
        for post in models.Post.objects.filter(owner=self.owner):
            post.body = f"Read more about topic {post.pk}: https://site{post.pk}.example/article"
            post.save()
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertGreaterEqual(finding.score, spam.THRESHOLD)
        self.assertGreater(len(finding.evidence["domains"]), 1)

    def test_backlink_must_be_in_an_external_url_and_profile_only_is_checked(self):
        models.Post.objects.create(
            owner=self.owner,
            title="Explanation",
            slug="explanation",
            body="The parameter utm_medium=backlink is used by spammers.",
        )
        scan()
        self.assertEqual(
            models.PromotionalSpamFinding.objects.get(owner=self.owner).score, 0
        )
        self.owner.footer_note = (
            "[Our site](https://offers.example/?utm_medium=backlink)"
        )
        self.owner.save()
        scan()
        self.assertGreaterEqual(
            models.PromotionalSpamFinding.objects.get(owner=self.owner).score,
            spam.THRESHOLD,
        )
        self.assertEqual(spam.hostname("https://www.Example.com/x"), "example.com")
        self.assertEqual(spam.hostname("https://bad host.example/x"), "")

    def test_own_domains_and_images_are_not_promotional_links(self):
        self.owner.custom_domain = "my.example"
        self.owner.save()
        models.Post.objects.bulk_create(
            [
                models.Post(
                    owner=self.owner,
                    title=f"Post {i}",
                    slug=f"post-{i}",
                    body="[Local](https://www.my.example/path) ![Image](https://images.example/x.png) [Unsafe](javascript:alert(1))",
                )
                for i in range(4)
            ]
        )
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertEqual(finding.score, 0)
        self.assertEqual(finding.evidence["domains"], [])

    def test_sample_covers_archive_recent_edits_pages_and_unpublished_content(self):
        posts = make_posts(self.owner, 40)
        old = timezone.now() - timedelta(days=10)
        models.Post.objects.filter(owner=self.owner).update(
            updated_at=old, published_at=None
        )
        models.Post.objects.filter(pk=posts[13].pk).update(updated_at=timezone.now())
        models.Page.objects.create(
            owner=self.owner,
            title="Hidden",
            slug="hidden",
            is_hidden=True,
            body="[Offer](https://pages.example)",
        )
        owners = spam.owner_batch(0, self.owner.pk, 100)
        stats = spam.metadata(owners)
        selected = spam.sample_batch(models.Post, owners, stats["post"], 8)[
            self.owner.pk
        ]
        ids = {p["pk"] for p in selected}
        self.assertLessEqual(len(ids), 12)
        self.assertTrue({posts[0].pk, posts[-1].pk, posts[13].pk} <= ids)
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertEqual(finding.evidence["posts"], 40)
        self.assertEqual(finding.evidence["pages"], 1)
        self.assertIn(
            {"domain": "pages.example", "count": 1}, finding.evidence["domains"]
        )

    def test_long_body_tail_is_examined_without_marking_it_thin(self):
        models.Post.objects.create(
            owner=self.owner,
            title="Long",
            slug="long",
            body="a" * 100000 + " https://offers.example/?utm_medium=backlink",
        )
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertGreaterEqual(finding.score, spam.THRESHOLD)
        self.assertEqual(finding.evidence["thin"], 0)
        self.assertLessEqual(len(finding.evidence["examples"][0]["excerpt"]), 500)

    def test_incremental_scan_detects_profile_edit_content_edit_and_deletion(self):
        posts = make_posts(self.owner)
        self.assertEqual(scan(), 1)
        with patch.object(spam, "sample_batch", wraps=spam.sample_batch) as sampled:
            self.assertEqual(scan(), 0)
            sampled.assert_not_called()
        self.owner.blog_byline = "New profile"
        self.owner.save()
        self.assertEqual(scan(), 1)
        posts[0].body = "Changed text"
        posts[0].save()
        self.assertEqual(scan(), 1)
        posts[1].delete()
        self.assertEqual(scan(), 1)
        self.assertEqual(scan(), 0)
        self.assertEqual(
            spam.scan_batch(spam.owner_batch(0, self.owner.pk, 100), force=True), 1
        )

    def test_dismissal_survives_rescans_and_small_additions_then_reopens(self):
        make_posts(self.owner, 10)
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        finding.dismissed_at = timezone.now()
        finding.dismissed_baseline = spam.baseline(finding.score, finding.evidence)
        finding.save()
        spam.scan_batch(spam.owner_batch(0, self.owner.pk, 100), force=True)
        finding.refresh_from_db()
        self.assertIsNotNone(finding.dismissed_at)
        for i in range(5):
            models.Post.objects.create(
                owner=self.owner,
                title=f"New {i}",
                slug=f"new-{i}",
                body="Read more at https://offers.example/new",
            )
            scan()
            finding.refresh_from_db()
            if i < 4:
                self.assertIsNotNone(finding.dismissed_at)
        self.assertIsNone(finding.dismissed_at)

    def test_shared_wording_groups_ignore_title_url_and_numbers(self):
        other = models.User.objects.create(username="related")
        for owner, domain in [(self.owner, "one.example"), (other, "two.example")]:
            models.Post.objects.bulk_create(
                [
                    models.Post(
                        owner=owner,
                        title=f"Topic {i}",
                        slug=f"topic-{i}",
                        body=f"Topic {i}\n"
                        + "This introduction promotes our detailed guide. Follow the link to read the complete article and discover all the details. "
                        + f"https://{domain}/{i}",
                    )
                    for i in range(4)
                ]
            )
        scan()
        left = set(
            models.PromotionalSpamGroup.objects.filter(
                finding__owner=self.owner, kind="template"
            ).values_list("key", flat=True)
        )
        right = set(
            models.PromotionalSpamGroup.objects.filter(
                finding__owner=other, kind="template"
            ).values_list("key", flat=True)
        )
        self.assertTrue(left & right)
        self.owner.delete()
        self.assertEqual(models.PromotionalSpamFinding.objects.count(), 1)


class ScanCommandTest(TestCase):
    def setUp(self):
        models.User.objects.bulk_create(
            [models.User(username=f"blog-{i}") for i in range(5)]
        )

    def command(self, **kwargs):
        out = StringIO()
        call_command("scanpromotionalspam", pause=0, stdout=out, **kwargs)
        return out.getvalue()

    def test_checkpoint_resume_and_incremental_completion(self):
        self.command(batch_size=2, max_blogs=2)
        state = models.PromotionalSpamScan.objects.get(pk=1)
        self.assertEqual(state.checked, 2)
        self.assertIsNone(state.completed_at)
        self.assertEqual(models.PromotionalSpamFinding.objects.count(), 2)
        self.command(batch_size=2)
        state.refresh_from_db()
        self.assertEqual(state.checked, 5)
        self.assertIsNotNone(state.completed_at)
        self.command(batch_size=2)
        state.refresh_from_db()
        self.assertEqual(state.analyzed, 0)
        self.command(full=True)
        state.refresh_from_db()
        self.assertEqual(state.analyzed, 5)

    def test_failed_batch_does_not_advance_checkpoint_and_lock_is_released(self):
        self.command(batch_size=2, max_blogs=2)
        before = models.PromotionalSpamScan.objects.get(pk=1).cursor
        with (
            patch.object(spam, "scan_batch", side_effect=RuntimeError("interrupted")),
            self.assertRaises(RuntimeError),
        ):
            self.command()
        self.assertEqual(models.PromotionalSpamScan.objects.get(pk=1).cursor, before)
        self.assertIn("Complete", self.command())

    def test_second_connection_cannot_run_overlapping_scan(self):
        with connection.Database.connect(**connection.get_connection_params()) as other:
            other.execute("SELECT pg_advisory_lock(%s)", [LOCK_ID])
            self.assertIn("already running", self.command())
            self.assertFalse(models.PromotionalSpamScan.objects.exists())
            other.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])


class QueueTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(username="admin", is_superuser=True)
        self.owner = models.User.objects.create(username="promoter")
        make_posts(self.owner)
        scan()
        self.finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.url = reverse("moderation_spam")
        self.dismiss_url = reverse("moderation_spam_dismiss", args=[self.owner.pk])
        self.client.force_login(self.admin)

    def test_queue_renders_saved_evidence_with_no_content_scan(self):
        self.finding.evidence["examples"][0]["title"] = "<script>alert(1)</script>"
        self.finding.save()
        with patch.object(
            spam, "scan_batch", side_effect=AssertionError("must not scan")
        ):
            response = self.client.get(self.url)
        self.assertContains(response, "Page 1 of 1", count=2)
        self.assertContains(response, "4 of 4 sampled items")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "data-delete-url=")
        self.assertIn("no-store", response["Cache-Control"])
        filtered = self.client.get(
            self.url, {"kind": "domain", "group": "offers.example"}
        )
        self.assertEqual(filtered.context["page_obj"].paginator.count, 1)
        missing = self.client.get(
            self.url, {"kind": "domain", "group": "other.example"}
        )
        self.assertEqual(missing.context["page_obj"].paginator.count, 0)

    def test_dismiss_restore_and_stale_review(self):
        data = {"revision": self.finding.source_signature}
        self.assertEqual(
            self.client.post(self.dismiss_url, {"revision": "old"}).status_code, 409
        )
        self.assertEqual(self.client.post(self.dismiss_url, data).status_code, 302)
        self.assertEqual(
            self.client.get(self.url).context["page_obj"].paginator.count, 0
        )
        self.assertEqual(
            self.client.get(self.url, {"status": "dismissed"})
            .context["page_obj"]
            .paginator.count,
            1,
        )
        self.client.post(self.dismiss_url, {**data, "action": "restore"})
        self.assertEqual(
            self.client.get(self.url).context["page_obj"].paginator.count, 1
        )

    def test_authorization_csrf_and_post_only(self):
        self.assertEqual(self.client.get(self.dismiss_url).status_code, 405)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        client.get(self.url)
        self.assertEqual(
            client.post(
                self.dismiss_url, {"revision": self.finding.source_signature}
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                self.dismiss_url,
                {"revision": self.finding.source_signature},
                HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
            ).status_code,
            302,
        )
        for user in [None, self.owner]:
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.post(self.dismiss_url).status_code, 404)

    def test_related_groups_and_pagination_preserve_filter(self):
        for i in range(26):
            owner = models.User.objects.create(username=f"related-{i}")
            finding = models.PromotionalSpamFinding.objects.create(
                owner=owner,
                score=60,
                scanned_at=timezone.now(),
                evidence=self.finding.evidence,
                source_signature="test",
            )
            models.PromotionalSpamGroup.objects.create(
                finding=finding, kind="domain", key="offers.example"
            )
        response = self.client.get(
            self.url, {"kind": "domain", "group": "offers.example", "page": 2}
        )
        self.assertContains(response, "Page 2 of 2", count=2)
        self.assertEqual(len(response.context["page_obj"]), 2)
        self.assertContains(response, "kind=domain&amp;group=offers.example&amp;page=1")
        self.assertEqual(response.context["groups"][0]["blogs"], 27)
