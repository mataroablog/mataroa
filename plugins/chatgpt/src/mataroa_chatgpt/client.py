"""Small, defensive async client for the Mataroa API.

Writes are deliberately limited to drafts and explicit publication. Mataroa has
no ETag or conditional-write API: the fingerprint check is a best-effort
read-before-write guard, NOT an atomic compare-and-swap. Another editor can still
change or publish the post between our GET and PATCH. Do not use concurrent
editing when this distinction matters. Writes are never automatically retried.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any, TypedDict
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import SecretStr

DEFAULT_BASE_URL = "https://mataroa.blog/api/"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
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
    """An intentionally sanitized error, safe to expose in MCP tool results.

    Neither upstream response bodies nor request objects are attached. `code`
    and optional `status_code` are suitable for programmatic error handling.
    """

    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _invalid(message: str) -> MataroaError:
    return MataroaError("invalid_argument", message)


def _invalid_response() -> MataroaError:
    return MataroaError("invalid_response", "Mataroa returned an invalid response.")


def _validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise _invalid("Slug must be 1–300 ASCII letters, digits, underscores, or hyphens.")
    return slug


def _validate_date(value: str) -> str:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise _invalid("Publication date must be an explicit YYYY-MM-DD calendar date.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise _invalid("Publication date must be a valid YYYY-MM-DD calendar date.") from None
    return value


def _validate_base_url(value: str, allow_insecure_localhost: bool) -> str:
    message = (
        "Base URL must be HTTPS without credentials, query, fragment, or traversal. "
        "HTTP requires an explicit loopback-only development opt-in."
    )
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise _invalid(message)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Reading .port also validates numeric/range syntax.
        _ = parsed.port
    except ValueError:
        raise _invalid(message) from None
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or "\\" in value
    ):
        raise _invalid(message)
    decoded_path = unquote(parsed.path)
    if (
        "%" in parsed.path
        or "\\" in decoded_path
        or any(piece in {".", ".."} for piece in decoded_path.split("/"))
    ):
        raise _invalid(message)
    loopback = hostname.lower() == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and allow_insecure_localhost is True and loopback
    ):
        raise _invalid(message)
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise _invalid(message) from None
    return str(url).rstrip("/") + "/"


def post_fingerprint(post: Mapping[str, Any]) -> str:
    """SHA-256 of canonical JSON binding slug, title, body, and publication date.

    The URL, response envelope, and any existing fingerprint are not included.
    Keep the fingerprint from the exact draft the user reviewed for publication.
    """
    try:
        values = {key: post[key] for key in ("slug", "title", "body", "published_at")}
    except (KeyError, TypeError):
        raise _invalid_response() from None
    if (
        not all(isinstance(values[key], str) for key in ("slug", "title"))
        or (values["body"] is not None and not isinstance(values["body"], str))
        or (values["published_at"] is not None and not isinstance(values["published_at"], str))
    ):
        raise _invalid_response()
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise _invalid_response() from None


class MataroaClient:
    """API-key client; base_url is trusted operator configuration, never tool input.

    `http_client` or `transport` may be injected for tests. A supplied client is
    caller-owned and must itself be trusted (including hooks/proxies/transports).
    Redirects and client-level authentication are overridden on every request.
    Owned clients disable environment proxies. Never log HTTP Authorization.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        allow_insecure_localhost: bool = False,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or any(ord(c) < 33 or ord(c) > 126 for c in api_key)
        ):
            raise _invalid(
                "A nonempty API key containing printable ASCII without spaces is required."
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 30
        ):
            raise _invalid("Timeout must be greater than zero and at most 30 seconds.")
        if http_client is not None and transport is not None:
            raise _invalid("Pass either http_client or transport, not both.")
        self.base_url = _validate_base_url(base_url, allow_insecure_localhost)
        self._api_key = SecretStr(api_key)
        self._timeout_seconds = float(timeout)
        self._timeout = httpx.Timeout(float(timeout), connect=min(float(timeout), 5.0))
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            transport=transport,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def __repr__(self) -> str:
        return "MataroaClient(api_key=<redacted>)"

    async def __aenter__(self) -> MataroaClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only the HTTP client owned by this wrapper."""
        if self._owns_client:
            await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Only internal code builds path; dynamic components must pass validators.
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._http.stream(
                    method,
                    self.base_url + path,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Accept": "application/json",
                    },
                    json=payload,
                    follow_redirects=False,
                    timeout=self._timeout,
                    auth=None,
                ) as response:
                    status = response.status_code
                    if status in {401, 403}:
                        raise MataroaError(
                            "unauthorized", "Mataroa authentication failed.", status_code=status
                        )
                    if status == 404:
                        raise MataroaError(
                            "not_found", "Mataroa resource was not found.", status_code=status
                        )
                    if 300 <= status < 400:
                        raise MataroaError(
                            "redirect_refused", "Mataroa redirect was refused.", status_code=status
                        )
                    if not 200 <= status < 300:
                        raise MataroaError(
                            "upstream_error", "Mataroa rejected the request.", status_code=status
                        )
                    parts: list[bytes] = []
                    size = 0
                    async for part in response.aiter_bytes():
                        size += len(part)
                        if size > MAX_RESPONSE_BYTES:
                            raise MataroaError(
                                "response_too_large", "Mataroa response exceeded the size limit."
                            )
                        parts.append(part)
        except (TimeoutError, httpx.TimeoutException):
            raise MataroaError(
                "timeout", "Mataroa request timed out. Check current state before retrying a write."
            ) from None
        except httpx.RequestError:
            raise MataroaError(
                "network_error",
                "Could not reach Mataroa. Check current state before retrying a write.",
            ) from None
        try:
            data = json.loads(b"".join(parts))
        except (ValueError, UnicodeError, RecursionError):
            raise _invalid_response() from None
        if not isinstance(data, dict):
            raise _invalid_response()
        if data.get("ok") is not True:
            raise MataroaError("upstream_error", "Mataroa did not confirm the request succeeded.")
        return data

    @staticmethod
    def _post(raw: Any) -> Post:
        if not isinstance(raw, dict):
            raise _invalid_response()
        keys = ("slug", "title", "url")
        if (
            not all(isinstance(raw.get(key), str) for key in keys)
            or "body" not in raw
            or (raw["body"] is not None and not isinstance(raw["body"], str))
            or "published_at" not in raw
        ):
            raise _invalid_response()
        try:
            _validate_slug(raw["slug"])
            if raw["published_at"] is not None:
                _validate_date(raw["published_at"])
        except MataroaError:
            raise _invalid_response() from None
        result: Post = {
            "slug": raw["slug"],
            "title": raw["title"],
            "body": raw["body"],
            "published_at": raw["published_at"],
            "url": raw["url"],
            "content_sha256": post_fingerprint(raw),
        }
        return result

    @staticmethod
    def _page(raw: Any) -> Page:
        if (
            not isinstance(raw, dict)
            or not all(isinstance(raw.get(key), str) for key in ("slug", "title", "url"))
            or "body" not in raw
            or (raw["body"] is not None and not isinstance(raw["body"], str))
            or not isinstance(raw.get("is_hidden"), bool)
        ):
            raise _invalid_response()
        try:
            _validate_slug(raw["slug"])
        except MataroaError:
            raise _invalid_response() from None
        return {key: raw[key] for key in ("slug", "title", "body", "url", "is_hidden")}  # type: ignore[return-value]

    @staticmethod
    def _comment(raw: Any, include_email: bool) -> dict[str, Any]:
        if not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] < 1:
            raise _invalid_response()
        keys = ("post_slug", "post_title", "post_url", "url", "created_at", "body")
        if (
            not all(isinstance(raw.get(key), str) for key in keys)
            or "name" not in raw
            or (raw["name"] is not None and not isinstance(raw["name"], str))
            or not all(isinstance(raw.get(key), bool) for key in ("is_approved", "is_author"))
        ):
            raise _invalid_response()
        result = {key: raw[key] for key in ("id", *keys, "name", "is_approved", "is_author")}
        if include_email:
            if raw.get("email") is not None and not isinstance(raw["email"], str):
                raise _invalid_response()
            result["email"] = raw.get("email")
        return result

    @staticmethod
    def _receipt(raw: dict[str, Any]) -> MutationReceipt:
        if not isinstance(raw.get("url"), str):
            raise _invalid_response()
        try:
            slug = _validate_slug(raw.get("slug"))
        except MataroaError:
            raise _invalid_response() from None
        return {"ok": True, "slug": slug, "url": raw["url"]}

    async def list_posts(self) -> list[Post]:
        data = await self._request("GET", "posts/")
        if not isinstance(data.get("post_list"), list):
            raise _invalid_response()
        return [self._post(post) for post in data["post_list"]]

    async def get_post(self, slug: str) -> Post:
        slug = _validate_slug(slug)
        post = self._post(await self._request("GET", f"posts/{slug}/"))
        if post["slug"] != slug:
            raise _invalid_response()
        return post

    async def create_draft(self, title: str, body: str = "") -> MutationReceipt:
        """Create an unpublished draft. There is no publication-date argument."""
        if not isinstance(title, str) or not isinstance(body, str):
            raise _invalid("A draft requires string title and body fields.")
        self._validate_content(title=title, body=body)
        return self._receipt(
            await self._request("POST", "posts/", payload={"title": title, "body": body})
        )

    @staticmethod
    def _validate_content(*, title: str | None, body: str | None) -> None:
        if title is not None and (
            not isinstance(title, str) or not title.strip() or len(title) > 300
        ):
            raise _invalid("Title must be a nonempty string of at most 300 characters.")
        if body is not None and not isinstance(body, str):
            raise _invalid("Body must be a string.")
        try:
            for value in (title, body):
                if value is not None:
                    value.encode("utf-8")
        except UnicodeError:
            raise _invalid("Content must contain valid Unicode text.") from None

    async def _check_draft(self, slug: str, expected_content_sha256: str) -> Post:
        if not isinstance(expected_content_sha256, str) or not _FINGERPRINT.fullmatch(
            expected_content_sha256
        ):
            raise _invalid(
                "The expected content fingerprint must be a lowercase SHA-256 hex digest."
            )
        post = await self.get_post(slug)
        if post["published_at"] is not None:
            raise MataroaError(
                "already_published",
                "This post has a publication date. Draft-only writes were refused.",
            )
        if not hmac.compare_digest(post["content_sha256"], expected_content_sha256):
            raise MataroaError(
                "content_changed",
                "The draft changed. Read it again and review the current content before writing.",
            )
        return post

    async def update_draft(
        self,
        slug: str,
        *,
        expected_content_sha256: str,
        title: str | None = None,
        body: str | None = None,
    ) -> MutationReceipt:
        """Update only title/body, after a non-atomic draft/fingerprint guard.

        No slug changes, unpublishing, or editing of published posts is exposed.
        Re-read after success for the actual saved content and new fingerprint.
        """
        slug = _validate_slug(slug)
        self._validate_content(title=title, body=body)
        payload = {
            key: value for key, value in {"title": title, "body": body}.items() if value is not None
        }
        if not payload:
            raise _invalid("Provide at least one draft field to update.")
        await self._check_draft(slug, expected_content_sha256)
        return self._receipt(await self._request("PATCH", f"posts/{slug}/", payload=payload))

    async def publish_post(
        self, slug: str, *, published_at: str, expected_content_sha256: str
    ) -> MutationReceipt:
        """Publish exactly the reviewed draft with an explicitly supplied date.

        The caller must obtain the user's approval of that content and date.
        The read-before-write guard cannot eliminate upstream concurrency races.
        """
        slug = _validate_slug(slug)
        published_at = _validate_date(published_at)
        await self._check_draft(slug, expected_content_sha256)
        return self._receipt(
            await self._request("PATCH", f"posts/{slug}/", payload={"published_at": published_at})
        )

    async def list_pages(self) -> list[Page]:
        data = await self._request("GET", "pages/")
        if not isinstance(data.get("page_list"), list):
            raise _invalid_response()
        return [self._page(page) for page in data["page_list"]]

    async def get_page(self, slug: str) -> Page:
        slug = _validate_slug(slug)
        page = self._page(await self._request("GET", f"pages/{slug}/"))
        if page["slug"] != slug:
            raise _invalid_response()
        return page

    async def list_comments(
        self,
        *,
        post_slug: str | None = None,
        pending_only: bool = False,
        include_email: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(pending_only, bool) or not isinstance(include_email, bool):
            raise _invalid("Comment filters must be booleans.")
        if post_slug is not None:
            path = f"posts/{_validate_slug(post_slug)}/comments/"
        else:
            path = "comments/pending/" if pending_only else "comments/"
        data = await self._request("GET", path)
        if not isinstance(data.get("comment_list"), list):
            raise _invalid_response()
        comments = [self._comment(comment, include_email) for comment in data["comment_list"]]
        # The API lacks a combined post + pending endpoint.
        return [comment for comment in comments if not pending_only or not comment["is_approved"]]

    async def get_comment(self, comment_id: int, *, include_email: bool = False) -> dict[str, Any]:
        if type(comment_id) is not int or comment_id < 1:
            raise _invalid("Comment ID must be a positive integer.")
        if not isinstance(include_email, bool):
            raise _invalid("include_email must be a boolean.")
        data = await self._request("GET", f"comments/{comment_id}/")
        comment = self._comment(data.get("comment"), include_email)
        if comment["id"] != comment_id:
            raise _invalid_response()
        return comment
