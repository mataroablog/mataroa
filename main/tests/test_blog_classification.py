from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from main import blog_classification as classification
from main import models
from main import promotional_spam as spam
from main.admin import UserAdmin
from main.tests.test_promotional_spam import make_posts, scan


class ClassificationTest(TestCase):
    def setUp(self):
        self.owner = models.User.objects.create(username="author")

    def finding(self, bodies):
        for i, body in enumerate(bodies):
            models.Post.objects.create(
                owner=self.owner, title=f"Notes {i}", slug=f"notes-{i}", body=body
            )
        scan()
        return models.PromotionalSpamFinding.objects.get(owner=self.owner)

    def test_linked_photos_are_not_promotion_even_with_repeated_destinations(self):
        finding = self.finding(
            [
                f'My photograph {i}. <a href="https://storage.example/full/{i}"><img src="https://storage.example/{i}.jpg" alt="bird"></a> '
                f"[Full resolution](https://storage.example/{i}.JPEG?download=1)"
                for i in range(25)
            ]
        )
        self.assertEqual(finding.score, 0)
        self.assertEqual(finding.evidence["domains"], [])

    def test_link_collection_and_imported_playlist_are_not_spam(self):
        finding = self.finding(
            [
                f"This week I enjoyed track {i}. [Listen](https://open.spotify.com/track/{i})"
                for i in range(30)
            ]
        )
        models.Post.objects.filter(owner=self.owner).update(published_at="2020-01-01")
        scan()
        finding.refresh_from_db()
        self.assertEqual(finding.score, 0)
        self.assertIn("links", finding.evidence["category_reasons"])
        self.assertFalse(
            any("busiest" in reason for reason in finding.evidence["reasons"])
        )

    def test_pornography_and_promotion_overlap_but_discussion_is_not_porn(self):
        finding = self.finding(
            [
                "View Full Gallery. Curated gallery: free porn videos. https://gallery.example/"
                + str(i)
                for i in range(4)
            ]
        )
        self.assertIn("pornography", finding.evidence["category_reasons"])
        self.assertIn("promotional", finding.evidence["category_reasons"])
        for text in [
            "Sex education and sexual health: a discussion of pornography.",
            "Research study about free porn videos and porn addiction.",
            "A nude figure drawing from my art class. View full gallery.",
            "The word porn appears in this book review.",
        ]:
            with self.subTest(text=text):
                self.assertNotIn(
                    "pornography", classification.item_labels("", text, True, 0)
                )

    def test_categories_cover_profiles_empty_unknown_and_non_english_porn(self):
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertEqual(list(finding.evidence["category_reasons"]), ["empty"])
        self.owner.blog_byline = "View Full Gallery: 成人影片 高清播放"
        self.owner.save()
        scan()
        finding.refresh_from_db()
        self.assertIn("pornography", finding.evidence["category_reasons"])
        self.owner.blog_byline = "Hello world"
        self.owner.save()
        scan()
        finding.refresh_from_db()
        self.assertEqual(list(finding.evidence["category_reasons"]), ["unclassified"])

    def test_business_gambling_personal_and_technical_signals(self):
        self.assertIn(
            "commercial",
            classification.item_labels("", "Contact us for our services", True, 0),
        )
        self.assertIn(
            "gambling",
            classification.item_labels(
                "Casino", "Real money play and bonuses", True, 0
            ),
        )
        self.assertNotIn(
            "gambling",
            classification.item_labels(
                "Casino history", "Research about casinos", True, 0
            ),
        )
        text = (
            "I learned Python programming. I started debugging a database. "
            + "An account of my experience. " * 12
        )
        self.assertTrue(
            {"personal", "technical"}
            <= classification.item_labels("My diary", text, False, 0)
        )

    def test_uploaded_images_and_redirects_do_not_count_as_empty(self):
        models.Image.objects.create(
            owner=self.owner, name="photo", slug="empty-photo", data=b"x"
        )
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertNotIn("empty", finding.evidence["category_reasons"])
        models.Image.objects.filter(owner=self.owner).delete()
        self.owner.redirect_domain = "my-site.example"
        self.owner.save()
        scan()
        finding.refresh_from_db()
        self.assertNotIn("empty", finding.evidence["category_reasons"])

    def test_direct_body_updates_are_reclassified_on_next_pass(self):
        finding = self.finding(["Hello world"])
        models.Post.objects.filter(owner=self.owner).update(
            body="View Full Gallery: free porn videos"
        )
        scan()
        finding.refresh_from_db()
        self.assertIn("pornography", finding.evidence["category_reasons"])
        models.Post.objects.filter(owner=self.owner).update(body="Hello again")
        scan()
        finding.refresh_from_db()
        self.assertNotIn("pornography", finding.evidence["category_reasons"])
        self.assertFalse(
            finding.groups.filter(kind="category", key="pornography").exists()
        )


class BulkDeletionTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(
            username="admin", is_superuser=True, is_staff=True
        )
        self.owner = models.User.objects.create(username="promoter")
        make_posts(self.owner)
        scan()
        self.client.force_login(self.admin)
        self.preview_url = reverse("moderation_bulk_preview")
        self.delete_url = reverse("moderation_bulk_delete")

    def preview(self, **extra):
        return self.client.post(
            self.preview_url, {"selected": [self.owner.pk], **extra}
        )

    def ticket(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        return response.context["rows"][0]["ticket"]

    def test_preview_does_not_delete_and_confirm_only_deletes_exact_selection(self):
        other = models.User.objects.create(username="other")
        make_posts(other)
        scan()
        response = self.preview()
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["totals"]["posts"], 4)
        self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())
        ticket = response.context["rows"][0]["ticket"]
        self.assertJSONEqual(
            self.client.post(self.delete_url, {"ticket": ticket}).content, {"ok": True}
        )
        self.assertFalse(models.User.objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(models.User.objects.filter(pk=other.pk).exists())
        self.assertEqual(
            self.client.post(self.delete_url, {"ticket": ticket}).status_code, 200
        )

    def test_every_protected_flag_excluded_and_rechecked_after_preview(self):
        for flag in [
            "is_premium",
            "is_grandfathered",
            "is_redirect_grandfathered",
            "is_approved",
            "is_staff",
            "is_superuser",
        ]:
            with self.subTest(flag=flag):
                ticket = self.ticket()
                models.User.objects.filter(pk=self.owner.pk).update(**{flag: True})
                self.assertEqual(self.preview().status_code, 400)
                result = self.client.post(self.delete_url, {"ticket": ticket})
                self.assertEqual(result.status_code, 409)
                self.assertIn("Protected", result.json()["error"])
                self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())
                self.assertEqual(
                    models.Post.objects.filter(owner=self.owner).count(), 4
                )
                models.User.objects.filter(pk=self.owner.pk).update(**{flag: False})

    def test_matching_selection_crosses_pages_excludes_protected_and_freezes(self):
        for i in range(26):
            owner = models.User.objects.create(
                username=f"another-{i}", is_approved=i == 0
            )
            make_posts(owner)
        scan()
        response = self.preview(scope="matching")
        self.assertEqual(len(response.context["rows"]), 26)
        self.assertNotIn(
            "another-0", [r["owner"].username for r in response.context["rows"]]
        )
        later = models.User.objects.create(username="later")
        make_posts(later)
        scan()
        for row in response.context["rows"]:
            self.assertEqual(
                self.client.post(
                    self.delete_url, {"ticket": row["ticket"]}
                ).status_code,
                200,
            )
        self.assertTrue(models.User.objects.filter(pk=later.pk).exists())
        self.assertTrue(models.User.objects.filter(username="another-0").exists())

    def test_category_selection_and_old_rule_findings(self):
        models.Post.objects.filter(owner=self.owner).update(
            body="Curated gallery. Free porn videos."
        )
        scan()
        self.assertEqual(self.preview().status_code, 400)
        response = self.preview(scope="matching", category="pornography")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 1)
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        finding.rule_version = spam.VERSION - 1
        finding.save()
        self.assertEqual(
            self.preview(scope="matching", category="pornography").status_code, 400
        )
        self.assertContains(
            self.client.get(reverse("moderation_spam")), "await reclassification"
        )

    def test_content_and_review_changes_block_execution(self):
        for change in ["post", "image", "comment", "dismiss", "version"]:
            with self.subTest(change=change):
                scan()
                finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
                finding.dismissed_at = None
                finding.save()
                ticket = self.ticket()
                if change == "post":
                    post = models.Post.objects.filter(owner=self.owner).first()
                    post.body += " New content"
                    post.save()
                elif change == "image":
                    models.Image.objects.create(
                        owner=self.owner, name="photo", slug="photo", data=b"x"
                    )
                elif change == "comment":
                    models.Comment.objects.create(
                        post=models.Post.objects.filter(owner=self.owner).first(),
                        body="New comment",
                    )
                elif change == "dismiss":
                    finding.dismissed_at = timezone.now()
                    finding.save()
                else:
                    finding.rule_version -= 1
                    finding.save()
                self.assertEqual(
                    self.client.post(self.delete_url, {"ticket": ticket}).status_code,
                    409,
                )
                self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())

    def test_changed_content_blocks_preview_until_scan(self):
        post = models.Post.objects.filter(owner=self.owner).first()
        post.body += " edited"
        post.save()
        self.assertEqual(self.preview().status_code, 409)
        scan()
        self.assertEqual(self.preview().status_code, 200)

    def test_authorization_csrf_signature_expiry_and_reviewer_binding(self):
        ticket = self.ticket()
        self.assertEqual(self.client.get(self.delete_url).status_code, 405)
        self.assertEqual(self.client.get(self.preview_url).status_code, 405)
        for value in ["", "bad", ticket + "x"]:
            self.assertEqual(
                self.client.post(self.delete_url, {"ticket": value}).status_code, 400
            )
        with patch(
            "django.core.signing.time.time",
            return_value=timezone.now().timestamp() + 3601,
        ):
            self.assertEqual(
                self.client.post(self.delete_url, {"ticket": ticket}).status_code, 400
            )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(
            csrf_client.post(self.delete_url, {"ticket": ticket}).status_code, 403
        )
        self.assertEqual(
            csrf_client.post(
                self.preview_url, {"selected": [self.owner.pk]}
            ).status_code,
            403,
        )
        other_admin = models.User.objects.create(
            username="another-admin", is_superuser=True
        )
        self.client.force_login(other_admin)
        self.assertEqual(
            self.client.post(self.delete_url, {"ticket": ticket}).status_code, 409
        )
        for user in [self.owner, None]:
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assertEqual(self.client.post(self.preview_url).status_code, 404)
            self.assertEqual(
                self.client.post(self.delete_url, {"ticket": ticket}).status_code, 404
            )
        self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())

    def test_admin_single_and_bulk_protection_even_after_object_was_loaded(self):
        model_admin = UserAdmin(models.User, AdminSite())
        request = RequestFactory().post("/admin/")
        request.user = self.admin
        for flag in [
            "is_premium",
            "is_grandfathered",
            "is_redirect_grandfathered",
            "is_approved",
        ]:
            with self.subTest(flag=flag):
                models.User.objects.filter(pk=self.owner.pk).update(**{flag: True})
                fresh = models.User.objects.get(pk=self.owner.pk)
                self.assertFalse(model_admin.has_delete_permission(request, fresh))
                with self.assertRaises(PermissionDenied):
                    model_admin.delete_model(request, self.owner)
                with self.assertRaises(PermissionDenied):
                    model_admin.delete_queryset(
                        request, models.User.objects.filter(pk=self.owner.pk)
                    )
                self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())
                models.User.objects.filter(pk=self.owner.pk).update(**{flag: False})
