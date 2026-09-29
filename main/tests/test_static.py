from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from main import models
from main.views import general


class StaticTestCase(TestCase):
    def setUp(self):
        cache.delete(general.TRANSPARENCY_STATS_CACHE_KEY)

    def tearDown(self):
        cache.delete(general.TRANSPARENCY_STATS_CACHE_KEY)

    def test_methodology(self):
        response = self.client.get(reverse("methodology"))
        self.assertEqual(response.status_code, 200)

    def test_transparency(self):
        old_user = models.User.objects.create(username="old-user")
        models.User.objects.filter(pk=old_user.pk).update(
            date_joined=timezone.now() - timedelta(days=60)
        )
        new_user = models.User.objects.create(username="new-user")
        models.Post.objects.create(owner=old_user, title="Old user post", slug="old")
        models.Post.objects.create(owner=new_user, title="New user post", slug="new")

        response = self.client.get(reverse("transparency"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total")
        self.assertEqual(response.context["active_users"], 2)
        self.assertEqual(response.context["active_nonnew_users"], 1)

    def test_transparency_statistics_are_cached(self):
        with patch.object(
            general,
            "_calculate_transparency_stats",
            wraps=general._calculate_transparency_stats,
        ) as calculate_stats:
            self.client.get(reverse("transparency"))
            self.client.get(reverse("transparency"))

        self.assertEqual(calculate_stats.call_count, 1)

    def test_guides_markdown(self):
        response = self.client.get(reverse("guides_markdown"))
        self.assertEqual(response.status_code, 200)

    def test_guides_images(self):
        response = self.client.get(reverse("guides_images"))
        self.assertEqual(response.status_code, 200)

    def test_comparisons(self):
        response = self.client.get(reverse("comparisons"))
        self.assertEqual(response.status_code, 200)

    def test_export(self):
        """Test export index page as an anon user."""
        response = self.client.get(reverse("export_index"))
        self.assertEqual(response.status_code, 200)
