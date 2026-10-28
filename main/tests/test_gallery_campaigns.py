from django.test import TestCase
from django.urls import reverse

from main import gallery_campaigns as gallery
from main import models
from main.tests.test_moderation_campaigns import GALLERY, posts
from main.tests.test_promotional_spam import scan

KNOWN = GALLERY.replace("https://offers.example/gallery", "https://go.cbrop.com/prse")
OTHER = KNOWN.replace("https://go.cbrop.com/prse", "https://sea.onlyfuns.win/ydt")
BUTTON = "[![View Full Gallery](https://i.postimg.cc/3Rbhqkz6/cta-1-gradient-pill.png)](https://go.cbrop.com/prse)\n"
VARIANT = (
    (BUTTON + KNOWN)
    .replace("Curated gallery | 3 images", "Gallery highlights")
    .replace("](https://go.cbrop.com/prse)", "](https://go.cbrop.com/prse?f=mataroa)")
)


class GalleryMatcherTest(TestCase):
    def test_evidence_combination_and_variants(self):
        for body in [
            KNOWN,
            OTHER,
            KNOWN.replace("Free porn videos.", "Wallpaper collection."),
            VARIANT,
            VARIANT.replace("go.cbrop.com/prse", "new-destination.example/gallery"),
        ]:
            with self.subTest(body=str(body)[:80]):
                self.assertIsNotNone(gallery.match(body))
        for body in [
            None,
            GALLERY,
            "View Full Gallery https://go.cbrop.com/prse",
            "https://go.cbrop.com/prse",
            "A curated gallery of furniture.",
            "https://example.com/?ref=mataroa",
            "https://i.postimg.cc/3Rbhqkz6/cta-1-gradient-pill.png",
            "```markdown\n" + KNOWN + "\n```",
            "<!--" + KNOWN + "-->",
            KNOWN.replace("go.cbrop.com", "go.cbrop.com.evil.example"),
            VARIANT.replace("cta-1-gradient-pill.png", "ordinary.png"),
        ]:
            with self.subTest(body=str(body)[:80]):
                self.assertIsNone(gallery.match(body))
        self.assertIsNone(gallery.match(KNOWN, {"go.cbrop.com"}))

    def test_button_is_not_a_second_thumbnail(self):
        body = BUTTON + KNOWN.replace(
            "[![Preview two](https://images.example/two.jpg)](https://go.cbrop.com/prse)",
            "",
        )
        self.assertIsNone(gallery.match(body))

    def test_single_preview_needs_all_three_corroborating_markers(self):
        body = VARIANT.replace(
            "[![Preview two](https://images.example/two.jpg)](https://go.cbrop.com/prse?f=mataroa)",
            "",
        )
        self.assertIsNotNone(gallery.match(body))
        self.assertIsNone(
            gallery.match(body.replace("cta-1-gradient-pill.png", "ordinary.png"))
        )
        self.assertIsNone(gallery.match(body.replace("?f=mataroa", "")))
        self.assertIsNone(
            gallery.match(body.replace("go.cbrop.com/prse", "other.example/gallery"))
        )


class GalleryArchiveTest(TestCase):
    def setUp(self):
        self.owner = models.User.objects.create(username="gallery-network")

    def finding(self):
        scan()
        return models.PromotionalSpamFinding.objects.get(owner=self.owner)

    def test_rotating_destinations_and_nonsexual_content_share_full_archive_group(self):
        posts(
            self.owner,
            [
                KNOWN,
                OTHER,
                KNOWN.replace("Free porn videos.", "My wallpaper collection."),
            ],
        )
        finding = self.finding()
        self.assertEqual(finding.campaign_key, gallery.KEY)
        self.assertEqual(finding.campaign_coverage, 100)
        self.assertTrue(finding.campaign_strong)
        self.assertEqual(finding.evidence["campaign"]["checked"], 3)
        self.assertIn("gallery_promotion", finding.evidence["category_reasons"])
        self.assertTrue(
            finding.groups.filter(kind="category", key="gallery_promotion").exists()
        )

    def test_category_does_not_claim_every_template_is_pornography(self):
        posts(
            self.owner,
            [KNOWN.replace("Free porn videos.", "Wallpaper collection.")] * 2,
        )
        finding = self.finding()
        self.assertTrue(finding.campaign_strong)
        self.assertNotIn("pornography", finding.evidence["category_reasons"])

    def test_single_and_mixed_blogs_cannot_be_strong_even_at_90_percent(self):
        posts(self.owner, [KNOWN])
        self.assertFalse(self.finding().campaign_strong)
        models.Post.objects.filter(owner=self.owner).delete()
        posts(self.owner, [KNOWN] * 9 + ["An ordinary personal essay."])
        finding = self.finding()
        self.assertEqual(finding.campaign_coverage, 90)
        self.assertFalse(finding.campaign_strong)
        self.assertIn(
            "personal essay", finding.evidence["campaign"]["counterexample"]["excerpt"]
        )

    def test_discovery_outside_samples_and_in_middle_of_long_body(self):
        rows = posts(self.owner, ["Ordinary notes."] * 30)
        models.Post.objects.filter(pk=rows[13].pk).update(
            body="Intro " * 2000 + KNOWN + "Tail " * 2000
        )
        finding = self.finding()
        self.assertEqual(finding.campaign_key, gallery.KEY)
        self.assertEqual(finding.evidence["campaign"]["matched"], 1)
        self.assertEqual(finding.evidence["campaign"]["checked"], 30)
        self.assertFalse(finding.campaign_strong)

    def test_hidden_page_is_in_denominator_and_blocks_deletion(self):
        posts(self.owner, [KNOWN] * 2)
        models.Page.objects.create(
            owner=self.owner,
            title="Private notes",
            slug="notes",
            body="Ordinary notes",
            is_hidden=True,
        )
        finding = self.finding()
        self.assertFalse(finding.campaign_strong)
        self.assertEqual(finding.evidence["campaign"]["checked"], 3)
        self.assertEqual(finding.evidence["campaign"]["counterexample"]["kind"], "page")

    def test_removal_of_template_removes_group_and_category(self):
        rows = posts(self.owner, [KNOWN] * 2)
        self.assertTrue(self.finding().campaign_strong)
        models.Post.objects.filter(pk__in=[p.pk for p in rows]).update(
            body="Ordinary writing"
        )
        finding = self.finding()
        self.assertNotEqual(finding.campaign_key, gallery.KEY)
        self.assertNotIn("gallery_promotion", finding.evidence["category_reasons"])

    def test_incomplete_archive_cannot_be_strong(self):
        result = {
            "checked": 2,
            "matched": 2,
            "example": {},
            "counterexample": None,
            "domains": {},
        }
        from collections import Counter

        result["domains"] = Counter()
        self.assertFalse(gallery.campaign(result, 3)["strong"])

    def test_protected_and_mixed_accounts_are_excluded_from_selection(self):
        admin = models.User.objects.create(
            username="reviewer", is_superuser=True, is_staff=True
        )
        for i in range(3):
            user = models.User.objects.create(username=f"candidate-{i}")
            posts(user, [KNOWN, OTHER])
        posts(self.owner, [KNOWN] * 9 + ["Personal notes"])
        for flag in (
            "is_premium",
            "is_grandfathered",
            "is_approved",
            "is_staff",
            "is_superuser",
        ):
            user = models.User.objects.create(username=flag, **{flag: True})
            posts(user, [KNOWN, OTHER])
        scan()
        self.client.force_login(admin)
        response = self.client.get(reverse("moderation_campaign", args=[gallery.KEY]))
        self.assertContains(response, "Archive coverage")
        self.assertContains(response, "Every post and page is checked")
        self.assertEqual(response.context["eligible"], 3)
        response = self.client.post(
            reverse("moderation_campaign_prepare", args=[gallery.KEY])
        )
        self.assertEqual(response.status_code, 302)
        selection = models.ModerationDeletionSelection.objects.get()
        self.assertEqual(
            set(selection.items.values_list("username", flat=True)),
            {f"candidate-{i}" for i in range(3)},
        )
        self.assertEqual(selection.totals["blogs"], 3)
        self.assertTrue(
            all(
                example["full_archive"]
                for group in selection.examples.values()
                for example in group
            )
        )
