from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from main import gambling_overview, models
from main.tests.test_moderation_campaigns import posts
from main.tests.test_promotional_spam import scan


def offer(url):
    return f"Casino sign up bonus and real money offers. [Visit]({url})"


class BrandKeyTest(SimpleTestCase):
    def test_brand_key(self):
        cases = {
            "mostbet-deu-de.com": "mostbet",
            "mostbet.web.pk": "mostbet",
            "www.mostbet.ng": "mostbet",
            "go.mostbet-sverige.com": "mostbet",
            "most-bet-casino.co.cz": "most",
            "t83-slot.cloud": "t83",
            "t883-com.com": "t883",
            "1-x-bet.com": "1xbet",
            "xn--80ak6aa92e.com": "xn--80ak6aa92e",
            "localhost": "localhost",
            "": "",
        }
        for domain, brand in cases.items():
            with self.subTest(domain=domain):
                self.assertEqual(gambling_overview.brand_key(domain), brand)

    def test_clean_term(self):
        self.assertEqual(gambling_overview.clean_term(" MostBet "), "mostbet")
        self.assertEqual(
            gambling_overview.clean_term("t83-slot.cloud"), "t83-slot.cloud"
        )
        for value in ("bet", "", None, "most bet", "mostbet%", "a" * 254):
            with self.subTest(value=value):
                self.assertEqual(gambling_overview.clean_term(value), "")


class GamblingOverviewTest(TestCase):
    def setUp(self):
        self.admin = models.User.objects.create(
            username="moderator", is_staff=True, is_superuser=True
        )
        self.mostbet = []
        for i, domain in enumerate(
            ("mostbet-deu-de.com", "mostbet.web.pk", "mostbet-sverige.com")
        ):
            owner = models.User.objects.create(username=f"mostbet-{i}")
            posts(owner, [offer(f"https://{domain}/")] * 2)
            self.mostbet.append(owner)
        self.t83 = models.User.objects.create(username="t83")
        posts(self.t83, [offer("https://t83-slot.cloud/")])
        self.hop = models.User.objects.create(username="hop")
        posts(self.hop, [offer("https://telegra.ph/casino")])
        self.ordinary = models.User.objects.create(username="gardener")
        posts(self.ordinary, ["Notes about tomatoes. [Seeds](https://seeds.example/)"])
        scan()
        self.url = reverse("moderation_gambling")
        self.prepare_url = reverse("moderation_gambling_prepare")
        self.client.force_login(self.admin)

    def prepare(self, term="mostbet"):
        response = self.client.post(self.prepare_url, {"term": term})
        self.assertEqual(response.status_code, 302)
        selection = models.ModerationDeletionSelection.objects.latest("created_at")
        return selection, reverse("moderation_campaign_action", args=[selection.pk])

    def test_superuser_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.prepare_url).status_code, 404)
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(
            self.client.post(self.prepare_url, {"term": "mostbet"}).status_code, 404
        )
        self.assertFalse(models.ModerationDeletionSelection.objects.exists())

    def test_index_links_to_overview(self):
        response = self.client.get(reverse("moderation_index"))
        self.assertContains(response, self.url)

    def test_overview_counts_brands_and_shared_hosts(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        summary = response.context["summary"]
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["eligible"], 5)
        self.assertEqual(summary["posts"], 8)
        destinations = response.context["destinations"]
        brands = {row["brand"]: row for row in destinations["brands"]}
        self.assertEqual(brands["mostbet"]["blogs"], 3)
        self.assertEqual(brands["mostbet"]["domain_count"], 3)
        self.assertEqual(brands["t83"]["blogs"], 1)
        domains = {row["domain"] for row in destinations["domains"]}
        self.assertNotIn("telegra.ph", domains)
        self.assertNotIn("seeds.example", domains)
        self.assertEqual(
            [row["domain"] for row in destinations["shared"]], ["telegra.ph"]
        )
        self.assertEqual(response.context["signups"][0]["blogs"], 5)
        self.assertEqual(response.context["still_posting"]["blogs"], 5)

    def test_dismissed_blogs_are_excluded(self):
        models.PromotionalSpamFinding.objects.filter(owner=self.t83).update(
            dismissed_at=timezone.now()
        )
        response = self.client.get(self.url)
        self.assertEqual(response.context["summary"]["total"], 4)
        self.assertEqual(response.context["summary"]["dismissed"], 1)

    def test_most_viewed(self):
        post = models.Post.objects.filter(owner=self.mostbet[0]).first()
        models.AnalyticPost.objects.bulk_create(
            [models.AnalyticPost(post=post) for _ in range(3)]
        )
        models.AnalyticPage.objects.create(user=self.mostbet[0], path="index")
        rows = self.client.get(self.url).context["most_viewed"]["rows"]
        self.assertEqual(rows[0]["owner_id"], self.mostbet[0].pk)
        self.assertEqual((rows[0]["post_views"], rows[0]["page_views"]), (3, 1))

    def test_term_search(self):
        response = self.client.get(self.url, {"term": "MostBet"})
        self.assertEqual(response.context["term"], "mostbet")
        self.assertEqual(response.context["term_total"], 3)
        self.assertEqual(
            {f.owner_id for f in response.context["term_page"]},
            {u.pk for u in self.mostbet},
        )
        self.assertContains(response, "Review 3 blogs for deletion")
        # Shared hosts never match, and short terms are too broad.
        response = self.client.get(self.url, {"term": "telegra"})
        self.assertEqual(response.context["term_total"], 0)
        response = self.client.get(self.url, {"term": "bet"})
        self.assertEqual(response.context["term"], "")
        self.assertNotIn("term_page", response.context)

    def test_prepare_rejects_invalid_terms(self):
        for term in ("", "bet", "most bet"):
            with self.subTest(term=term):
                response = self.client.post(self.prepare_url, {"term": term})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.prepare_url).status_code, 405)
        self.assertFalse(models.ModerationDeletionSelection.objects.exists())

    def test_selection_excludes_protected_and_deletes_only_after_confirmation(self):
        models.User.objects.filter(pk=self.mostbet[0].pk).update(is_premium=True)
        selection, action = self.prepare()
        self.assertEqual(selection.campaign_key, "")
        self.assertEqual(
            set(selection.items.values_list("owner_id_snapshot", flat=True)),
            {u.pk for u in self.mostbet[1:]},
        )
        page = self.client.get(
            reverse("moderation_campaign_selection", args=[selection.pk])
        )
        self.assertContains(page, "domains containing “mostbet”")
        self.assertEqual(
            self.client.post(action, {"action": "process"}).status_code, 400
        )
        self.assertEqual(
            models.User.objects.filter(username__startswith="most").count(), 3
        )
        self.client.post(action, {"action": "confirm", "acknowledge": "yes"})
        for _ in range(2):
            result = self.client.post(action, {"action": "process"}).json()
        self.assertTrue(result["finished"])
        self.assertEqual(result["deleted"], 2)
        remaining = set(models.User.objects.values_list("username", flat=True))
        self.assertEqual(
            remaining, {"moderator", "mostbet-0", "t83", "hop", "gardener"}
        )

    def test_changed_or_dismissed_blogs_are_skipped(self):
        selection, action = self.prepare()
        models.PromotionalSpamFinding.objects.filter(owner=self.mostbet[0]).update(
            dismissed_at=timezone.now()
        )
        models.Post.objects.create(
            owner=self.mostbet[1], title="New", slug="new", body="Written after review"
        )
        models.User.objects.filter(pk=self.mostbet[2].pk).update(is_approved=True)
        self.client.post(action, {"action": "confirm", "acknowledge": "yes"})
        for _ in range(3):
            result = self.client.post(action, {"action": "process"}).json()
        self.assertEqual((result["deleted"], result["skipped"]), (0, 3))
        self.assertEqual(
            models.User.objects.filter(pk__in=[u.pk for u in self.mostbet]).count(), 3
        )

    def test_campaign_breadcrumb_unchanged_for_campaign_selections(self):
        selection, _ = self.prepare()
        page = self.client.get(
            reverse("moderation_campaign_selection", args=[selection.pk])
        )
        self.assertContains(page, reverse("moderation_gambling"))
        self.assertNotContains(page, reverse("moderation_campaigns") + '">')
