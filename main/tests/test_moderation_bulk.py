from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from main import models, moderation_bulk, moderation_campaigns
from main.tests.test_moderation_campaigns import GALLERY, posts
from main.tests.test_moderation_gambling import offer
from main.tests.test_promotional_spam import scan


class BulkSelectionTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(
            username="moderator", is_staff=True, is_superuser=True
        )
        self.gallery = []
        for i in range(3):
            owner = models.User.objects.create(username=f"gallery-{i}")
            posts(owner, [GALLERY] * 3)
            self.gallery.append(owner)
        self.gambling = []
        for i, domain in enumerate(("mostbet-deu-de.com", "t83-slot.cloud")):
            owner = models.User.objects.create(username=f"gambling-{i}")
            posts(owner, [offer(f"https://{domain}/")])
            self.gambling.append(owner)
        self.premium = models.User.objects.create(username="premium", is_premium=True)
        posts(self.premium, [offer("https://mostbet.web.pk/")])
        self.ordinary = models.User.objects.create(username="gardener")
        posts(self.ordinary, ["Notes about tomatoes"])
        scan()
        self.url = reverse("moderation_bulk")
        self.prepare_url = reverse("moderation_bulk_prepare")
        self.client.force_login(self.admin)

    def owners(self, response):
        return {row["owner_id"] for row in response.context["rows"]}

    def selection(self, data):
        response = self.client.post(self.prepare_url, data)
        self.assertEqual(response.status_code, 302)
        selection = models.ModerationDeletionSelection.objects.latest("created_at")
        return selection, reverse("moderation_campaign_action", args=[selection.pk])

    def test_superuser_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.prepare_url).status_code, 404)
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        response = self.client.post(self.prepare_url, {"ids": str(self.ordinary.pk)})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(models.ModerationDeletionSelection.objects.exists())

    def test_index_links_to_bulk_page(self):
        self.assertContains(self.client.get(reverse("moderation_index")), self.url)

    def test_lists_all_blogs_newest_first_and_protected_rows_cannot_be_ticked(self):
        response = self.client.get(self.url)
        rows = response.context["rows"]
        self.assertEqual([row["owner_id"] for row in rows][0], self.ordinary.pk)
        self.assertEqual(len(rows), 8)
        self.assertEqual(response.context["eligible"], 6)
        self.assertContains(
            response, f'class="bulk-select" value="{self.gambling[0].pk}"'
        )
        self.assertNotContains(
            response, f'class="bulk-select" value="{self.premium.pk}"'
        )
        self.assertNotContains(response, f'class="bulk-select" value="{self.admin.pk}"')

    def test_filters(self):
        cases = [
            ({"category": "gambling"}, {u.pk for u in self.gambling + [self.premium]}),
            ({"term": "mostbet"}, {self.gambling[0].pk, self.premium.pk}),
            ({"term": "bet"}, None),  # too short: ignored
            ({"category": "nonsense"}, None),
        ]
        everyone = self.owners(self.client.get(self.url))
        for params, expected in cases:
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(self.owners(response), expected or everyone)

    def test_date_activity_and_status_filters(self):
        old = timezone.now() - timedelta(days=400)
        models.User.objects.filter(pk=self.ordinary.pk).update(date_joined=old)
        models.Post.objects.filter(owner=self.ordinary).update(
            created_at=old
        )  # not active
        response = self.client.get(
            self.url, {"joined_to": (old + timedelta(days=1)).date().isoformat()}
        )
        self.assertEqual(self.owners(response), {self.ordinary.pk})
        response = self.client.get(
            self.url, {"joined_from": (old + timedelta(days=2)).date().isoformat()}
        )
        self.assertNotIn(self.ordinary.pk, self.owners(response))
        response = self.client.get(self.url, {"active": "1"})
        self.assertNotIn(self.ordinary.pk, self.owners(response))
        self.assertIn(self.gambling[0].pk, self.owners(response))
        models.PromotionalSpamFinding.objects.filter(owner=self.gambling[1]).update(
            dismissed_at=timezone.now()
        )
        self.assertNotIn(self.gambling[1].pk, self.owners(self.client.get(self.url)))
        response = self.client.get(self.url, {"status": "dismissed"})
        self.assertEqual(self.owners(response), {self.gambling[1].pk})

    def test_recommendation_filter_and_labels_match_recommendation_sets(self):
        expected = {
            key: set(qs.values_list("owner_id", flat=True))
            for key, qs in moderation_campaigns.recommendation_sets().items()
        }
        self.assertEqual(expected["strong"], {u.pk for u in self.gallery})
        for key, owners in expected.items():
            with self.subTest(recommendation=key):
                response = self.client.get(
                    self.url, {"recommendation": key, "status": "all"}
                )
                self.assertEqual(self.owners(response), owners)
                label = moderation_campaigns.RECOMMENDATIONS[key]
                self.assertTrue(
                    all(
                        row["recommendation"] == label
                        for row in response.context["rows"]
                    )
                )

    def test_selected_ids_create_selection_and_delete_only_after_confirmation(self):
        ids = [self.gambling[0].pk, self.ordinary.pk, self.premium.pk, 999999]
        selection, action = self.selection(
            {"scope": "selected", "ids": ",".join(map(str, ids))}
        )
        self.assertEqual(
            set(selection.items.values_list("owner_id_snapshot", flat=True)),
            {self.gambling[0].pk, self.ordinary.pk},
        )
        self.assertEqual(selection.campaign_key, "")
        self.assertEqual(
            self.client.post(action, {"action": "process"}).status_code, 400
        )
        self.assertEqual(models.User.objects.count(), 8)
        self.client.post(action, {"action": "confirm", "acknowledge": "yes"})
        for _ in range(2):
            result = self.client.post(action, {"action": "process"}).json()
        self.assertEqual((result["deleted"], result["finished"]), (2, True))
        self.assertFalse(
            models.User.objects.filter(
                pk__in=[self.gambling[0].pk, self.ordinary.pk]
            ).exists()
        )
        self.assertTrue(models.User.objects.filter(pk=self.premium.pk).exists())

    def test_matching_scope_uses_filters_and_skips_protected(self):
        selection, _ = self.selection(
            {"scope": "matching", "category": "gambling", "status": "pending"}
        )
        self.assertEqual(
            set(selection.items.values_list("owner_id_snapshot", flat=True)),
            {u.pk for u in self.gambling},
        )

    def test_invalid_selections_are_rejected(self):
        for data in ({}, {"ids": ""}, {"ids": "1,abc"}, {"ids": str(self.premium.pk)}):
            with self.subTest(data=data):
                response = self.client.post(self.prepare_url, data)
                self.assertIn(response.status_code, (400, 409))
        self.assertEqual(self.client.get(self.prepare_url).status_code, 405)
        self.assertFalse(models.ModerationDeletionSelection.objects.exists())

    def test_changed_blogs_are_skipped(self):
        selection, action = self.selection(
            {"ids": ",".join(str(u.pk) for u in self.gambling)}
        )
        models.User.objects.filter(pk=self.gambling[0].pk).update(is_approved=True)
        models.PromotionalSpamFinding.objects.filter(owner=self.gambling[1]).update(
            dismissed_at=timezone.now()
        )
        self.client.post(action, {"action": "confirm", "acknowledge": "yes"})
        for _ in range(2):
            result = self.client.post(action, {"action": "process"}).json()
        self.assertEqual((result["deleted"], result["skipped"]), (0, 2))

    def test_page_size(self):
        self.assertEqual(moderation_bulk.PAGE_SIZE, 500)
