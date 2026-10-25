from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from main import models, moderation_deletion
from main import promotional_spam as scanner
from main.tests.test_promotional_spam import scan

GALLERY = """[View Full Gallery](https://offers.example/gallery)

> Curated gallery | 3 images

[![Preview one](https://images.example/one.jpg)](https://offers.example/gallery)

[![Preview two](https://images.example/two.jpg)](https://offers.example/gallery)

Free porn videos.
"""
GAMBLING = """Casino sign up bonus and real money offers. [Visit](https://casino.example/offer)

![Preview](https://images.example/photo.jpg)

## Offers
Details
## Games
Details
## Register
Details
"""


def posts(owner, bodies):
    return models.Post.objects.bulk_create(
        [
            models.Post(owner=owner, title=f"Sample {i}", slug=f"sample-{i}", body=body)
            for i, body in enumerate(bodies)
        ]
    )


class CampaignRuleTest(TestCase):
    def test_campaign_needs_category_layout_and_shared_destination(self):
        cases = [
            ("gallery", [GALLERY] * 3, True, "explicit-gallery"),
            ("one-gallery", [GALLERY], False, "explicit-gallery"),
            (
                "mixed",
                [GALLERY] * 2 + ["My travel diary"] * 8,
                False,
                "explicit-gallery",
            ),
            (
                "photos",
                [GALLERY.replace("Free porn videos.", "My wildlife photographs.")] * 3,
                False,
                "",
            ),
            (
                "discussion",
                [GALLERY + "Research study about porn addiction."] * 3,
                False,
                "",
            ),
            (
                "plain-links",
                ["Free porn videos. View Full Gallery https://offers.example/"] * 3,
                False,
                "",
            ),
            ("gambling", [GAMBLING], True, "gambling-article"),
            (
                "gambling-reference",
                ["Casino history. https://casino.example/"],
                False,
                "",
            ),
            ("social-host", [GAMBLING.replace("casino.example", "t.me")], False, ""),
        ]
        cases.append(
            (
                "reference-host",
                [GAMBLING.replace("casino.example", "ru.wikipedia.org")],
                False,
                "",
            )
        )
        owners = []
        for name, bodies, strong, family in cases:
            owner = models.User.objects.create(username=name)
            posts(owner, bodies)
            owners.append((owner, strong, family))
        scan()
        for owner, strong, family in owners:
            with self.subTest(owner=owner.username):
                finding = models.PromotionalSpamFinding.objects.get(owner=owner)
                self.assertEqual(finding.campaign_strong, strong)
                self.assertEqual(finding.evidence["campaign"].get("family", ""), family)
        photos = models.PromotionalSpamFinding.objects.get(owner__username="photos")
        self.assertEqual(photos.score, 0)

    def test_changed_content_removes_campaign_membership(self):
        owner = models.User.objects.create(username="gallery")
        posts(owner, [GALLERY] * 3)
        scan()
        self.assertTrue(
            models.PromotionalSpamFinding.objects.get(owner=owner).campaign_key
        )
        models.Post.objects.filter(owner=owner).update(body="Ordinary personal notes")
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=owner)
        self.assertEqual(finding.campaign_key, "")
        self.assertFalse(finding.campaign_strong)


class CampaignSelectionTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(
            username="moderator", is_staff=True, is_superuser=True
        )
        self.owners = [
            models.User.objects.create(username=f"campaign-{i}") for i in range(3)
        ]
        for owner in self.owners:
            posts(owner, [GALLERY] * 3)
        scan()
        self.finding = models.PromotionalSpamFinding.objects.get(owner=self.owners[0])
        self.key = self.finding.campaign_key
        self.client.force_login(self.admin)
        self.url = reverse("moderation_campaign", args=[self.key])
        self.prepare_url = reverse("moderation_campaign_prepare", args=[self.key])

    def prepare(self):
        response = self.client.post(self.prepare_url)
        self.assertEqual(response.status_code, 302)
        selection = models.ModerationDeletionSelection.objects.latest("created_at")
        return selection, reverse("moderation_campaign_action", args=[selection.pk])

    def confirm(self, url):
        response = self.client.post(url, {"action": "confirm", "acknowledge": "yes"})
        self.assertEqual(response.status_code, 200)

    def test_recommendations_partition_inventory_and_filter_queue(self):
        from main.moderation_campaigns import recommendation_sets

        weak = models.User.objects.create(username="weak-gallery")
        posts(weak, [GALLERY])
        ordinary = models.User.objects.create(username="ordinary")
        posts(ordinary, ["Notes about gardening"])
        scan()
        models.User.objects.filter(pk=self.owners[0].pk).update(is_premium=True)
        groups = {
            key: set(qs.values_list("owner_id", flat=True))
            for key, qs in recommendation_sets().items()
        }
        self.assertEqual(groups["strong"], {u.pk for u in self.owners[1:]})
        self.assertEqual(groups["review"], {weak.pk})
        self.assertEqual(groups["none"], {ordinary.pk})
        self.assertEqual(groups["protected"], {self.admin.pk, self.owners[0].pk})
        for key, owners in groups.items():
            response = self.client.get(
                reverse("moderation_spam"),
                {"category": "all", "status": "all", "recommendation": key},
            )
            self.assertEqual({f.owner_id for f in response.context["page_obj"]}, owners)

    def test_review_examples_and_preview_never_delete(self):
        response = self.client.get(reverse("moderation_campaigns"))
        self.assertContains(response, "3 strong candidates eligible for deletion")
        response = self.client.get(self.url)
        self.assertEqual(response.context["eligible"], 3)
        self.assertEqual(
            set(response.context["examples"]),
            {"Representative", "Random", "Weakest matches"},
        )
        selection, action = self.prepare()
        self.assertEqual(selection.totals["blogs"], 3)
        self.assertEqual(selection.totals["posts"], 9)
        self.assertEqual(selection.items.count(), 3)
        self.assertFalse(selection.confirmed_at)
        self.assertEqual(models.Post.objects.count(), 9)
        self.assertEqual(
            self.client.post(action, {"action": "process"}).status_code, 400
        )
        self.assertEqual(
            self.client.post(action, {"action": "confirm"}).status_code, 400
        )
        self.assertEqual(
            models.User.objects.filter(pk__in=[u.pk for u in self.owners]).count(), 3
        )

    def test_confirmed_selection_does_not_include_later_group_members_and_resumes(self):
        selection, action = self.prepare()
        later = models.User.objects.create(username="later")
        posts(later, [GALLERY] * 3)
        scan()
        self.confirm(action)
        # Confirmation itself cannot delete; a request processes one frozen item.
        self.assertEqual(models.Post.objects.count(), 12)
        result = self.client.post(action, {"action": "process"})
        self.assertEqual(result.json()["deleted"], 1)
        page = self.client.get(
            reverse("moderation_campaign_selection", args=[selection.pk])
        )
        self.assertContains(page, "Resume deletion")
        self.assertEqual(page.context["progress"]["remaining"], 2)
        for _ in range(3):
            result = self.client.post(action, {"action": "process"})
        self.assertTrue(result.json()["finished"])
        self.assertEqual(result.json()["deleted"], 3)
        self.assertTrue(models.User.objects.filter(pk=later.pk).exists())
        self.assertEqual(selection.items.filter(status="deleted").count(), 3)

    def test_weak_and_protected_matches_excluded_from_selection(self):
        weak = models.User.objects.create(username="mixed")
        posts(weak, [GALLERY] * 2 + ["Personal diary"] * 5)
        for field in (
            "is_premium",
            "is_grandfathered",
            "is_approved",
            "is_staff",
            "is_superuser",
        ):
            owner = models.User.objects.create(username=field, **{field: True})
            posts(owner, [GALLERY] * 3)
        scan()
        selection, _ = self.prepare()
        self.assertEqual(
            set(selection.items.values_list("owner_id_snapshot", flat=True)),
            {u.pk for u in self.owners},
        )
        response = self.client.get(self.url)
        self.assertEqual(response.context["weak"], 1)
        self.assertEqual(response.context["protected"], 5)

    def test_new_protection_is_rechecked_immediately_before_deletion(self):
        for field in (
            "is_premium",
            "is_grandfathered",
            "is_approved",
            "is_staff",
            "is_superuser",
        ):
            with self.subTest(field=field):
                selection, action = self.prepare()
                models.User.objects.filter(pk=self.owners[0].pk).update(**{field: True})
                self.confirm(action)
                result = self.client.post(action, {"action": "process"})
                self.assertEqual(result.json()["skipped"], 1)
                self.assertIn("Protected", result.json()["last"]["result"])
                self.assertTrue(
                    models.User.objects.filter(pk=self.owners[0].pk).exists()
                )
                models.User.objects.filter(pk=self.owners[0].pk).update(
                    **{field: False}
                )

    def test_content_dismissal_or_membership_changes_are_skipped(self):
        for change in ("content", "dismiss", "weak", "key"):
            with self.subTest(change=change):
                scan()
                models.PromotionalSpamFinding.objects.filter(
                    owner=self.owners[0]
                ).update(dismissed_at=None)
                selection, action = self.prepare()
                self.confirm(action)
                if change == "content":
                    post = models.Post.objects.filter(owner=self.owners[0]).first()
                    post.body += " New content"
                    post.save()
                elif change == "dismiss":
                    models.PromotionalSpamFinding.objects.filter(
                        owner=self.owners[0]
                    ).update(dismissed_at=timezone.now())
                elif change == "weak":
                    models.PromotionalSpamFinding.objects.filter(
                        owner=self.owners[0]
                    ).update(campaign_strong=False)
                else:
                    models.PromotionalSpamFinding.objects.filter(
                        owner=self.owners[0]
                    ).update(campaign_key="different")
                result = self.client.post(action, {"action": "process"})
                self.assertEqual(result.json()["skipped"], 1)
                self.assertTrue(
                    models.User.objects.filter(pk=self.owners[0].pk).exists()
                )

    def test_cancel_and_expiration_prevent_further_deletion(self):
        selection, action = self.prepare()
        models.ModerationDeletionSelection.objects.filter(pk=selection.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        self.assertEqual(
            self.client.post(
                action, {"action": "confirm", "acknowledge": "yes"}
            ).status_code,
            409,
        )
        selection, action = self.prepare()
        self.confirm(action)
        models.ModerationDeletionSelection.objects.filter(pk=selection.pk).update(
            confirmed_at=timezone.now() - timedelta(hours=25)
        )
        self.assertEqual(
            self.client.post(action, {"action": "process"}).status_code, 409
        )
        selection, action = self.prepare()
        self.confirm(action)
        self.assertEqual(
            self.client.post(action, {"action": "cancel"}).status_code, 200
        )
        self.assertEqual(
            self.client.post(action, {"action": "process"}).status_code, 409
        )
        self.assertEqual(models.Post.objects.count(), 9)

    def test_rule_changes_invalidate_selection(self):
        selection, action = self.prepare()
        self.confirm(action)
        with patch.object(scanner, "VERSION", scanner.VERSION + 1):
            self.assertEqual(
                self.client.post(action, {"action": "process"}).status_code, 409
            )
        self.assertEqual(models.Post.objects.count(), 9)

    def test_failure_rolls_back_deletion_and_progress(self):
        selection, action = self.prepare()
        self.confirm(action)
        original = moderation_deletion.delete_snapshot

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise DatabaseError("failure after deletion")

        with (
            patch.object(moderation_deletion, "delete_snapshot", side_effect=fail),
            self.assertRaises(DatabaseError),
        ):
            self.client.post(action, {"action": "process"})
        selection.refresh_from_db()
        self.assertEqual(selection.deleted, 0)
        self.assertEqual(selection.items.filter(status="pending").count(), 3)
        self.assertEqual(models.Post.objects.count(), 9)

    def test_authorization_post_only_csrf_and_reviewer_binding(self):
        selection, action = self.prepare()
        url = reverse("moderation_campaign_selection", args=[selection.pk])
        self.assertEqual(self.client.get(action).status_code, 405)
        self.assertEqual(self.client.get(self.prepare_url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        self.assertEqual(
            csrf.post(action, {"action": "confirm", "acknowledge": "yes"}).status_code,
            403,
        )
        self.assertEqual(csrf.post(self.prepare_url).status_code, 403)
        other = models.User.objects.create(username="other-mod", is_superuser=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(action, {"action": "process"}).status_code, 404
        )
        for user in (self.owners[0], None):
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.post(self.prepare_url).status_code, 404)
            self.assertEqual(self.client.post(action).status_code, 404)

    def test_small_group_cannot_prepare_bulk_deletion(self):
        models.PromotionalSpamFinding.objects.filter(owner=self.owners[0]).update(
            campaign_strong=False
        )
        self.assertEqual(self.client.post(self.prepare_url).status_code, 409)

    def test_selection_has_no_thousand_account_limit(self):
        extra = models.User.objects.bulk_create(
            [models.User(username=f"extra-{i}") for i in range(1001)]
        )
        owners = list(scanner.owner_queryset().filter(pk__in=[u.pk for u in extra]))
        stats = scanner.metadata(owners)
        # These synthetic saved findings exercise selection size, not classification.
        models.PromotionalSpamFinding.objects.bulk_create(
            [
                models.PromotionalSpamFinding(
                    owner=owner,
                    source_signature=scanner.source_signature(owner, stats),
                    scanned_at=timezone.now(),
                    rule_version=scanner.VERSION,
                    evidence=self.finding.evidence,
                    campaign_key=self.key,
                    campaign_strong=True,
                    campaign_coverage=100,
                )
                for owner in owners
            ]
        )
        selection, _ = self.prepare()
        self.assertEqual(selection.items.count(), 1004)
        self.assertEqual(selection.totals["blogs"], 1004)
        self.assertEqual(
            models.User.objects.filter(pk__in=[u.pk for u in extra]).count(), 1001
        )
