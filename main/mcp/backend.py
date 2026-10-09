"""Owner-scoped ORM backend for the official, multi-user Mataroa service.

Construct this backend with the authenticated OAuth subject's user primary key,
never a tool argument, slug owner, or process-wide API key. ORM work runs synchronously on Django's request thread. On a database with row-lock support
(production PostgreSQL), checking the reviewed fingerprint and writing the draft
happen under the same row lock and transaction. SQLite is for development only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Any, TypedDict

from django.db import IntegrityError, transaction

from main import forms, models, scheme, text_processing

_SLUG = re.compile(r"[A-Za-z0-9_-]{1,300}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class Post(TypedDict):
    slug: str
    title: str
    body: str | None
    published_at: str | None
    url: str
    content_sha256: str


class Page(TypedDict):
    slug: str
    title: str
    body: str | None
    is_hidden: bool
    url: str


class MutationReceipt(TypedDict):
    ok: bool
    slug: str
    url: str


class MataroaError(Exception):
    """A sanitized error safe to expose in MCP tool results."""

    def __init__(
        self, code: str, message: str, *, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def post_fingerprint(post: Mapping[str, Any]) -> str:
    """SHA-256 of canonical JSON binding slug, title, body, and publication date.

    The URL, response envelope, and any existing fingerprint are not included.
    Keep the fingerprint from the exact draft the user reviewed for publication.
    """
    try:
        values = {key: post[key] for key in ("slug", "title", "body", "published_at")}
    except (KeyError, TypeError):
        raise MataroaError(
            "invalid_response", "Mataroa returned an invalid response."
        ) from None
    if (
        not all(isinstance(values[key], str) for key in ("slug", "title"))
        or (values["body"] is not None and not isinstance(values["body"], str))
        or (
            values["published_at"] is not None
            and not isinstance(values["published_at"], str)
        )
    ):
        raise MataroaError("invalid_response", "Mataroa returned an invalid response.")
    encoded = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    try:
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise MataroaError(
            "invalid_response", "Mataroa returned an invalid response."
        ) from None


def _invalid(message: str) -> MataroaError:
    return MataroaError("invalid_argument", message)


def _not_found() -> MataroaError:
    # Never disclose whether another tenant owns the requested resource.
    return MataroaError("not_found", "Mataroa resource was not found.", status_code=404)


def _validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise _invalid(
            "Slug must be 1–300 ASCII letters, digits, underscores, or hyphens."
        )


def _validate_fingerprint(value: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise _invalid(
            "The expected content fingerprint must be a lowercase SHA-256 hex digest."
        )


def _validate_content(*, title: str | None, body: str | None) -> None:
    if title is not None and (
        not isinstance(title, str) or not title.strip() or len(title) > 300
    ):
        raise _invalid("Title must be a nonempty string of at most 300 characters.")
    if body is not None and not isinstance(body, str):
        raise _invalid("Body must be a string.")


def _post_form(data: dict[str, Any]) -> forms.APIPost:
    form = forms.APIPost(data)
    if not form.is_valid():
        raise _invalid("The post fields are invalid.")
    return form


def _sanitize_title(title: str) -> str:
    title = text_processing.sanitize_text(title)
    if not title.strip():
        raise _invalid("Title must contain visible text after sanitization.")
    return title


def _post(post: models.Post) -> Post:
    result: Post = {
        "slug": post.slug,
        "title": post.title,
        "body": post.body,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "url": scheme.get_protocol() + post.get_absolute_url(),
        "content_sha256": "",
    }
    result["content_sha256"] = post_fingerprint(result)
    return result


def _page(page: models.Page) -> Page:
    return {
        "slug": page.slug,
        "title": page.title,
        "body": page.body,
        "is_hidden": page.is_hidden,
        "url": scheme.get_protocol() + page.get_absolute_url(),
    }


def _receipt(post: models.Post) -> MutationReceipt:
    return {
        "ok": True,
        "slug": post.slug,
        "url": scheme.get_protocol() + post.get_absolute_url(),
    }


def _comment(comment: models.Comment, *, include_email: bool) -> dict[str, Any]:
    result = {
        "id": comment.pk,
        "post_slug": comment.post.slug,
        "post_title": comment.post.title,
        "post_url": scheme.get_protocol() + comment.post.get_absolute_url(),
        "url": scheme.get_protocol() + comment.get_absolute_url(),
        "created_at": comment.created_at.isoformat(),
        "name": comment.name,
        "body": comment.body,
        "is_approved": comment.is_approved,
        "is_author": comment.is_author,
    }
    if include_email:
        result["email"] = comment.email
    return result


class DjangoBlogBackend:
    """One request's authenticated blog owner; no mutable global user context."""

    def __init__(self, user_id: int) -> None:
        if type(user_id) is not int or user_id < 1:
            raise _invalid("An authenticated user primary key is required.")
        self._user_id = user_id

    def _posts(self):
        return models.Post.objects.filter(owner_id=self._user_id).select_related(
            "owner"
        )

    def _pages(self):
        return models.Page.objects.filter(owner_id=self._user_id).select_related(
            "owner"
        )

    def _comments(self, *, include_email: bool):
        comments = models.Comment.objects.filter(
            post__owner_id=self._user_id
        ).select_related("post__owner")
        # Do not even retrieve commenters' email addresses unless explicitly requested.
        return comments if include_email else comments.defer("email")

    def _locked_draft(self, slug: str, fingerprint: str) -> models.Post:
        try:
            post = self._posts().select_for_update(of=("self",)).get(slug=slug)
        except models.Post.DoesNotExist:
            raise _not_found() from None
        if post.published_at is not None:
            raise MataroaError(
                "already_published",
                "This post is published or scheduled. Draft-only writes were refused.",
            )
        if not hmac.compare_digest(_post(post)["content_sha256"], fingerprint):
            raise MataroaError(
                "content_changed",
                "The draft changed. Read it again and review the current content before writing.",
            )
        return post

    def list_posts(self) -> list[Post]:
        return [_post(post) for post in self._posts()]

    def get_post(self, slug: str) -> Post:
        _validate_slug(slug)
        try:
            return _post(self._posts().get(slug=slug))
        except models.Post.DoesNotExist:
            raise _not_found() from None

    def create_draft(self, title: str, body: str = "") -> MutationReceipt:
        if not isinstance(title, str) or not isinstance(body, str):
            raise _invalid("A draft requires string title and body fields.")
        _validate_content(title=title, body=body)
        _post_form({"title": title, "body": body})
        # Match API creation: validate via APIPost, then sanitize original text.
        title = _sanitize_title(title)
        body = text_processing.sanitize_text(body)
        try:
            with transaction.atomic():
                # Serialize our slug generation for this owner as well as the insert.
                try:
                    owner = models.User.objects.select_for_update().get(
                        pk=self._user_id, is_active=True
                    )
                except models.User.DoesNotExist:
                    raise MataroaError(
                        "unauthorized", "Mataroa authentication failed."
                    ) from None
                slug = text_processing.create_post_slug(title, owner)
                if len(slug) > 300:
                    # Upstream suffixes duplicates; reserve room for its 9-char suffix.
                    slug = text_processing.create_post_slug(slug[:291], owner)
                post = models.Post.objects.create(
                    owner=owner, slug=slug, title=title, body=body, published_at=None
                )
                return _receipt(post)
        except IntegrityError:
            # An outside writer may race slug generation. Never replay a mutation.
            raise MataroaError(
                "write_conflict",
                "The draft could not be saved. Read current posts before retrying.",
            ) from None

    def update_draft(
        self,
        slug: str,
        *,
        expected_content_sha256: str,
        title: str | None = None,
        body: str | None = None,
    ) -> MutationReceipt:
        _validate_slug(slug)
        _validate_fingerprint(expected_content_sha256)
        _validate_content(title=title, body=body)
        data = {
            key: value
            for key, value in {"title": title, "body": body}.items()
            if value is not None
        }
        if not data:
            raise _invalid("Provide at least one draft field to update.")
        form = _post_form(data)
        # Match PATCH: sanitize the values cleaned by the API form.
        cleaned = {
            key: _sanitize_title(form.cleaned_data[key])
            if key == "title"
            else text_processing.sanitize_text(form.cleaned_data[key])
            for key in data
        }
        with transaction.atomic():
            post = self._locked_draft(slug, expected_content_sha256)
            for key, value in cleaned.items():
                setattr(post, key, value)
            post.save(update_fields=[*cleaned, "updated_at"])
            return _receipt(post)

    def publish_post(
        self, slug: str, *, published_at: str, expected_content_sha256: str
    ) -> MutationReceipt:
        _validate_slug(slug)
        _validate_fingerprint(expected_content_sha256)
        if not isinstance(published_at, str) or not _ISO_DATE.fullmatch(published_at):
            raise _invalid(
                "Publication date must be an explicit YYYY-MM-DD calendar date."
            )
        try:
            date.fromisoformat(published_at)
        except ValueError:
            raise _invalid(
                "Publication date must be a valid YYYY-MM-DD calendar date."
            ) from None
        form = _post_form({"published_at": published_at})
        with transaction.atomic():
            post = self._locked_draft(slug, expected_content_sha256)
            post.published_at = form.cleaned_data["published_at"]
            post.save(update_fields=["published_at", "updated_at"])
            return _receipt(post)

    def list_pages(self) -> list[Page]:
        return [_page(page) for page in self._pages()]

    def get_page(self, slug: str) -> Page:
        _validate_slug(slug)
        try:
            return _page(self._pages().get(slug=slug))
        except models.Page.DoesNotExist:
            raise _not_found() from None

    def list_comments(
        self,
        *,
        post_slug: str | None = None,
        pending_only: bool = False,
        include_email: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(pending_only, bool) or not isinstance(include_email, bool):
            raise _invalid("Comment filters must be booleans.")
        comments = self._comments(include_email=include_email)
        if post_slug is not None:
            _validate_slug(post_slug)
            if not self._posts().filter(slug=post_slug).exists():
                raise _not_found()
            comments = comments.filter(post__slug=post_slug)
        if pending_only:
            comments = comments.filter(is_approved=False)
        return [_comment(comment, include_email=include_email) for comment in comments]

    def get_comment(
        self, comment_id: int, *, include_email: bool = False
    ) -> dict[str, Any]:
        if type(comment_id) is not int or comment_id < 1:
            raise _invalid("Comment ID must be a positive integer.")
        if not isinstance(include_email, bool):
            raise _invalid("include_email must be a boolean.")
        try:
            comment = self._comments(include_email=include_email).get(pk=comment_id)
        except models.Comment.DoesNotExist:
            raise _not_found() from None
        return _comment(comment, include_email=include_email)
