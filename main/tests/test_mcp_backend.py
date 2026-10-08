"""Integration tests against Mataroa's real ORM models and API forms."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from main import models
from main.mcp.backend import DjangoBlogBackend, MataroaError, post_fingerprint


class DjangoBlogBackendTests(TransactionTestCase):
    def setUp(self):
        self.owner = models.User.objects.create_user(username="alice")
        self.other = models.User.objects.create_user(username="bob")
        self.backend = DjangoBlogBackend(self.owner.pk)
        self.draft = models.Post.objects.create(
            owner=self.owner,
            slug="same-slug",
            title="Alice draft",
            body="Reviewed",
            published_at=None,
        )
        self.other_draft = models.Post.objects.create(
            owner=self.other,
            slug="same-slug",
            title="Bob private draft",
            body="Do not reveal",
            published_at=None,
        )

    def call(self, method, *args, **kwargs):
        return async_to_sync(getattr(self.backend, method))(*args, **kwargs)

    def fingerprint(self):
        return self.call("get_post", self.draft.slug)["content_sha256"]

    def assert_error(self, code, method, *args, **kwargs):
        with self.assertRaises(MataroaError) as caught:
            self.call(method, *args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_requires_authenticated_integer_subject(self):
        for subject in (True, False, 0, -1, "1", None, 1.5):
            with self.subTest(subject=subject), self.assertRaises(MataroaError):
                DjangoBlogBackend(subject)

    def test_list_and_get_posts_are_owner_scoped_with_same_slug(self):
        posts = self.call("list_posts")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "Alice draft")
        self.assertEqual(self.call("get_post", "same-slug"), posts[0])
        self.assertEqual(posts[0]["content_sha256"], post_fingerprint(posts[0]))
        self.assertIn("//alice.", posts[0]["url"])

    def test_fingerprint_binds_every_reviewed_field(self):
        initial = self.fingerprint()
        for key, value in (
            ("title", "Changed"),
            ("body", "Changed"),
            ("slug", "changed-slug"),
            ("published_at", date(2026, 10, 8)),
        ):
            with self.subTest(key=key):
                old = getattr(self.draft, key)
                setattr(self.draft, key, value)
                self.draft.save()
                changed = self.call("get_post", self.draft.slug)["content_sha256"]
                self.assertNotEqual(changed, initial)
                setattr(self.draft, key, old)
                self.draft.save()

    def test_foreign_only_posts_are_not_found_for_read_and_writes(self):
        self.other_draft.slug = "bob-only"
        self.other_draft.save()
        foreign_hash = async_to_sync(DjangoBlogBackend(self.other.pk).get_post)(
            "bob-only"
        )["content_sha256"]
        for method, kwargs in (
            ("get_post", {}),
            (
                "update_draft",
                {"body": "attack", "expected_content_sha256": foreign_hash},
            ),
            (
                "publish_post",
                {"published_at": "2026-10-08", "expected_content_sha256": foreign_hash},
            ),
        ):
            with self.subTest(method=method):
                error = self.assert_error("not_found", method, "bob-only", **kwargs)
                self.assertEqual(error.status_code, 404)
                self.assertNotIn("Bob", str(error))
        self.other_draft.refresh_from_db()
        self.assertEqual(self.other_draft.body, "Do not reveal")
        self.assertIsNone(self.other_draft.published_at)

    def test_other_tenant_fingerprint_cannot_overwrite_same_slug(self):
        foreign_hash = async_to_sync(DjangoBlogBackend(self.other.pk).get_post)(
            "same-slug"
        )["content_sha256"]
        self.assert_error(
            "content_changed",
            "update_draft",
            "same-slug",
            expected_content_sha256=foreign_hash,
            body="attack",
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.body, "Reviewed")

    def test_update_only_mutates_authenticated_owner_and_preserves_slug(self):
        receipt = self.call(
            "update_draft",
            "same-slug",
            expected_content_sha256=self.fingerprint(),
            title="New title",
        )
        self.assertEqual(set(receipt), {"ok", "slug", "url"})
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["slug"], "same-slug")
        self.draft.refresh_from_db()
        self.other_draft.refresh_from_db()
        self.assertEqual(self.draft.title, "New title")
        self.assertEqual(self.draft.body, "Reviewed")
        self.assertEqual(self.other_draft.title, "Bob private draft")

    def test_create_is_always_draft_and_returns_actual_slug_receipt(self):
        first = self.call("create_draft", "Brand new", "Hello")
        second = self.call("create_draft", "Brand new", "Second")
        self.assertEqual(set(first), {"ok", "slug", "url"})
        self.assertEqual(first["slug"], "brand-new")
        self.assertNotEqual(first["slug"], second["slug"])
        saved = models.Post.objects.get(owner=self.owner, slug=second["slug"])
        self.assertIsNone(saved.published_at)
        self.assertEqual(saved.body, "Second")
        self.assertEqual(self.call("get_post", saved.slug)["url"], second["url"])
        self.assertEqual(models.Post.objects.filter(owner=self.other).count(), 1)

    def test_duplicate_maximum_length_title_has_valid_slug(self):
        for _ in range(2):
            receipt = self.call("create_draft", "x" * 300)
            self.assertLessEqual(len(receipt["slug"]), 300)
            self.assertEqual(self.call("get_post", receipt["slug"])["title"], "x" * 300)

    def test_creation_matches_upstream_sanitization_and_preserves_whitespace(self):
        receipt = self.call(
            "create_draft", "  Hello\x01\ud835world  ", "  a\x01b\udc00\n  "
        )
        post = self.call("get_post", receipt["slug"])
        self.assertEqual(post["title"], "  Hello world  ")
        self.assertEqual(post["body"], "  a b\n  ")

    def test_update_uses_cleaned_api_form_fields_and_sanitizes(self):
        self.call(
            "update_draft",
            "same-slug",
            expected_content_sha256=self.fingerprint(),
            title="  Hello\x01\ud835world  ",
            body="  a\x01b\udc00\n  ",
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, "Hello world")
        self.assertEqual(self.draft.body, "a b")

    def test_empty_and_invalid_creation_fields_are_rejected_without_insert(self):
        for title, body in (
            ("", "body"),
            ("   ", "body"),
            ("x" * 301, "body"),
            (None, "body"),
            (1, "body"),
            ("title", None),
            ("title", []),
            ("title", "null\x00byte"),
            ("\ud835\x01", "body"),
        ):
            with self.subTest(title=repr(title), body=repr(body)):
                self.assert_error("invalid_argument", "create_draft", title, body)
        self.assertEqual(models.Post.objects.count(), 2)

    def test_invalid_update_does_not_change_draft(self):
        for fields in ({}, {"title": ""}, {"title": 3}, {"body": []}, {"body": "\x00"}):
            with self.subTest(fields=fields):
                self.assert_error(
                    "invalid_argument",
                    "update_draft",
                    "same-slug",
                    expected_content_sha256=self.fingerprint(),
                    **fields,
                )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.body, "Reviewed")
        self.assertEqual(self.draft.title, "Alice draft")

    def test_body_can_be_cleared_and_null_legacy_bodies_remain_distinct(self):
        self.call(
            "update_draft",
            "same-slug",
            expected_content_sha256=self.fingerprint(),
            body="",
        )
        self.assertEqual(self.call("get_post", "same-slug")["body"], "")
        empty_fingerprint = self.fingerprint()
        self.draft.body = None
        self.draft.save()
        self.assertIsNone(self.call("get_post", "same-slug")["body"])
        self.assertNotEqual(self.fingerprint(), empty_fingerprint)

    def test_stale_fingerprint_refuses_update_and_publish(self):
        fingerprint = self.fingerprint()
        self.draft.body = "Edited elsewhere"
        self.draft.save()
        self.assert_error(
            "content_changed",
            "update_draft",
            "same-slug",
            expected_content_sha256=fingerprint,
            body="Unreviewed overwrite",
        )
        self.assert_error(
            "content_changed",
            "publish_post",
            "same-slug",
            expected_content_sha256=fingerprint,
            published_at="2026-10-08",
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.body, "Edited elsewhere")
        self.assertIsNone(self.draft.published_at)

    def test_malformed_fingerprints_are_refused(self):
        for fingerprint in (None, True, 1, "", "0" * 63, "A" * 64, "z" * 64):
            with self.subTest(fingerprint=fingerprint):
                self.assert_error(
                    "invalid_argument",
                    "update_draft",
                    "same-slug",
                    expected_content_sha256=fingerprint,
                    body="bad",
                )
                self.assert_error(
                    "invalid_argument",
                    "publish_post",
                    "same-slug",
                    expected_content_sha256=fingerprint,
                    published_at="2026-10-08",
                )

    def test_published_and_scheduled_posts_refuse_draft_mutations(self):
        for published_at in (date(2000, 1, 1), date(2099, 1, 1)):
            self.draft.published_at = published_at
            self.draft.save()
            fingerprint = self.fingerprint()
            for method, fields in (
                ("update_draft", {"title": "Bad"}),
                ("publish_post", {"published_at": "2026-10-08"}),
            ):
                with self.subTest(published_at=published_at, method=method):
                    self.assert_error(
                        "already_published",
                        method,
                        "same-slug",
                        expected_content_sha256=fingerprint,
                        **fields,
                    )
            self.draft.refresh_from_db()
            self.assertEqual(self.draft.published_at, published_at)
            self.assertEqual(self.draft.title, "Alice draft")

    def test_publish_saves_only_reviewed_owner_draft_and_explicit_date(self):
        fingerprint = self.fingerprint()
        receipt = self.call(
            "publish_post",
            "same-slug",
            expected_content_sha256=fingerprint,
            published_at="2026-10-08",
        )
        self.assertEqual(set(receipt), {"ok", "slug", "url"})
        self.draft.refresh_from_db()
        self.other_draft.refresh_from_db()
        self.assertEqual(self.draft.published_at, date(2026, 10, 8))
        self.assertEqual(self.draft.body, "Reviewed")
        self.assertEqual(self.draft.title, "Alice draft")
        self.assertIsNone(self.other_draft.published_at)
        self.assertNotEqual(self.fingerprint(), fingerprint)

    def test_publish_allows_explicit_future_schedule(self):
        self.call(
            "publish_post",
            "same-slug",
            expected_content_sha256=self.fingerprint(),
            published_at="2099-01-01",
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.published_at, date(2099, 1, 1))
        self.assertFalse(self.draft.is_published)

    def test_publish_requires_exact_valid_calendar_date(self):
        for value in (
            None,
            "",
            "2026-1-01",
            "2026-02-30",
            "2026-10-08T12:00:00Z",
            "10/08/2026",
            "tomorrow",
            True,
            date(2026, 10, 8),
        ):
            with self.subTest(value=value):
                self.assert_error(
                    "invalid_argument",
                    "publish_post",
                    "same-slug",
                    expected_content_sha256=self.fingerprint(),
                    published_at=value,
                )
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.published_at)

    def test_all_mutations_select_for_update_inside_atomic_transaction(self):
        calls = []
        original = QuerySet.select_for_update

        def record_lock(queryset, *args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            calls.append((queryset.model, kwargs))
            return original(queryset, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", record_lock):
            self.call("create_draft", "Another draft")
            self.call(
                "update_draft",
                "same-slug",
                expected_content_sha256=self.fingerprint(),
                body="Next",
            )
            self.call(
                "publish_post",
                "same-slug",
                expected_content_sha256=self.fingerprint(),
                published_at="2026-10-08",
            )
        self.assertEqual(
            [model for model, _ in calls], [models.User, models.Post, models.Post]
        )
        self.assertEqual(calls[1][1]["of"], ("self",))
        self.assertEqual(calls[2][1]["of"], ("self",))

    def test_publish_rolls_back_if_transaction_cannot_complete(self):
        fingerprint = self.fingerprint()
        with (
            patch("main.mcp.backend._receipt", side_effect=RuntimeError("failure")),
            self.assertRaises(RuntimeError),
        ):
            self.call(
                "publish_post",
                "same-slug",
                expected_content_sha256=fingerprint,
                published_at="2026-10-08",
            )
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.published_at)
        self.assertEqual(self.fingerprint(), fingerprint)

    def test_create_and_update_roll_back_if_transaction_cannot_complete(self):
        fingerprint = self.fingerprint()
        with patch("main.mcp.backend._receipt", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.call("create_draft", "Rollback draft")
            with self.assertRaises(RuntimeError):
                self.call(
                    "update_draft",
                    "same-slug",
                    expected_content_sha256=fingerprint,
                    body="Rollback",
                )
        self.assertEqual(models.Post.objects.count(), 2)
        self.assertEqual(self.fingerprint(), fingerprint)

    def test_pages_are_read_only_and_owner_scoped_including_hidden_pages(self):
        models.Page.objects.create(
            owner=self.owner, slug="about", title="Alice", is_hidden=True
        )
        models.Page.objects.create(
            owner=self.other, slug="about", title="Bob", body="Private"
        )
        models.Page.objects.create(owner=self.other, slug="bob-only", title="Bob only")
        pages = self.call("list_pages")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["title"], "Alice")
        self.assertTrue(pages[0]["is_hidden"])
        self.assertIsNone(pages[0]["body"])
        self.assertEqual(self.call("get_page", "about"), pages[0])
        self.assert_error("not_found", "get_page", "bob-only")
        self.assertFalse(hasattr(self.backend, "update_page"))
        self.assertFalse(hasattr(self.backend, "delete_page"))

    def create_comments(self):
        own = models.Comment.objects.create(
            post=self.draft, body="Pending", name="Reader", email="reader@example.test"
        )
        approved = models.Comment.objects.create(
            post=self.draft, body="Approved", is_approved=True
        )
        other = models.Comment.objects.create(
            post=self.other_draft, body="Secret", email="secret@example.test"
        )
        return own, approved, other

    def test_comments_are_scoped_through_post_owner_and_hide_email_by_default(self):
        own, approved, other = self.create_comments()
        comments = self.call("list_comments")
        self.assertEqual({comment["id"] for comment in comments}, {own.pk, approved.pk})
        self.assertTrue(all("email" not in comment for comment in comments))
        self.assertEqual(self.call("get_comment", own.pk), comments[0])
        self.assert_error("not_found", "get_comment", other.pk)
        self.assert_error("not_found", "get_comment", other.pk, include_email=True)
        self.assertFalse(hasattr(self.backend, "approve_comment"))
        self.assertFalse(hasattr(self.backend, "delete_comment"))

    def test_default_comments_do_not_query_email_column(self):
        own, _, _ = self.create_comments()
        with CaptureQueriesContext(connection) as queries:
            self.call("list_comments")
            self.call("get_comment", own.pk)
        self.assertEqual(len(queries), 2)
        # User rows also have email, so check the Comment column specifically.
        self.assertTrue(
            all('"main_comment"."email"' not in row["sql"] for row in queries)
        )

    def test_comments_email_is_explicit_opt_in_for_owner_only(self):
        own, _, other = self.create_comments()
        comment = self.call("get_comment", own.pk, include_email=True)
        self.assertEqual(comment["email"], "reader@example.test")
        comments = self.call("list_comments", include_email=True)
        self.assertTrue(all("email" in item for item in comments))
        self.assertNotIn(other.pk, {item["id"] for item in comments})

    def test_combined_post_and_pending_comment_filters_remain_owner_scoped(self):
        own, _, _ = self.create_comments()
        another_post = models.Post.objects.create(
            owner=self.owner, slug="another", title="Other own", published_at=None
        )
        models.Comment.objects.create(post=another_post, body="Other own pending")
        for fields in ({"post_slug": "same-slug", "pending_only": True},):
            comments = self.call("list_comments", **fields)
            self.assertEqual([comment["id"] for comment in comments], [own.pk])
        self.assertEqual(len(self.call("list_comments", pending_only=True)), 2)
        self.assertEqual(len(self.call("list_comments", post_slug="same-slug")), 2)
        self.other_draft.slug = "bob-only"
        self.other_draft.save()
        self.assert_error("not_found", "list_comments", post_slug="bob-only")

    def test_invalid_comments_filters_and_ids_are_refused(self):
        for fields in (
            {"include_email": "yes"},
            {"pending_only": 1},
            {"post_slug": "../x"},
        ):
            with self.subTest(fields=fields):
                self.assert_error("invalid_argument", "list_comments", **fields)
        for comment_id in (True, 0, -1, "1", None):
            with self.subTest(comment_id=comment_id):
                self.assert_error("invalid_argument", "get_comment", comment_id)
        self.assert_error("invalid_argument", "get_comment", 1, include_email="yes")

    def test_invalid_slugs_are_refused_without_unscoped_lookup(self):
        for slug in ("../x", "a/b", "", "é", "x" * 301, None):
            with self.subTest(slug=slug):
                self.assert_error("invalid_argument", "get_post", slug)
                self.assert_error("invalid_argument", "get_page", slug)

    def test_urls_use_upstream_owner_alternative_post_path(self):
        self.owner.post_altpath_on = True
        self.owner.save()
        post = self.call("get_post", "same-slug")
        self.assertIn("/p/same-slug/", post["url"])
        own = models.Comment.objects.create(post=self.draft, body="comment")
        comment = self.call("get_comment", own.pk)
        self.assertEqual(comment["post_url"], post["url"])
        self.assertEqual(comment["url"], post["url"] + f"#comment-{own.pk}")

    def test_inactive_or_deleted_owner_cannot_create_draft(self):
        self.owner.is_active = False
        self.owner.save()
        self.assert_error("unauthorized", "create_draft", "Not allowed")
        self.owner.delete()
        self.assert_error("unauthorized", "create_draft", "Not allowed")

    def test_nullable_comment_name_is_preserved(self):
        comment = models.Comment.objects.create(post=self.draft, body="Text", name=None)
        self.assertIsNone(self.call("get_comment", comment.pk)["name"])

    def test_publish_preserves_upstream_notification_eligibility(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        self.call(
            "publish_post",
            "same-slug",
            expected_content_sha256=self.fingerprint(),
            published_at=yesterday.isoformat(),
        )
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.broadcasted_at)
        # This is the same selection used by processnotifications. Publication
        # does not send mail inline or mark the normal newsletter job complete.
        self.assertTrue(
            models.Post.objects.filter(
                pk=self.draft.pk,
                owner__notifications_on=True,
                broadcasted_at__isnull=True,
                published_at=yesterday,
            ).exists()
        )
        self.assertEqual(models.NotificationRecord.objects.count(), 0)

    def test_create_conflict_rolls_back_without_automatic_retry(self):
        with patch(
            "main.mcp.backend.text_processing.create_post_slug",
            return_value="same-slug",
        ) as create_slug:
            self.assert_error("write_conflict", "create_draft", "Collision")
        create_slug.assert_called_once()
        self.assertEqual(models.Post.objects.count(), 2)
        self.assertEqual(self.call("get_post", "same-slug")["title"], "Alice draft")

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_publications_commit_exactly_once_with_real_row_locks(self):
        # SQLite cannot demonstrate PostgreSQL's row-lock guarantee. Run this
        # test as part of production-database CI in addition to SQLite tests.
        fingerprint = self.fingerprint()
        gate = Barrier(2)
        owner_id = self.owner.pk

        def publish(publication_date):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                receipt = async_to_sync(DjangoBlogBackend(owner_id).publish_post)(
                    "same-slug",
                    expected_content_sha256=fingerprint,
                    published_at=publication_date,
                )
                return publication_date, receipt
            except MataroaError as exc:
                return publication_date, exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(publish, value)
                for value in ("2026-10-08", "2026-10-09")
            ]
            results = [future.result(timeout=15) for future in futures]
        winners = [
            (value, result) for value, result in results if isinstance(result, dict)
        ]
        losers = [result for _, result in results if isinstance(result, str)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(losers, ["already_published"])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.published_at.isoformat(), winners[0][0])


class PostFingerprintTests(SimpleTestCase):
    post = {
        "slug": "hello-world",
        "title": "Hello World",
        "body": "A private draft. Unicode: café 🌳",
        "published_at": None,
    }

    def test_fingerprint_is_canonical_and_ignores_transport_metadata(self):
        reversed_keys = dict(reversed(list(self.post.items())))
        reversed_keys.update(
            url="https://different.example/", content_sha256="ignored", ok=False
        )
        self.assertEqual(post_fingerprint(reversed_keys), post_fingerprint(self.post))
        self.assertEqual(len(post_fingerprint(self.post)), 64)

    def test_fingerprint_rejects_missing_invalid_and_non_unicode_fields(self):
        for post in [
            {},
            {**self.post, "body": 42},
            {**self.post, "published_at": []},
            {**self.post, "body": "\ud800"},
        ]:
            with self.subTest(post=post), self.assertRaises(MataroaError) as error:
                post_fingerprint(post)
            self.assertEqual(error.exception.code, "invalid_response")
