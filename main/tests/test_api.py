from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from main import models, scheme


class APIDocsAnonTestCase(TestCase):
    def test_docs_get(self):
        response = self.client.get(reverse("api_docs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API")


class APIDocsTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)

    def test_docs_get(self):
        response = self.client.get(reverse("api_docs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API")
        self.assertContains(response, self.user.api_key)


class APIResetKeyTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.api_key = self.user.api_key
        self.client.force_login(self.user)

    def test_api_key_reset_get(self):
        response = self.client.get(reverse("api_reset"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset API key")

    def test_api_key_reset_post(self):
        response = self.client.post(reverse("api_reset"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API key has been reset")
        new_api_key = models.User.objects.get(username="alice").api_key
        self.assertNotEqual(self.api_key, new_api_key)


class APIListAnonTestCase(TestCase):
    """Test cases for anonymous POST / GET / PATCH / DELETE on /api/posts/."""

    def test_posts_get(self):
        response = self.client.get(reverse("api_posts"))
        self.assertEqual(response.status_code, 403)

    def test_posts_post(self):
        response = self.client.post(reverse("api_posts"))
        self.assertEqual(response.status_code, 403)

    def test_posts_patch(self):
        response = self.client.patch(reverse("api_posts"))
        self.assertEqual(response.status_code, 405)

    def test_posts_delete(self):
        response = self.client.delete(reverse("api_posts"))
        self.assertEqual(response.status_code, 405)


class APISingleAnonTestCase(TestCase):
    """Test cases for anonymous GET / PATCH / DELETE on /api/posts/<post-slug>/."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        self.post = models.Post.objects.create(**data)

    def test_post_get(self):
        response = self.client.get(
            reverse("api_post", args=(self.post.slug,)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_post_post(self):
        response = self.client.post(
            reverse("api_post", args=(self.post.slug,)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405)

    def test_post_patch(self):
        response = self.client.patch(
            reverse("api_post", args=(self.post.slug,)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_post_delete(self):
        response = self.client.delete(
            reverse("api_post", args=(self.post.slug,)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)


class APIListPostAuthTestCase(TestCase):
    """Test cases for auth-related POST /api/posts/ aka post creation."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")

    def test_posts_post_no_auth(self):
        response = self.client.post(reverse("api_posts"))
        self.assertEqual(response.status_code, 403)

    def test_posts_post_bad_auth(self):
        response = self.client.post(
            reverse("api_posts"), HTTP_AUTHORIZATION=f"Nearer {self.user.api_key}"
        )
        self.assertEqual(response.status_code, 403)

    def test_posts_post_wrong_auth(self):
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION="Bearer 12345678901234567890123456789012",
        )
        self.assertEqual(response.status_code, 403)

    def test_posts_post_good_auth(self):
        response = self.client.post(
            reverse("api_posts"), HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}"
        )
        self.assertEqual(response.status_code, 400)


class APIListPostTestCase(TestCase):
    """Test cases for POST /api/posts/ aka post creation."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")

    def test_posts_post_no_title(self):
        data = {
            "body": "This is my post with no title key",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data=data,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(models.Post.objects.all().count(), 0)

    def test_posts_post_no_body(self):
        data = {
            "title": "First Post no body key",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        self.assertEqual(models.Post.objects.all().first().body, "")
        self.assertEqual(models.Post.objects.all().count(), 1)

    def test_posts_post_bogus_key(self):
        data = {
            "randomkey": "random value",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data=data,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(models.Post.objects.all().count(), 0)

    def test_posts_post_no_published_at(self):
        data = {
            "title": "First Post",
            "body": "## Welcome\n\nThis is my first sentence.",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(models.Post.objects.all().first().published_at, None)
        models.Post.objects.all().first().delete()

    def test_posts_post_other_owner(self):
        user_b = models.User.objects.create(username="bob")
        data = {
            "title": "First Post",
            "body": "## Welcome\n\nThis is my first sentence.",
            "owner_id": user_b.id,
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().owner_id, self.user.id)
        models.Post.objects.all().first().delete()

    def test_posts_post(self):
        data = {
            "title": "First Post",
            "body": "## Welcome\n\nThis is my first sentence.",
            "published_at": "2020-01-23",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(
            models.Post.objects.all().first().published_at, date(2020, 1, 23)
        )
        self.assertTrue(response.json()["ok"])
        self.assertEqual(
            response.json()["slug"], models.Post.objects.all().first().slug
        )
        self.assertEqual(
            response.json()["url"],
            scheme.get_protocol()
            + models.Post.objects.all().first().get_absolute_url(),
        )
        models.Post.objects.all().first().delete()


class APIListPatchAuthTestCase(TestCase):
    """Test cases for auth-related PATCH /api/posts/<post-slug>/ aka post update."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            body="## Hey\n\nHey world.",
            owner=self.user,
        )

    def test_post_get(self):
        response = self.client.get(reverse("api_post", args=(self.post.slug,)))
        self.assertEqual(response.status_code, 403)

    def test_post_post(self):
        response = self.client.post(reverse("api_post", args=(self.post.slug,)))
        self.assertEqual(response.status_code, 405)

    def test_post_patch_no_auth(self):
        response = self.client.patch(reverse("api_post", args=(self.post.slug,)))
        self.assertEqual(response.status_code, 403)

    def test_post_patch_bad_auth(self):
        response = self.client.patch(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION=f"Nearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 403)

    def test_post_patch_wrong_auth(self):
        response = self.client.patch(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION="Bearer 12345678901234567890123456789012",
        )
        self.assertEqual(response.status_code, 403)


class APIListPatchTestCase(TestCase):
    """Test cases for PATCH /api/posts/<post-slug>/ aka post update."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")

    def test_post_patch(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.patch(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "title": "New world",
                "slug": "new-world",
                "body": "new body",
                "published_at": "2019-07-02",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, "New world")
        self.assertEqual(models.Post.objects.all().first().slug, "new-world")
        self.assertEqual(models.Post.objects.all().first().body, "new body")
        self.assertEqual(
            models.Post.objects.all().first().published_at, date(2019, 7, 2)
        )
        self.assertTrue(response.json()["ok"])
        self.assertEqual(
            response.json()["url"],
            scheme.get_protocol()
            + models.Post.objects.all().first().get_absolute_url(),
        )
        models.Post.objects.all().first().delete()

    def test_post_patch_nonexistent_post(self):
        response = self.client.get(
            reverse("api_post", args=("nonexistent-post",)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "title": "New world",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"ok": False, "error": "Not found."})

    def test_post_patch_no_body(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.patch(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "title": "New world",
                "slug": "new-world",
                "published_at": "2019-07-02",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, "New world")
        self.assertEqual(models.Post.objects.all().first().slug, "new-world")
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(
            models.Post.objects.all().first().published_at, date(2019, 7, 2)
        )
        models.Post.objects.all().first().delete()

    def test_post_patch_no_slug(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.patch(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "title": "New world",
                "published_at": "2019-07-02",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, "New world")
        self.assertEqual(models.Post.objects.all().first().slug, data["slug"])
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(
            models.Post.objects.all().first().published_at, date(2019, 7, 2)
        )
        models.Post.objects.all().first().delete()

    def test_post_patch_invalid_slug(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.patch(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "slug": "slug with spaces is invalid",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        self.assertEqual(models.Post.objects.all().first().slug, data["slug"])
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(
            models.Post.objects.all().first().published_at, data["published_at"]
        )
        models.Post.objects.all().first().delete()

    def test_post_patch_invalid_key(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.patch(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "invalid": "random key value",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        self.assertEqual(models.Post.objects.all().first().slug, data["slug"])
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(
            models.Post.objects.all().first().published_at, data["published_at"]
        )
        models.Post.objects.all().first().delete()

    def test_post_patch_other_user_post(self):
        """Test changing another user's blog post is not allowed."""

        user_b = models.User.objects.create(username="bob")
        data = {
            "owner": user_b,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.patch(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
            data={
                "title": "Hi Bob, it's Alice",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        models.Post.objects.all().first().delete()


class APIGetAuthTestCase(TestCase):
    """Test cases for auth-related GET /api/posts/<post-slug>/ aka post retrieve."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            body="## Hey\n\nHey world.",
            owner=self.user,
        )

    def test_post_get_no_auth(self):
        response = self.client.get(reverse("api_post", args=(self.post.slug,)))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Not authorized."})

    def test_post_get_bad_auth(self):
        response = self.client.get(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION=f"Nearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Not authorized."})

    def test_post_get_wrong_auth(self):
        response = self.client.get(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION="Bearer 12345678901234567890123456789012",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Not authorized."})


class APIGetTestCase(TestCase):
    """Test cases for GET /api/posts/<post-slug>/ aka post retrieve."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")

    def test_post_get(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.get(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 1)
        self.assertEqual(models.Post.objects.all().first().title, data["title"])
        self.assertEqual(models.Post.objects.all().first().body, data["body"])
        self.assertEqual(models.Post.objects.all().first().slug, data["slug"])
        self.assertEqual(models.Post.objects.all().first().owner, self.user)
        self.assertEqual(
            models.Post.objects.all().first().published_at, data["published_at"]
        )
        self.assertTrue(response.json()["ok"])
        self.assertEqual(
            response.json()["url"],
            scheme.get_protocol()
            + models.Post.objects.all().first().get_absolute_url(),
        )
        models.Post.objects.all().first().delete()

    def test_post_get_nonexistent(self):
        response = self.client.get(
            reverse("api_post", args=("nonexistent-post",)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(models.Post.objects.all().count(), 0)
        self.assertFalse(response.json()["ok"])


class APIDeleteAuthTestCase(TestCase):
    """Test cases for auth-related DELETE /api/posts/<post-slug>/ aka post retrieve."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            body="## Hey\n\nHey world.",
            owner=self.user,
        )

    def test_post_delete_no_auth(self):
        response = self.client.delete(reverse("api_post", args=(self.post.slug,)))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Not authorized."})

    def test_post_delete_bad_auth(self):
        response = self.client.delete(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION=f"Nearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Not authorized."})

    def test_post_delete_wrong_auth(self):
        response = self.client.delete(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION="Bearer 12345678901234567890123456789012",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Not authorized."})

    def test_post_delete_other_user(self):
        user_b = models.User.objects.create(username="bob")
        response = self.client.delete(
            reverse("api_post", args=(self.post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {user_b.api_key}",
        )
        self.assertEqual(response.status_code, 404)


class APIDeleteTestCase(TestCase):
    """Test cases for DELETE /api/posts/<post-slug>/ aka post retrieve."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")

    def test_post_delete(self):
        data = {
            "owner": self.user,
            "title": "Hello world",
            "slug": "hello-world",
            "body": "## Hey\n\nHey world.",
            "published_at": date(2020, 7, 2),
        }
        post = models.Post.objects.create(**data)
        response = self.client.delete(
            reverse("api_post", args=(post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 0)
        self.assertTrue(response.json()["ok"])

    def test_post_get_nonexistent(self):
        response = self.client.get(
            reverse("api_post", args=("nonexistent-post",)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(models.Post.objects.all().count(), 0)
        self.assertFalse(response.json()["ok"])


class APIListGetTestCase(TestCase):
    """Test cases for GET /api/posts/ aka post list."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post_a = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            body="## Hey\n\nHey world.",
            published_at=date(2020, 1, 1),
            owner=self.user,
        )
        self.post_b = models.Post.objects.create(
            title="Bye world",
            slug="bye-world",
            body="## Bye\n\nBye world.",
            published_at=date(2020, 9, 14),
            owner=self.user,
        )

    def test_posts_get(self):
        response = self.client.get(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Post.objects.all().count(), 2)
        self.assertTrue(response.json()["ok"])
        post_list = response.json()["post_list"]
        self.assertEqual(len(post_list), 2)
        self.assertIn(
            {
                "title": "Hello world",
                "slug": "hello-world",
                "body": "## Hey\n\nHey world.",
                "published_at": "2020-01-01",
                "url": f"{scheme.get_protocol()}//{self.user.username}.{settings.CANONICAL_HOST}/blog/hello-world/",
            },
            post_list,
        )
        self.assertIn(
            {
                "title": "Bye world",
                "slug": "bye-world",
                "body": "## Bye\n\nBye world.",
                "published_at": "2020-09-14",
                "url": f"{scheme.get_protocol()}//{self.user.username}.{settings.CANONICAL_HOST}/blog/bye-world/",
            },
            post_list,
        )


class APISingleGetTestCase(TestCase):
    """Test posts with the same slug return across different users."""

    def setUp(self):
        # user 1
        self.user1 = models.User.objects.create(username="alice")
        self.data = {
            "title": "Test 1",
            "published_at": "2021-06-01",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user1.api_key}",
            content_type="application/json",
            data=self.data,
        )
        self.assertEqual(response.status_code, 200)
        # user 2, same post
        self.user2 = models.User.objects.create(username="bob")
        self.data = {
            "title": "Test 1",
            "published_at": "2021-06-02",
        }
        response = self.client.post(
            reverse("api_posts"),
            HTTP_AUTHORIZATION=f"Bearer {self.user2.api_key}",
            content_type="application/json",
            data=self.data,
        )
        self.assertEqual(response.status_code, 200)
        # verify objects
        self.assertEqual(models.Post.objects.all().count(), 2)
        self.assertEqual(models.Post.objects.all()[0].slug, "test-1")
        self.assertEqual(models.Post.objects.all()[1].slug, "test-1")

    def test_get(self):
        # user 1
        response = self.client.get(
            reverse("api_post", args=("test-1",)),
            HTTP_AUTHORIZATION=f"Bearer {self.user1.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["published_at"], "2021-06-01")
        # user 2
        response = self.client.get(
            reverse("api_post", args=("test-1",)),
            HTTP_AUTHORIZATION=f"Bearer {self.user2.api_key}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["published_at"], "2021-06-02")


class APICommentsAnonTestCase(TestCase):
    def test_comments_requires_auth(self):
        response = self.client.get(reverse("api_comments"))
        self.assertEqual(response.status_code, 403)

    def test_post_comments_requires_auth(self):
        response = self.client.get(reverse("api_post_comments", args=("hello-world",)))
        self.assertEqual(response.status_code, 403)

    def test_comments_pending_requires_auth(self):
        response = self.client.get(reverse("api_comments_pending"))
        self.assertEqual(response.status_code, 403)

    def test_comment_detail_requires_auth(self):
        response = self.client.get(reverse("api_comment", args=(1,)))
        self.assertEqual(response.status_code, 403)

    def test_comment_approve_requires_auth(self):
        response = self.client.post(reverse("api_comment_approve", args=(1,)))
        self.assertEqual(response.status_code, 403)


class APICommentsListTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Hello world",
            slug="hello-world",
            body="content",
        )
        self.comment_approved = models.Comment.objects.create(
            post=self.post,
            body="Approved comment",
            name="Eve",
            email="eve@example.com",
            is_approved=True,
        )
        self.comment_pending = models.Comment.objects.create(
            post=self.post,
            body="Pending comment",
            name="Mallory",
            email="mallory@example.com",
            is_approved=False,
        )
        other_user = models.User.objects.create(username="bob")
        other_post = models.Post.objects.create(
            owner=other_user,
            title="Other post",
            slug="other-post",
            body="content",
        )
        models.Comment.objects.create(
            post=other_post,
            body="Foreign comment",
            is_approved=False,
        )

    def test_comments_list(self):
        response = self.client.get(
            reverse("api_comments"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        comment_list = response.json()["comment_list"]
        self.assertEqual(len(comment_list), 2)
        ids = {c["id"] for c in comment_list}
        self.assertEqual(ids, {self.comment_approved.id, self.comment_pending.id})
        for comment in comment_list:
            self.assertIn("post_slug", comment)
            self.assertIn("url", comment)

    def test_comments_pending_list(self):
        response = self.client.get(
            reverse("api_comments_pending"),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        comment_list = response.json()["comment_list"]
        self.assertEqual(len(comment_list), 1)
        self.assertEqual(comment_list[0]["id"], self.comment_pending.id)
        self.assertFalse(comment_list[0]["is_approved"])


class APIPostCommentsTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Hello world",
            slug="hello-world",
            body="content",
        )
        self.comment_approved = models.Comment.objects.create(
            post=self.post,
            body="Approved comment",
            is_approved=True,
        )
        self.comment_pending = models.Comment.objects.create(
            post=self.post,
            body="Pending comment",
            is_approved=False,
        )
        other_user = models.User.objects.create(username="bob")
        self.other_post = models.Post.objects.create(
            owner=other_user,
            title="Hello world",
            slug="bob-post",
            body="content",
        )
        models.Comment.objects.create(
            post=self.other_post,
            body="Foreign comment",
            is_approved=False,
        )

    def test_post_comments_list(self):
        response = self.client.get(
            reverse("api_post_comments", args=(self.post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        comment_list = response.json()["comment_list"]
        ids = {comment["id"] for comment in comment_list}
        self.assertEqual(ids, {self.comment_approved.id, self.comment_pending.id})

    def test_post_comments_not_found(self):
        response = self.client.get(
            reverse("api_post_comments", args=("missing",)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 404)

    def test_post_comments_foreign_slug(self):
        response = self.client.get(
            reverse("api_post_comments", args=(self.other_post.slug,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 404)


class APICommentDetailMutationsTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Hello world",
            slug="hello-world",
            body="content",
        )
        self.comment = models.Comment.objects.create(
            post=self.post,
            body="Pending comment",
            is_approved=False,
        )
        other_user = models.User.objects.create(username="bob")
        other_post = models.Post.objects.create(
            owner=other_user,
            title="Other post",
            slug="other-post",
            body="content",
        )
        self.foreign_comment = models.Comment.objects.create(
            post=other_post,
            body="Foreign comment",
            is_approved=False,
        )

    def test_comment_detail(self):
        response = self.client.get(
            reverse("api_comment", args=(self.comment.id,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["comment"]["id"], self.comment.id)

    def test_comment_approve(self):
        response = self.client.post(
            reverse("api_comment_approve", args=(self.comment.id,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_approved)

    def test_comment_approve_foreign(self):
        response = self.client.post(
            reverse("api_comment_approve", args=(self.foreign_comment.id,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 404)

    def test_comment_delete(self):
        response = self.client.delete(
            reverse("api_comment", args=(self.comment.id,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(models.Comment.objects.filter(id=self.comment.id).exists())

    def test_comment_delete_foreign(self):
        response = self.client.delete(
            reverse("api_comment", args=(self.foreign_comment.id,)),
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_key}",
        )
        self.assertEqual(response.status_code, 404)
