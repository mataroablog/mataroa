from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from main import models


class PageCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)

    def test_page_create(self):
        data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        response = self.client.post(reverse("page_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(models.Page.objects.filter(title=data["title"]).exists())
        self.assertEqual(
            models.Page.objects.get(title=data["title"]).slug, data["slug"]
        )
        self.assertEqual(
            models.Page.objects.get(title=data["title"]).body, data["body"]
        )

    def test_page_invalid_slug(self):
        data = {
            "title": "New page",
            "slug": "rss",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        response = self.client.post(reverse("page_create"), data)
        self.assertContains(response, "slug is not allowed")
        self.assertFalse(models.Page.objects.filter(title=data["title"]).exists())

    def test_page_invalid_slug_p(self):
        data = {
            "title": "New page",
            "slug": "p",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        response = self.client.post(reverse("page_create"), data)
        self.assertContains(response, "slug is not allowed")
        self.assertFalse(models.Page.objects.filter(title=data["title"]).exists())


class PageCreateAnonTestCase(TestCase):
    def test_page_create_anon(self):
        data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        response = self.client.post(reverse("page_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue("login/" in response.url)
        self.assertFalse(models.Page.objects.filter(title=data["title"]).exists())


class PageDetailTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_page_detail(self):
        response = self.client.get(
            reverse("page_detail", args=(self.page.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data["title"])
        self.assertContains(response, self.data["body"])


class PageNonHiddenTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_page_non_hidden(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data["title"])
        self.assertContains(response, self.data["slug"])


class PageHiddenTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": True,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_page_hidden(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.data["title"])
        self.assertNotContains(response, self.data["slug"])


class PageUpdateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_get_update(self):
        response = self.client.get(
            reverse("page_update", args=(self.page.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)

    def test_page_update(self):
        new_data = {
            "title": "Updated page",
            "slug": "updated-page",
            "is_hidden": True,
            "body": "Updated sentence.",
        }
        self.client.post(
            reverse("page_update", args=(self.page.slug,)),
            new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        page_now = models.Page.objects.get(id=self.page.id)
        self.assertEqual(page_now.title, new_data["title"])
        self.assertEqual(page_now.slug, new_data["slug"])
        self.assertEqual(page_now.is_hidden, new_data["is_hidden"])
        self.assertEqual(page_now.body, new_data["body"])

    def test_page_update_invalid_slug(self):
        new_data = {
            "title": "Updated page",
            "slug": "p",
            "is_hidden": True,
            "body": "Updated sentence.",
        }
        response = self.client.post(
            reverse("page_update", args=(self.page.slug,)),
            new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertContains(response, "slug is not allowed")
        page_now = models.Page.objects.get(id=self.page.id)
        self.assertEqual(page_now.slug, self.data["slug"])


class PageUpdateAnonTestCase(TestCase):
    """Tests non logged in user cannot update page."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_page_update_anon(self):
        new_data = {
            "title": "Updated page",
            "slug": "updated-page",
            "is_hidden": True,
            "body": "Updated sentence.",
        }
        self.client.post(
            reverse("page_update", args=(self.page.slug,)),
            new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        page_now = models.Page.objects.get(id=self.page.id)
        self.assertEqual(page_now.title, self.data["title"])
        self.assertEqual(page_now.slug, self.data["slug"])
        self.assertEqual(page_now.is_hidden, self.data["is_hidden"])
        self.assertEqual(page_now.body, self.data["body"])


class PageUpdateNotOwnTestCase(TestCase):
    """Tests user cannot update other user's page."""

    def setUp(self):
        self.victim = models.User.objects.create(username="bob")
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.victim, **self.data)

        self.attacker = models.User.objects.create(username="alice")
        self.client.force_login(self.attacker)

    def test_page_update_not_own(self):
        new_data = {
            "title": "Updated page",
            "slug": "updated-page",
            "is_hidden": True,
            "body": "Updated sentence.",
        }
        self.client.post(
            reverse("page_update", args=(self.page.slug,)),
            new_data,
            HTTP_HOST=self.victim.username + "." + settings.CANONICAL_HOST,
        )
        page_now = models.Page.objects.get(id=self.page.id)
        self.assertEqual(page_now.title, self.data["title"])
        self.assertEqual(page_now.slug, self.data["slug"])
        self.assertEqual(page_now.is_hidden, self.data["is_hidden"])
        self.assertEqual(page_now.body, self.data["body"])


class PageDeleteTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_page_delete(self):
        self.client.post(
            reverse("page_delete", args=(self.page.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertFalse(
            models.Page.objects.filter(slug=self.data["slug"], owner=self.user).exists()
        )


class PageDeleteAnonTestCase(TestCase):
    """Tests non logged in user cannot delete page."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.user, **self.data)

    def test_page_delete_anon(self):
        self.client.post(
            reverse("page_delete", args=(self.page.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertTrue(
            models.Page.objects.filter(slug=self.data["slug"], owner=self.user).exists()
        )


class PageDeleteNotOwnTestCase(TestCase):
    """Tests user cannot delete other's page."""

    def setUp(self):
        self.victim = models.User.objects.create(username="bob")
        self.data = {
            "title": "New page",
            "slug": "new-page",
            "is_hidden": False,
            "body": "Content sentence.",
        }
        self.page = models.Page.objects.create(owner=self.victim, **self.data)

        self.attacker = models.User.objects.create(username="alice")
        self.client.force_login(self.attacker)

    def test_page_delete_not_own(self):
        self.client.post(
            reverse("page_delete", args=(self.page.slug,)),
            HTTP_HOST=self.victim.username + "." + settings.CANONICAL_HOST,
        )
        self.assertTrue(
            models.Page.objects.filter(
                slug=self.data["slug"], owner=self.victim
            ).exists()
        )
