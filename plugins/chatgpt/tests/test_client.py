"""No live API calls: every network operation uses httpx.MockTransport."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable

import httpx
import pytest

from mataroa_chatgpt.client import (
    MAX_RESPONSE_BYTES,
    MataroaClient,
    MataroaError,
    post_fingerprint,
)

API_KEY = "test-secret-never-use-live"
POST = {
    "ok": True,
    "slug": "hello-world",
    "title": "Hello World",
    "body": "A private draft.\n\nUnicode: café 🌳",
    "published_at": None,
    "url": "https://author.mataroa.blog/blog/hello-world/",
}
PAGE = {
    "slug": "about",
    "title": "About",
    "body": "A biography.",
    "is_hidden": False,
    "url": "https://author.mataroa.blog/about/",
}
COMMENT = {
    "id": 7,
    "post_slug": POST["slug"],
    "post_title": POST["title"],
    "post_url": POST["url"],
    "url": POST["url"] + "#comment-7",
    "created_at": "2026-10-08T12:00:00Z",
    "name": "Reader",
    "email": "private@example.com",
    "body": "Comment text.",
    "is_approved": False,
    "is_author": False,
}
RECEIPT = {"ok": True, "slug": POST["slug"], "url": POST["url"]}


@pytest.fixture
async def make_client():
    clients = []

    def make(handler: Callable, **kwargs) -> MataroaClient:
        client = MataroaClient(API_KEY, transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


def response(data, status=200):
    return httpx.Response(status, json=data)


async def test_posts_normalization_fingerprint_auth_and_timeout(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert request.headers["accept"] == "application/json"
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 10.0,
            "write": 10.0,
            "pool": 10.0,
        }
        if request.url.path == "/api/posts/":
            return response({"ok": True, "post_list": [POST]})
        return response(POST)

    client = make_client(handler)
    posts = await client.list_posts()
    post = await client.get_post("hello-world")
    assert posts == [post]
    assert post["content_sha256"] == post_fingerprint(POST)
    assert "ok" not in post
    assert [str(call.url) for call in calls] == [
        "https://mataroa.blog/api/posts/",
        "https://mataroa.blog/api/posts/hello-world/",
    ]


@pytest.mark.parametrize("field", ["slug", "title", "body", "published_at"])
def test_fingerprint_binds_every_reviewed_field(field):
    changed = dict(POST)
    changed[field] = "2026-10-08" if field == "published_at" else "changed"
    assert post_fingerprint(changed) != post_fingerprint(POST)


def test_fingerprint_is_canonical_and_ignores_transport_metadata():
    reversed_keys = dict(reversed(list(POST.items())))
    reversed_keys.update(url="https://different.example/", content_sha256="ignored", ok=False)
    assert post_fingerprint(reversed_keys) == post_fingerprint(POST)
    assert len(post_fingerprint(POST)) == 64


@pytest.mark.parametrize("bad", [{}, {**POST, "body": 42}, {**POST, "published_at": []}])
def test_fingerprint_rejects_missing_or_invalid_fields(bad):
    with pytest.raises(MataroaError) as exc:
        post_fingerprint(bad)
    assert exc.value.code == "invalid_response"


async def test_create_is_draft_only(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/posts/"
        assert json.loads(request.content) == {"title": "My draft", "body": "Markdown **body**"}
        return response(RECEIPT)

    client = make_client(handler)
    assert await client.create_draft("My draft", "Markdown **body**") == RECEIPT
    with pytest.raises(TypeError):
        await client.create_draft("My draft", published_at="2026-10-08")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "title,body",
    [(None, "body"), ("Title", None), ("", ""), ("  ", ""), ("a" * 301, ""), ("Title", 42)],
)
async def test_invalid_draft_content_never_reaches_network(make_client, title, body):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(MataroaError, match="[Tt]itle|[Bb]ody"):
        await client.create_draft(title, body)
    assert not calls


async def test_update_rechecks_and_writes_only_draft_fields(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return response(POST)
        assert json.loads(request.content) == {"title": "Edited", "body": ""}
        return response(RECEIPT)

    client = make_client(handler)
    receipt = await client.update_draft(
        "hello-world", title="Edited", body="", expected_content_sha256=post_fingerprint(POST)
    )
    assert receipt == RECEIPT
    assert [call.method for call in calls] == ["GET", "PATCH"]


@pytest.mark.parametrize("operation", ["update", "publish"])
@pytest.mark.parametrize(
    "changed,code",
    [
        ({"body": "Newer edit"}, "content_changed"),
        ({"published_at": "2026-10-08"}, "already_published"),
    ],
)
async def test_changed_or_published_post_refuses_every_write(make_client, operation, changed, code):
    calls = []

    def handler(request):
        calls.append(request)
        return response({**POST, **changed})

    client = make_client(handler)
    with pytest.raises(MataroaError) as exc:
        if operation == "update":
            await client.update_draft(
                "hello-world", title="Edited", expected_content_sha256=post_fingerprint(POST)
            )
        else:
            await client.publish_post(
                "hello-world",
                published_at="2026-10-08",
                expected_content_sha256=post_fingerprint(POST),
            )
    assert exc.value.code == code
    assert [call.method for call in calls] == ["GET"]


async def test_publish_sends_explicit_date_only(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return response(POST)
        assert request.method == "PATCH"
        assert json.loads(request.content) == {"published_at": "2026-10-08"}
        return response(RECEIPT)

    client = make_client(handler)
    assert (
        await client.publish_post(
            "hello-world", published_at="2026-10-08", expected_content_sha256=post_fingerprint(POST)
        )
        == RECEIPT
    )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "invalid_date",
    [None, "", "today", "20261008", "2026-1-1", "2026-02-30", "2026-10-08T12:00:00Z", True],
)
async def test_invalid_publish_date_never_reaches_network(make_client, invalid_date):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(MataroaError) as exc:
        await client.publish_post(
            "hello-world", published_at=invalid_date, expected_content_sha256=post_fingerprint(POST)
        )
    assert exc.value.code == "invalid_argument"
    assert not calls


@pytest.mark.parametrize("fingerprint", [None, "", "abc", "A" * 64, "0" * 65, 3])
async def test_invalid_fingerprint_never_reaches_network(make_client, fingerprint):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(MataroaError) as exc:
        await client.update_draft(
            "hello-world", body="changed", expected_content_sha256=fingerprint
        )
    assert exc.value.code == "invalid_argument"
    assert not calls


async def test_empty_update_is_refused_without_request(make_client):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(MataroaError, match="at least one"):
        await client.update_draft("hello-world", expected_content_sha256=post_fingerprint(POST))
    assert not calls


@pytest.mark.parametrize(
    "slug",
    [
        "../private",
        "a/b",
        "https://evil.example",
        "a?token=secret",
        "a#frag",
        "%2e%2e",
        "a%2fb",
        "a\\b",
        "",
        "café",
        "a" * 301,
        None,
    ],
)
async def test_path_injection_is_rejected_everywhere(make_client, slug):
    calls = []
    client = make_client(lambda request: calls.append(request))
    operations = [
        client.get_post(slug),
        client.get_page(slug),
        client.list_comments(post_slug=slug if slug is not None else "../invalid"),
        client.update_draft(slug, body="x", expected_content_sha256=post_fingerprint(POST)),
        client.publish_post(
            slug, published_at="2026-10-08", expected_content_sha256=post_fingerprint(POST)
        ),
    ]
    for operation in operations:
        with pytest.raises(MataroaError) as exc:
            await operation
        assert exc.value.code == "invalid_argument"
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "http://mataroa.blog/api/",
        "file:///etc/passwd",
        "//mataroa.blog/api/",
        "https:///api/",
        "https://username:password@mataroa.blog/api/",
        "https://mataroa.blog/api/?x=secret",
        "https://mataroa.blog/api/#fragment",
        "https://mataroa.blog/api/../private/",
        "https://mataroa.blog/api/%2e%2e/",
        "https://mataroa.blog\\@evil.example/api/",
        " https://mataroa.blog/api/",
        "https://mataroa.blog:invalid/api/",
        "https://mataroa.blog:70000/api/",
    ],
)
def test_unsafe_base_url_refused(url):
    with pytest.raises(MataroaError) as exc:
        MataroaClient(API_KEY, base_url=url)
    assert exc.value.code == "invalid_argument"
    assert "password" not in str(exc.value)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
async def test_http_loopback_requires_opt_in(make_client, host):
    base = f"http://{host}:8000/api"
    with pytest.raises(MataroaError):
        MataroaClient(API_KEY, base_url=base)
    client = make_client(
        lambda request: response({"ok": True, "post_list": []}),
        base_url=base,
        allow_insecure_localhost=True,
    )
    assert await client.list_posts() == []
    assert client.base_url == base + "/"


@pytest.mark.parametrize(
    "host",
    ["evil.example", "localhost.evil.example", "10.0.0.1", "0.0.0.0", "127.0.0.1.evil.example"],
)
def test_loopback_opt_in_does_not_allow_other_http_hosts(host):
    with pytest.raises(MataroaError):
        MataroaClient(API_KEY, base_url=f"http://{host}/api/", allow_insecure_localhost=True)


@pytest.mark.parametrize("timeout", [0, -1, 31, float("inf"), float("nan"), None, True])
def test_timeout_is_bounded(timeout):
    with pytest.raises(MataroaError, match="Timeout"):
        MataroaClient(API_KEY, timeout=timeout)


@pytest.mark.parametrize("key", ["", None, "newline\nkey", "space key", "é", "tab\tkey"])
def test_malformed_keys_are_rejected_without_echo(key):
    with pytest.raises(MataroaError) as exc:
        MataroaClient(key)
    assert exc.value.code == "invalid_argument"
    assert "newline" not in str(exc.value)


async def test_secret_is_redacted_in_representations(make_client):
    client = make_client(lambda request: response(POST))
    assert API_KEY not in repr(client)
    assert API_KEY not in repr(vars(client))
    assert API_KEY not in str(client._api_key)


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "redirect_refused"),
        (307, "redirect_refused"),
        (401, "unauthorized"),
        (403, "unauthorized"),
        (404, "not_found"),
        (429, "upstream_error"),
        (500, "upstream_error"),
    ],
)
async def test_status_errors_are_sanitized_and_redirects_never_followed(make_client, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text=f"Leaked upstream secret {API_KEY}",
            headers={"Location": "https://evil.example/capture"},
        )

    client = make_client(handler)
    with pytest.raises(MataroaError) as exc:
        await client.list_posts()
    assert exc.value.code == code
    assert exc.value.status_code == status
    assert API_KEY not in str(exc.value)
    assert API_KEY not in repr(exc.value)
    assert not hasattr(exc.value, "request")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "data", [{"ok": False, "error": API_KEY}, {"ok": "true", "message": API_KEY}, [], None]
)
async def test_api_error_and_non_object_json_are_sanitized(make_client, data):
    client = make_client(lambda request: response(data))
    with pytest.raises(MataroaError) as exc:
        await client.list_posts()
    assert API_KEY not in str(exc.value)


@pytest.mark.parametrize("content", [b"<html>private failure</html>", b"\xff", b"{invalid}"])
async def test_invalid_json_is_sanitized(make_client, content):
    client = make_client(lambda request: httpx.Response(200, content=content))
    with pytest.raises(MataroaError) as exc:
        await client.list_posts()
    assert exc.value.code == "invalid_response"


@pytest.mark.parametrize(
    "exception,code", [(httpx.ConnectError, "network_error"), (httpx.ReadTimeout, "timeout")]
)
async def test_transport_error_has_no_raw_message_and_no_retry(make_client, exception, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception(f"Raw secret {API_KEY}", request=request)

    client = make_client(handler)
    with pytest.raises(MataroaError) as exc:
        await client.create_draft("Title")
    assert exc.value.code == code
    assert API_KEY not in str(exc.value)
    assert exc.value.__suppress_context__
    assert len(calls) == 1


async def test_total_deadline_even_when_transport_ignores_httpx_timeout(make_client):
    async def handler(request):
        await asyncio.sleep(0.1)
        return response(POST)

    client = make_client(handler, timeout=0.005)
    with pytest.raises(MataroaError) as exc:
        await client.get_post("hello-world")
    assert exc.value.code == "timeout"


async def test_response_size_is_bounded(make_client):
    client = make_client(
        lambda request: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
    )
    with pytest.raises(MataroaError) as exc:
        await client.list_posts()
    assert exc.value.code == "response_too_large"


async def test_pages_are_read_only_and_normalized(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/pages/":
            return response({"ok": True, "page_list": [PAGE]})
        return response({"ok": True, **PAGE})

    client = make_client(handler)
    assert await client.list_pages() == [PAGE]
    assert await client.get_page("about") == PAGE
    assert all(request.method == "GET" for request in calls)
    for unavailable in (
        "create_page",
        "update_page",
        "delete_page",
        "delete_post",
        "delete_comment",
        "approve_comment",
    ):
        assert not hasattr(client, unavailable)


@pytest.mark.parametrize(
    "pending,post_slug,path",
    [
        (False, None, "/api/comments/"),
        (True, None, "/api/comments/pending/"),
        (False, "hello-world", "/api/posts/hello-world/comments/"),
        (True, "hello-world", "/api/posts/hello-world/comments/"),
    ],
)
async def test_comment_routes_filters_and_email_redaction(make_client, pending, post_slug, path):
    def handler(request):
        assert request.url.path == path
        return response(
            {"ok": True, "comment_list": [COMMENT, {**COMMENT, "id": 8, "is_approved": True}]}
        )

    client = make_client(handler)
    comments = await client.list_comments(pending_only=pending, post_slug=post_slug)
    assert len(comments) == (1 if pending else 2)
    assert all("email" not in comment for comment in comments)
    included = await client.list_comments(
        pending_only=pending, post_slug=post_slug, include_email=True
    )
    assert included[0]["email"] == "private@example.com"


async def test_get_comment_email_opt_in_does_not_mutate_raw(make_client):
    original = copy.deepcopy(COMMENT)
    client = make_client(lambda request: response({"ok": True, "comment": COMMENT}))
    assert "email" not in await client.get_comment(7)
    assert (await client.get_comment(7, include_email=True))["email"] == "private@example.com"
    assert COMMENT == original


@pytest.mark.parametrize("comment_id", [0, -1, "7", True, "../private", 1.5])
async def test_invalid_comment_id_cannot_form_a_url(make_client, comment_id):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(MataroaError):
        await client.get_comment(comment_id)
    assert not calls


@pytest.mark.parametrize(
    "resource,data",
    [
        ("post", {**POST, "published_at": False}),
        ("post", {**POST, "slug": "other-post"}),
        ("post", {**POST, "body": 3}),
        ("page", {**PAGE, "is_hidden": "yes"}),
        ("page", {**PAGE, "slug": "other-page"}),
        ("comment", {**COMMENT, "id": True}),
        ("comment", {**COMMENT, "id": 8}),
    ],
)
async def test_malformed_or_mismatched_resources_are_rejected(make_client, resource, data):
    envelope = {"ok": True, "comment": data} if resource == "comment" else {"ok": True, **data}
    client = make_client(lambda request: response(envelope))
    with pytest.raises(MataroaError) as exc:
        if resource == "post":
            await client.get_post("hello-world")
        elif resource == "page":
            await client.get_page("about")
        else:
            await client.get_comment(7)
    assert exc.value.code == "invalid_response"


async def test_injected_client_auth_and_redirect_settings_are_overridden():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, auth=("wrong", "auth")
    ) as http:
        async with MataroaClient(API_KEY, http_client=http) as client:
            with pytest.raises(MataroaError) as exc:
                await client.list_posts()
            assert exc.value.code == "redirect_refused"
        assert not http.is_closed
    assert http.is_closed
    assert len(calls) == 1


async def test_owned_client_is_closed_on_context_exit():
    client = MataroaClient(API_KEY, transport=httpx.MockTransport(lambda request: response(POST)))
    async with client:
        assert not client._http.is_closed
    assert client._http.is_closed


async def test_injection_choices_are_mutually_exclusive():
    transport = httpx.MockTransport(lambda request: response(POST))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(MataroaError, match="either"):
            MataroaClient(API_KEY, http_client=http, transport=transport)


async def test_nullable_model_fields_are_preserved(make_client):
    def handler(request):
        if "/pages/" in request.url.path:
            return response({"ok": True, **PAGE, "body": None})
        if "/comments/" in request.url.path:
            return response({"ok": True, "comment": {**COMMENT, "name": None}})
        return response({**POST, "body": None})

    client = make_client(handler)
    assert (await client.get_post("hello-world"))["body"] is None
    assert (await client.get_page("about"))["body"] is None
    assert (await client.get_comment(7))["name"] is None
    assert post_fingerprint({**POST, "body": None}) != post_fingerprint({**POST, "body": ""})


async def test_invalid_unicode_input_is_safely_rejected(make_client):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(MataroaError, match="Unicode"):
        await client.create_draft("Valid title", "\ud800")
    with pytest.raises(MataroaError) as exc:
        post_fingerprint({**POST, "body": "\ud800"})
    assert exc.value.code == "invalid_response"
    assert not calls
