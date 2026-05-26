import time

from django.conf import settings
from django.core import signing
from django.test import TestCase
from django.urls import reverse

from main import models


def _signed_ts(seconds_ago=10):
    """Signed timestamp matching what PostDetail renders into the comment form."""
    return signing.dumps(int(time.time()) - seconds_ago)


class CommentCreateAuthorTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )
        self.client.force_login(self.user)

    def test_comment_create_author(self):
        data = {
            "body": "Content sentence.",
        }
        response = self.client.post(
            reverse("comment_create_author", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 302)

        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().name, self.user.username)
        self.assertIsNone(models.Comment.objects.all().first().email)
        self.assertEqual(models.Comment.objects.all().first().body, data["body"])
        self.assertEqual(models.Comment.objects.all().first().post, self.post)

        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "your comment is public")


class CommentCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def test_comment_create(self):
        data = {
            "name": "Jon",
            "email": "jon@wick.com",
            "body": "Content sentence.",
            "ts": _signed_ts(),
        }
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 302)

        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().name, data["name"])
        self.assertEqual(models.Comment.objects.all().first().email, data["email"])
        self.assertEqual(models.Comment.objects.all().first().body, data["body"])
        self.assertEqual(models.Comment.objects.all().first().post, self.post)

        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "your comment will be published soon")


class CommentApprovedCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )
        self.comment = models.Comment.objects.create(
            post=self.post,
            name="Jon",
            email="jon@wick.com",
            body="Content sentence.",
            is_approved=True,
        )

    def test_comment_create(self):
        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jon")
        self.assertContains(response, "Content sentence.")


class CommentNotApprovedCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )
        self.comment = models.Comment.objects.create(
            post=self.post,
            name="Jon",
            email="jon@wick.com",
            body="Content sentence.",
            is_approved=False,
        )

    def test_comment_create(self):
        response = self.client.get(
            reverse("post_detail", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Jon")
        self.assertNotContains(response, "Content sentence.")


class CommentNameCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def test_comment_create(self):
        data = {
            "name": "Jon",
            "body": "Content sentence.",
            "ts": _signed_ts(),
        }
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().name, data["name"])
        self.assertEqual(models.Comment.objects.all().first().body, data["body"])
        self.assertEqual(models.Comment.objects.all().first().post, self.post)


class CommentEmailCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def test_comment_create(self):
        data = {
            "email": "jon@wick.com",
            "body": "Content sentence.",
            "ts": _signed_ts(),
        }
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().name, "Anonymous")
        self.assertEqual(models.Comment.objects.all().first().email, data["email"])
        self.assertEqual(models.Comment.objects.all().first().body, data["body"])
        self.assertEqual(models.Comment.objects.all().first().post, self.post)


class CommentNoAuthCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def test_comment_create(self):
        data = {
            "body": "Content sentence.",
            "ts": _signed_ts(),
        }
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().name, "Anonymous")
        self.assertEqual(models.Comment.objects.all().first().body, data["body"])
        self.assertEqual(models.Comment.objects.all().first().post, self.post)


class CommentNoBodyCreateTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def test_comment_create(self):
        data = {
            "body": "",
        }
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Comment.objects.all().count(), 0)


class CommentSpamFilterTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def _post(self, **extra):
        data = {"body": "Content sentence.", "ts": _signed_ts(), **extra}
        return self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )

    def test_honeypot_filled_is_dropped(self):
        response = self._post(url="http://spam.example/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.count(), 0)

    def test_missing_ts_is_dropped(self):
        response = self._post(ts="")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.count(), 0)

    def test_invalid_ts_is_dropped(self):
        response = self._post(ts="not-a-valid-signed-value")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.count(), 0)

    def test_ts_too_recent_is_dropped(self):
        response = self._post(ts=_signed_ts(seconds_ago=0))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.count(), 0)

    def test_valid_ts_and_empty_honeypot_is_saved(self):
        response = self._post(url="")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.count(), 1)

    def test_form_rerender_after_validation_error_keeps_ts(self):
        # Empty body -> form re-renders. The re-rendered page must include
        # a fresh signed ts so the user's next submission isn't dropped.
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data={"body": "", "ts": _signed_ts()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="ts" value=""')


class CommentDisallowedCreateTestCase(TestCase):
    def setUp(self):
        # user.comments_on=False is the default
        self.user = models.User.objects.create(username="alice")
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )

    def test_comment_create(self):
        data = {
            "body": "",
        }
        response = self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Comment.objects.all().count(), 0)


class CommentDeleteTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.client.force_login(self.user)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )
        self.comment = models.Comment.objects.create(
            name="Jon",
            email="jon@wick.com",
            body="Content sentence.",
            post=self.post,
        )

    def test_comment_delete(self):
        response = self.client.post(
            reverse("comment_delete", args=(self.post.slug, self.comment.id)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.all().count(), 0)


class CommentNonOwnerDeleteTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )
        self.comment = models.Comment.objects.create(
            name="Jon",
            email="jon@wick.com",
            body="Content sentence.",
            post=self.post,
        )
        self.non_owner = models.User.objects.create(username="bob")
        self.client.force_login(self.non_owner)

    def test_comment_delete(self):
        response = self.client.post(
            reverse("comment_delete", args=(self.post.slug, self.comment.id)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Comment.objects.all().count(), 1)


class CommentNoAuthDeleteTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            title="Hello world",
            slug="hello-world",
            owner=self.user,
        )
        self.comment = models.Comment.objects.create(
            name="Jon",
            email="jon@wick.com",
            body="Content sentence.",
            post=self.post,
        )

    def test_comment_delete(self):
        response = self.client.post(
            reverse("comment_delete", args=(self.post.slug, self.comment.id)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Comment.objects.all().count(), 1)


class CommentsApproveTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Welcome post",
            slug="welcome-post",
            body="Content sentence.",
            published_at="2020-02-06",
        )
        data = {
            "body": "Hey, I am a comment.",
            "ts": _signed_ts(),
        }
        self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(models.Comment.objects.all().count(), 1)

    def test_comment_approve(self):
        self.comment = models.Comment.objects.all().first()
        self.client.force_login(self.user)
        data = {
            "is_approved": True,
        }
        response = self.client.post(
            reverse(
                "comment_approve",
                args=(
                    self.comment.post.slug,
                    self.comment.id,
                ),
            ),
            HTTP_HOST=f"{self.user.username}.{settings.CANONICAL_HOST}",
            data=data,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().is_approved, True)


class CommentsApproveNoAuthTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Welcome post",
            slug="welcome-post",
            body="Content sentence.",
            published_at="2020-02-06",
        )
        data = {
            "body": "Hey, I am a comment.",
            "ts": _signed_ts(),
        }
        self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(models.Comment.objects.all().count(), 1)

    def test_comment_approve_not_authorized(self):
        self.comment = models.Comment.objects.all().first()
        data = {
            "is_approved": True,
        }
        response = self.client.post(
            reverse(
                "comment_approve",
                args=(
                    self.comment.post.slug,
                    self.comment.id,
                ),
            ),
            HTTP_HOST=f"{self.user.username}.{settings.CANONICAL_HOST}",
            data=data,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().is_approved, False)


class CommentsApproveNonOwnerTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Welcome post",
            slug="welcome-post",
            body="Content sentence.",
            published_at="2020-02-06",
        )
        data = {
            "body": "Hey, I am a comment.",
            "ts": _signed_ts(),
        }
        self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(models.Comment.objects.all().count(), 1)

    def test_comment_approve_not_owner(self):
        self.comment = models.Comment.objects.all().first()
        self.second_user = models.User.objects.create(username="bob", comments_on=True)
        self.client.force_login(self.second_user)
        data = {
            "is_approved": True,
        }
        response = self.client.post(
            reverse(
                "comment_approve",
                args=(
                    self.comment.post.slug,
                    self.comment.id,
                ),
            ),
            HTTP_HOST=f"{self.user.username}.{settings.CANONICAL_HOST}",
            data=data,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Comment.objects.all().count(), 1)
        self.assertEqual(models.Comment.objects.all().first().is_approved, False)


class CommentsDeleteTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Welcome post",
            slug="welcome-post",
            body="Content sentence.",
            published_at="2020-02-06",
        )
        data = {
            "body": "Hey, I am a comment.",
            "ts": _signed_ts(),
        }
        self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(models.Comment.objects.all().count(), 1)

    def test_comment_delete(self):
        self.comment = models.Comment.objects.all().first()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "comment_delete",
                args=(
                    self.comment.post.slug,
                    self.comment.id,
                ),
            ),
            HTTP_HOST=f"{self.user.username}.{settings.CANONICAL_HOST}",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.Comment.objects.all().count(), 0)


class CommentsDeleteNoAuthTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Welcome post",
            slug="welcome-post",
            body="Content sentence.",
            published_at="2020-02-06",
        )
        data = {
            "body": "Hey, I am a comment.",
            "ts": _signed_ts(),
        }
        self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(models.Comment.objects.all().count(), 1)

    def test_comment_delete_not_authorized(self):
        self.comment = models.Comment.objects.all().first()
        response = self.client.post(
            reverse(
                "comment_delete",
                args=(
                    self.comment.post.slug,
                    self.comment.id,
                ),
            ),
            HTTP_HOST=f"{self.user.username}.{settings.CANONICAL_HOST}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Comment.objects.all().count(), 1)


class CommentsDeleteNonOwnerTestCase(TestCase):
    def setUp(self):
        self.user = models.User.objects.create(username="alice", comments_on=True)
        self.post = models.Post.objects.create(
            owner=self.user,
            title="Welcome post",
            slug="welcome-post",
            body="Content sentence.",
            published_at="2020-02-06",
        )
        data = {
            "body": "Hey, I am a comment.",
            "ts": _signed_ts(),
        }
        self.client.post(
            reverse("comment_create", args=(self.post.slug,)),
            HTTP_HOST="alice." + settings.CANONICAL_HOST,
            data=data,
        )
        self.assertEqual(models.Comment.objects.all().count(), 1)

    def test_comment_delete_not_owner(self):
        self.comment = models.Comment.objects.all().first()
        self.second_user = models.User.objects.create(username="bob", comments_on=True)
        self.client.force_login(self.second_user)
        response = self.client.post(
            reverse(
                "comment_delete",
                args=(
                    self.comment.post.slug,
                    self.comment.id,
                ),
            ),
            HTTP_HOST=f"{self.user.username}.{settings.CANONICAL_HOST}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Comment.objects.all().count(), 1)
