from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from main import models


class PostCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)

    def test_post_create(self):
        data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        response = self.client.post(reverse("post_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(models.Post.objects.get(title=data["title"]))

    def test_post_multiline_create(self):
        data = {
            "title": "multiline post",
            "slug": "multiline-post",
            "body": """What I’m really concerned about is reaching
one person. And that person may be myself for all I know.

    — Jorge Luis Borges.""",
        }
        response = self.client.post(reverse("post_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(models.Post.objects.get(title=data["title"]))
        self.assertEqual(
            models.Post.objects.get(title=data["title"]).body, data["body"]
        )


class PostCreateAnonTestCase(TestCase):
    """Test non logged in user cannot create post."""

    def test_post_create_anon(self):
        data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        response = self.client.post(reverse("post_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(reverse("login") in response.url)


class PostCreateDraftTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
            "published_at": "",
        }
        self.client.post(reverse("post_create"), self.data)

    def test_post_create_draft(self):
        """Test draft post gets created."""
        self.assertTrue(models.Post.objects.get(title=self.data["title"]))

    def test_post_draft_index(self):
        """Test draft post appears on blog index as draft."""
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Drafts")


class PostCreateDraftAnonTestCase(TestCase):
    """Test draft post does not appear on blog index for non-logged in users."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data_published = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        models.Post.objects.create(owner=self.user, **self.data_published)
        self.data_nonpublished = {
            "title": "Draft post",
            "slug": "draft-post",
            "body": "Incomplete content sentence.",
            "published_at": None,
        }
        models.Post.objects.create(owner=self.user, **self.data_nonpublished)

    def test_post_draft_index(self):
        response = self.client.get(
            reverse("index"),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data_published["title"])
        self.assertNotContains(response, self.data_nonpublished["title"])
        self.assertNotContains(response, "Drafts")


class PostDetailTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_detail(self):
        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            # needs HTTP_HOST because we need to request it on the subdomain
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data["title"])
        self.assertContains(response, self.data["body"])

    def test_post_detail_redir_a(self):
        response = self.client.get(
            reverse("post_detail_redir_a", args=(self.post.slug,)),
            # needs HTTP_HOST because we need to request it on the subdomain
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("post_detail", args=(self.post.slug,)))

    def test_post_detail_redir_b(self):
        response = self.client.get(
            reverse("post_detail_redir_b", args=(self.post.slug,)),
            # needs HTTP_HOST because we need to request it on the subdomain
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("post_detail", args=(self.post.slug,)))


class PostRawTestCase(TestCase):
    """Test raw markdown endpoint (.md suffix)."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "# Markdown content\n\nWith **bold** text.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_raw_published(self):
        """Test published post returns raw markdown."""
        response = self.client.get(
            reverse("post_raw", args=(self.post.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        published_date = self.post.published_at.strftime("%B %d, %Y")
        expected = f"# {self.data['title']}\n\nPublished on {published_date}\n\n{self.data['body']}"
        self.assertEqual(response.content.decode(), expected)

    def test_post_raw_p_path(self):
        """Test /p/ path also works for raw markdown."""
        response = self.client.get(
            reverse("post_raw_p", args=(self.post.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        published_date = self.post.published_at.strftime("%B %d, %Y")
        expected = f"# {self.data['title']}\n\nPublished on {published_date}\n\n{self.data['body']}"
        self.assertEqual(response.content.decode(), expected)

    def test_post_raw_draft_non_owner(self):
        """Test draft post returns 404 for non-owner."""
        self.post.published_at = None
        self.post.save()
        response = self.client.get(
            reverse("post_raw", args=(self.post.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 404)

    def test_post_raw_draft_owner(self):
        """Test draft post returns markdown for owner."""
        self.post.published_at = None
        self.post.save()
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("post_raw", args=(self.post.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        expected = f"# {self.data['title']}\n\n{self.data['body']}"
        self.assertEqual(response.content.decode(), expected)

    def test_post_raw_nonexistent(self):
        """Test non-existent post returns 404."""
        response = self.client.get(
            reverse("post_raw", args=("nonexistent-slug",)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 404)

    def test_post_raw_no_subdomain(self):
        """Test request without subdomain returns 404."""
        response = self.client.get(
            reverse("post_raw", args=(self.post.slug,)),
            HTTP_HOST=settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 404)


class PostSanitizeHTMLTestCase(TestCase):
    """Test is bleach is sanitizing illegal tags."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence. <script>alert(1)</script>",
        }
        models.Post.objects.create(owner=self.user, **self.data)

    def test_get_sanitized(self):
        post = models.Post.objects.get(slug=self.data["slug"])
        self.assertTrue("&lt;script&gt;" in post.body_as_html)
        self.assertFalse("<script>" in post.body_as_html)


class PostUpdateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_get_update(self):
        response = self.client.get(
            reverse("post_update", args=(self.post.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)

    def test_post_update(self):
        new_data = {
            "title": "Updated post",
            "slug": "updated-new-post",
            "body": "Updated content sentence.",
        }
        self.client.post(
            reverse("post_update", args=(self.post.slug,)),
            data=new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        updated_post = models.Post.objects.get(id=self.post.id)
        self.assertEqual(updated_post.title, new_data["title"])
        self.assertEqual(updated_post.slug, new_data["slug"])
        self.assertEqual(updated_post.body, new_data["body"])


class PostUpdateSameSlugTestCase(TestCase):
    """Test updating post without changing slug works."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "Post A",
            "slug": "post-a",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_update(self):
        new_data = {
            "title": "Post A",
            "slug": "post-a",
            "body": "Updated content, same slug.",
        }
        self.client.post(
            reverse("post_update", args=(self.post.slug,)),
            data=new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        updated_post = models.Post.objects.get(id=self.post.id)
        self.assertEqual(updated_post.title, new_data["title"])
        self.assertEqual(updated_post.slug, "post-a")
        self.assertEqual(updated_post.body, new_data["body"])


class PostUpdateTwoSlugsTestCase(TestCase):
    """Test updating post with slug of another post."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data_a = {
            "title": "Post A",
            "slug": "post-a",
            "body": "Content a sentence.",
        }
        self.post_a = models.Post.objects.create(owner=self.user, **self.data_a)
        self.data_b = {
            "title": "Post B",
            "slug": "post-b",
            "body": "Content b sentence.",
        }
        self.post_b = models.Post.objects.create(owner=self.user, **self.data_b)

    def test_post_update(self):
        """Update post a and set its slug to post-b."""
        new_data = {
            "title": "Post A",
            "slug": "post-b",
            "body": "Content a sentence.",
        }
        self.client.post(
            reverse("post_update", args=(self.post_a.slug,)),
            data=new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        updated_post = models.Post.objects.get(id=self.post_a.id)
        self.assertEqual(updated_post.title, new_data["title"])
        # updated_post slug is post-b-<random-chars>
        self.assertEqual(updated_post.slug[:6], "post-b")
        self.assertEqual(updated_post.body, new_data["body"])


class PostUpdateNotOwnTestCase(TestCase):
    """Test user cannot update other user's post."""

    def setUp(self):
        self.victim = models.User.objects.create(username="bob")
        self.original_data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.victim, **self.original_data)

        self.attacker = models.User.objects.create(username="alice")
        self.client.force_login(self.attacker)

    def test_post_update_not_own(self):
        new_data = {
            "title": "Bob sucks",
            "slug": "sorry-bob",
            "body": "No more content.",
        }
        self.client.post(reverse("post_update", args=(self.post.slug,)), new_data)
        updated_post = models.Post.objects.get(id=self.post.id)
        self.assertEqual(updated_post.title, self.original_data["title"])
        self.assertEqual(updated_post.slug, self.original_data["slug"])
        self.assertEqual(updated_post.body, self.original_data["body"])


class PostUpdateAnonTestCase(TestCase):
    """Test non logged in user cannot update post."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_update_anon(self):
        new_data = {
            "title": "Updated post",
            "slug": "updated-new-post",
            "body": "Updated content sentence.",
        }
        self.client.post(reverse("post_update", args=(self.post.slug,)), new_data)
        post_now = models.Post.objects.get(id=self.post.id)
        self.assertEqual(post_now.title, self.data["title"])
        self.assertEqual(post_now.slug, self.data["slug"])
        self.assertEqual(post_now.body, self.data["body"])

    def test_post_update_anon_subdomain(self):
        new_data = {
            "title": "Updated post",
            "slug": "updated-new-post",
            "body": "Updated content sentence.",
        }
        self.client.post(
            reverse("post_update", args=(self.post.slug,)),
            new_data,
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        post_now = models.Post.objects.get(id=self.post.id)
        self.assertEqual(post_now.title, self.data["title"])
        self.assertEqual(post_now.slug, self.data["slug"])
        self.assertEqual(post_now.body, self.data["body"])


class PostDeleteTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_delete(self):
        self.client.post(
            reverse("post_delete", args=(self.post.slug,)),
            HTTP_HOST=self.user.username + "." + settings.CANONICAL_HOST,
        )
        self.assertFalse(
            models.Post.objects.filter(slug=self.data["slug"], owner=self.user).exists()
        )


class PostDeleteNoSubdomainTestCase(TestCase):
    """Test user cannot delete post without being in their subdomain."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_delete_no_subdomain(self):
        self.client.post(reverse("post_delete", args=(self.post.slug,)))
        self.assertTrue(
            models.Post.objects.filter(slug=self.data["slug"], owner=self.user).exists()
        )


class PostDeleteAnonTestCase(TestCase):
    """Test non logged in user cannot delete post."""

    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)

    def test_post_delete_anon(self):
        self.client.post(reverse("post_delete", args=(self.post.slug,)))
        self.assertTrue(
            models.Post.objects.filter(slug=self.data["slug"], owner=self.user).exists()
        )


class PostDeleteNotOwnTestCase(TestCase):
    """Test user cannot delete other's post."""

    def setUp(self):
        self.victim = models.User.objects.create(username="bob")
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.victim, **self.data)

        self.attacker = models.User.objects.create(username="alice")
        self.client.force_login(self.attacker)

    def test_post_delete_not_own(self):
        self.client.post(
            reverse("post_delete", args=(self.post.slug,)),
            HTTP_HOST=self.victim.username + "." + settings.CANONICAL_HOST,
        )
        self.assertTrue(
            models.Post.objects.filter(
                slug=self.data["slug"], owner=self.victim
            ).exists()
        )

    def test_post_delete_not_own_no_subdomain(self):
        self.client.post(reverse("post_delete", args=(self.post.slug,)))
        self.assertTrue(
            models.Post.objects.filter(
                slug=self.data["slug"], owner=self.victim
            ).exists()
        )


class PostPathTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice")
        self.client.force_login(self.user)
        self.data = {
            "title": "New post",
            "slug": "new-post",
            "body": "Content sentence.",
        }
        self.post = models.Post.objects.create(owner=self.user, **self.data)
        self.domain = self.user.username + "." + settings.CANONICAL_HOST

    def test_default_path(self):
        """Test default path is /blog/."""
        self.assertFalse(self.user.post_altpath_on)
        self.assertEqual(
            self.post.get_absolute_url(), f"//{self.domain}/blog/{self.post.slug}/"
        )

        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            HTTP_HOST=self.domain,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("post_detail_p", args=(self.post.slug,)),
            HTTP_HOST=self.domain,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, f"//{self.domain}/blog/{self.post.slug}/")

    def test_alt_path(self):
        """Test alt path is /p/."""
        self.user.post_altpath_on = True
        self.user.save()
        self.assertTrue(self.user.post_altpath_on)
        self.assertEqual(
            self.post.get_absolute_url(), f"//{self.domain}/p/{self.post.slug}/"
        )

        response = self.client.get(
            reverse("post_detail_p", args=(self.post.slug,)),
            HTTP_HOST=self.domain,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            HTTP_HOST=self.domain,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, f"//{self.domain}/p/{self.post.slug}/")
