from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from main import models, moderation_campaigns, support_impersonation
from main import promotional_spam as spam
from main.tests.test_promotional_spam import scan


def support_body(number="1-(844)-470-2814", count=8):
    return f"For wallet support and withdrawal troubleshooting, call {number}. " * count


class SupportPatternTest(SimpleTestCase):
    def test_observed_brands_and_support_titles_without_links(self):
        for title in (
            "Why Is Uniswap Not Working? (Troubleshooting Guide)",
            "How to Withdraw Money from Coinbase Wallet to Bank Account?",
            "How To Fidelity 401k Withdrawal?",
            "MetaMask customer support",
            "Why Is My MetaMask Transaction Stuck? (Quick Solutions)",
            "Why Has My Paybis Deposit Not Arrived? (Deposit Problems)",
        ):
            with self.subTest(title=title):
                match = support_impersonation.match(title, support_body())
                self.assertEqual(
                    match["phones"], [{"number": "+18444702814", "count": 8}]
                )

    def test_equivalent_phone_formats_are_counted_together(self):
        body = "Wallet support: " + " / ".join(
            [
                "+1(844)-470-2814",
                "844.470.2814",
                "(844) 470 2814",
                "１－（８４４）－４７０－２８１４",
                "1–844–470–2814",
                "844\u200b-470-2814",
            ]
        )
        match = support_impersonation.match("Uniswap support", body)
        self.assertEqual(match["phones"], [{"number": "+18444702814", "count": 6}])

    def test_normal_contact_details_and_brand_mentions_are_insufficient(self):
        for title, body in (
            ("Uniswap support", support_body(count=4)),
            (
                "Uniswap support",
                "Wallet support: " + " / ".join(f"844-470-{i:04}" for i in range(10)),
            ),
            ("Uniswap protocol research", support_body()),
            ("My local business support", support_body()),
            ("Uniswap support", "Record ID 8444702814. " * 10),
            ("Uniswap support", "Reference (844)-470-2814. " * 10),
            ("Uniswap support", "Check the official documentation."),
        ):
            with self.subTest(title=title, body=body):
                self.assertIsNone(support_impersonation.match(title, body))

    def test_warning_articles_are_not_flagged(self):
        for title in (
            "Uniswap support scam warning",
            "Fake Coinbase withdrawal helpline",
            "Beware of MetaMask support impersonation",
            "Uniswap support: don't call this number",
        ):
            with self.subTest(title=title):
                self.assertIsNone(support_impersonation.match(title, support_body()))


class SupportClassificationTest(TestCase):
    def setUp(self):
        self.owner = models.User.objects.create(username="support-articles")
        self.post = models.Post.objects.create(
            owner=self.owner,
            title="Why Is Uniswap Not Working?",
            slug="uniswap-support",
            body=support_body(),
            published_at=None,
        )

    def test_scan_filter_evidence_and_review_recommendation(self):
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertTrue(
            finding.groups.filter(kind="category", key="support_impersonation").exists()
        )
        self.assertNotIn("unclassified", finding.evidence["category_reasons"])
        self.assertEqual(finding.rule_version, spam.VERSION)
        self.assertIn("+18444702814 appears 8 times", finding.evidence["reasons"][0])
        self.assertEqual(finding.evidence["examples"][0]["id"], self.post.pk)
        self.assertFalse(finding.campaign_strong)
        self.assertTrue(
            moderation_campaigns.recommendation_sets()["review"]
            .filter(pk=finding.pk)
            .exists()
        )
        admin = models.User.objects.create(
            username="reviewer", is_superuser=True, is_staff=True
        )
        self.client.force_login(admin)
        response = self.client.get(
            reverse("moderation_spam"),
            {"category": "support_impersonation", "recommendation": "review"},
        )
        self.assertContains(response, "Support impersonation")
        self.assertContains(response, self.owner.username)
        self.assertContains(response, "+18444702814 appears 8 times")
        response = self.client.post(
            reverse("moderation_bulk_preview"),
            {"scope": "matching", "category": "support_impersonation"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["owner"].pk for row in response.context["rows"]], [self.owner.pk]
        )
        self.assertTrue(models.User.objects.filter(pk=self.owner.pk).exists())

    def test_hidden_pages_are_classified_and_removed_evidence_is_reclassified(self):
        self.post.delete()
        page = models.Page.objects.create(
            owner=self.owner,
            title="Coinbase withdrawal help",
            slug="help",
            body=support_body(),
            is_hidden=True,
        )
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        self.assertIn("support_impersonation", finding.evidence["category_reasons"])
        page.body = "My personal notes about using Coinbase."
        page.save()
        scan()
        finding.refresh_from_db()
        self.assertNotIn("support_impersonation", finding.evidence["category_reasons"])
        self.assertFalse(finding.groups.filter(key="support_impersonation").exists())

    def test_new_category_reopens_a_dismissal(self):
        self.post.body = "Notes about Uniswap."
        self.post.save()
        scan()
        finding = models.PromotionalSpamFinding.objects.get(owner=self.owner)
        finding.dismissed_at = timezone.now()
        finding.dismissed_baseline = spam.baseline(finding.score, finding.evidence)
        finding.save()
        self.post.body = support_body()
        self.post.save()
        scan()
        finding.refresh_from_db()
        self.assertIsNone(finding.dismissed_at)

    def test_protected_accounts_stay_protected(self):
        scan()
        for flag in (
            "is_premium",
            "is_grandfathered",
            "is_approved",
            "is_staff",
            "is_superuser",
        ):
            with self.subTest(flag=flag):
                models.User.objects.filter(pk=self.owner.pk).update(**{flag: True})
                sets = moderation_campaigns.recommendation_sets()
                self.assertTrue(sets["protected"].filter(owner=self.owner).exists())
                self.assertFalse(sets["review"].filter(owner=self.owner).exists())
                self.assertFalse(sets["strong"].filter(owner=self.owner).exists())
                models.User.objects.filter(pk=self.owner.pk).update(**{flag: False})
