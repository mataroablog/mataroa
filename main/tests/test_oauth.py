"""Real local OAuth flow tests: Django views, Toolkit database and MCP verifier.

All clients, users and grants here are disposable test data, never deployment
credentials. No network service is contacted.
"""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import SkipTest, skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone
from django.views.debug import SafeExceptionReporterFilter

if not settings.MATAROA_CHATGPT_ENABLED:
    raise SkipTest("Enable the ChatGPT integration to run OAuth tests.")

from oauth2_provider.models import (  # noqa: E402
    AccessToken,
    Application,
    Grant,
    RefreshToken,
    set_token_value,
)
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError  # noqa: E402

from mataroa.oauth import DjangoTokenVerifier  # noqa: E402

RESOURCE = "https://mataroa.blog/mcp"
REDIRECT = "https://chatgpt.com/connector_platform/oauth/callback"
VERIFIER = "this-is-a-disposable-test-code-verifier-" + "x" * 32
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)
SCOPES = "blog:read drafts:write posts:publish"


class OAuthTestHelpers:
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "oauthalice", password="test-password"
        )
        self.application = Application.objects.create(
            client_id="test-chatgpt",
            name="ChatGPT test fixture",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT,
            skip_authorization=False,
        )
        self.client.force_login(self.user)
        self.verifier = DjangoTokenVerifier()

    def auth_parameters(self, **overrides):
        values = {
            "response_type": "code",
            "client_id": self.application.client_id,
            "redirect_uri": REDIRECT,
            "scope": SCOPES,
            "state": "disposable-test-state",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        }
        values.update(overrides)
        return values

    def get(self, path, values=None, client=None):
        return (client or self.client).get(
            path, values or {}, secure=True, HTTP_HOST="mataroa.blog"
        )

    def post(self, path, values, client=None):
        return (client or self.client).post(
            path,
            urlencode(values, doseq=True),
            content_type="application/x-www-form-urlencoded",
            secure=True,
            HTTP_HOST="mataroa.blog",
        )

    def authorize(self, **overrides):
        parameters = self.auth_parameters(**overrides)
        response = self.get("/oauth/authorize/", parameters)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(
            response, "Read your posts, drafts, pages, and blog comments"
        )
        response = self.post("/oauth/authorize/", {**parameters, "allow": "true"})
        self.assertEqual(response.status_code, 302, response.content)
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(query["state"], [parameters["state"]])
        self.assertEqual(query["iss"], [settings.MATAROA_MCP_ISSUER_URL])
        self.assertIn("code", query, query)
        return query["code"][0]

    def exchange(self, code, **overrides):
        values = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.application.client_id,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
        }
        values.update(overrides)
        return self.post("/oauth/token/", values)

    def issue(self, **authorize_overrides):
        code = self.authorize(**authorize_overrides)
        response = self.exchange(code)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def verify(self, token):
        return async_to_sync(self.verifier.verify_token)(token)


@override_settings(MATAROA_CHATGPT_CLIENT_IDS=("test-chatgpt",))
class OAuthFlowTests(OAuthTestHelpers, TestCase):
    def test_end_to_end_consent_pkce_and_opaque_user_bound_token(self):
        tokens = self.issue()
        verified = self.verify(tokens["access_token"])
        self.assertEqual(verified.subject, str(self.user.pk))
        self.assertEqual(verified.resource, RESOURCE)
        self.assertEqual(verified.scopes, SCOPES.split())
        self.assertEqual(verified.claims, {"iss": "https://mataroa.blog"})
        self.assertEqual(verified.client_id, "test-chatgpt")
        self.assertGreater(verified.expires_at, timezone.now().timestamp())
        self.assertEqual(AccessToken.objects.get().resource, [RESOURCE])
        self.assertEqual(RefreshToken.objects.get().resource, [RESOURCE])
        self.assertEqual(AccessToken.objects.get().token, "")  # hashed at rest
        self.assertEqual(RefreshToken.objects.get().token, "")
        self.assertEqual(Grant.objects.count(), 0)

    def test_two_accounts_have_different_subjects(self):
        alice = self.issue()
        bob = get_user_model().objects.create_user("oauthbob", password="test-password")
        self.client.force_login(bob)
        bob_tokens = self.issue()
        self.assertEqual(self.verify(alice["access_token"]).subject, str(self.user.pk))
        self.assertEqual(self.verify(bob_tokens["access_token"]).subject, str(bob.pk))

    def test_discovery_is_canonical_and_minimal(self):
        metadata = self.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(metadata["issuer"], "https://mataroa.blog")
        self.assertEqual(
            metadata["authorization_endpoint"], "https://mataroa.blog/oauth/authorize/"
        )
        self.assertEqual(
            metadata["token_endpoint"], "https://mataroa.blog/oauth/token/"
        )
        self.assertEqual(
            metadata["revocation_endpoint"], "https://mataroa.blog/oauth/revoke/"
        )
        self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(
            metadata["grant_types_supported"], ["authorization_code", "refresh_token"]
        )
        self.assertEqual(metadata["response_types_supported"], ["code"])
        self.assertFalse(metadata["client_id_metadata_document_supported"])
        self.assertNotIn("registration_endpoint", metadata)
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            resource = self.get(path).json()
            self.assertEqual(resource["resource"], RESOURCE)
            self.assertEqual(
                resource["authorization_servers"], ["https://mataroa.blog"]
            )
            self.assertEqual(resource["bearer_methods_supported"], ["header"])

    def test_no_registration_device_or_introspection_endpoints(self):
        for path in (
            "/oauth/register/",
            "/oauth/applications/register/",
            "/oauth/device/",
            "/oauth/introspect/",
        ):
            self.assertEqual(self.get(path).status_code, 404)

    def test_discovery_rejects_wrong_host_and_plain_http(self):
        self.assertEqual(
            self.client.get(
                "/.well-known/oauth-authorization-server",
                secure=True,
                HTTP_HOST="evil.example",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                "/.well-known/oauth-authorization-server", HTTP_HOST="mataroa.blog"
            ).status_code,
            400,
        )

    def test_get_requires_login_and_never_issues_a_grant(self):
        anonymous = Client()
        response = self.get(
            "/oauth/authorize/", self.auth_parameters(), client=anonymous
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/?next="))
        self.assertEqual(Grant.objects.count(), 0)
        self.assertEqual(
            self.get("/oauth/authorize/", self.auth_parameters()).status_code, 200
        )
        self.assertEqual(Grant.objects.count(), 0)

    def test_consent_cancel_creates_no_grant(self):
        response = self.post("/oauth/authorize/", self.auth_parameters())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(response.url).query)["error"], ["access_denied"]
        )
        self.assertEqual(Grant.objects.count(), 0)

    def test_consent_is_csrf_protected_but_token_exchange_uses_client_auth(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(
            self.post(
                "/oauth/authorize/",
                {**self.auth_parameters(), "allow": "true"},
                client=browser,
            ).status_code,
            403,
        )
        code = self.authorize()
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "authorization_code",
                "client_id": "test-chatgpt",
                "code": code,
                "redirect_uri": REDIRECT,
                "resource": RESOURCE,
                "code_verifier": VERIFIER,
            },
            client=browser,
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_password_login_and_consent_succeed_with_real_csrf_checks(self):
        browser = Client(enforce_csrf_checks=True)
        response = self.get("/oauth/authorize/", self.auth_parameters(), client=browser)
        self.assertEqual(response.status_code, 302)
        login_url = response.url
        response = self.get(login_url, client=browser)
        self.assertEqual(response.status_code, 200)
        next_url = parse_qs(urlsplit(login_url).query)["next"][0]
        response = browser.post(
            "/accounts/login/",
            urlencode(
                {
                    "username": self.user.username,
                    "password": "test-password",
                    "next": next_url,
                    "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
                }
            ),
            content_type="application/x-www-form-urlencoded",
            secure=True,
            HTTP_HOST="mataroa.blog",
            HTTP_REFERER="https://mataroa.blog/accounts/login/",
        )
        self.assertEqual(response.status_code, 302, response.content)
        response = self.get(response.url, client=browser)
        self.assertEqual(response.status_code, 200)
        response = browser.post(
            "/oauth/authorize/",
            urlencode(
                {
                    **self.auth_parameters(),
                    "allow": "true",
                    "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
                }
            ),
            content_type="application/x-www-form-urlencoded",
            secure=True,
            HTTP_HOST="mataroa.blog",
            HTTP_REFERER="https://mataroa.blog/oauth/authorize/",
        )
        self.assertEqual(response.status_code, 302, response.content)
        code = parse_qs(urlsplit(response.url).query)["code"][0]
        tokens = self.exchange(code)
        self.assertEqual(tokens.status_code, 200, tokens.content)
        self.assertEqual(
            self.verify(tokens.json()["access_token"]).subject, str(self.user.pk)
        )

    def test_authorization_rejects_missing_or_non_s256_pkce(self):
        for overrides in (
            {"code_challenge": ""},
            {"code_challenge_method": "plain"},
            {"code_challenge_method": ""},
            {"code_challenge": "x"},
        ):
            response = self.get("/oauth/authorize/", self.auth_parameters(**overrides))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Grant.objects.count(), 0)

    def test_authorization_requires_state_and_explicit_redirect(self):
        for overrides in ({"state": ""}, {"redirect_uri": ""}):
            self.assertEqual(
                self.get(
                    "/oauth/authorize/", self.auth_parameters(**overrides)
                ).status_code,
                400,
            )

    def test_authorization_requires_one_exact_resource_on_get_and_post(self):
        for audience in (
            "",
            "https://other.example/mcp",
            "https://mataroa.blog",
            RESOURCE + "/",
            RESOURCE + "?x=1",
            [RESOURCE, RESOURCE],
            [RESOURCE, "https://evil.example"],
        ):
            parameters = self.auth_parameters(resource=audience)
            self.assertEqual(self.get("/oauth/authorize/", parameters).status_code, 400)
            self.assertEqual(
                self.post(
                    "/oauth/authorize/", {**parameters, "allow": "true"}
                ).status_code,
                400,
            )
        self.assertEqual(Grant.objects.count(), 0)

    def test_wrong_verifier_and_replayed_code_are_rejected(self):
        code = self.authorize()
        self.assertEqual(self.exchange(code, code_verifier="bad" * 30).status_code, 400)
        response = self.exchange(code)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.exchange(code).status_code, 400)

    def test_token_exchange_requires_exact_resource(self):
        for resource in (
            "",
            "https://evil.example",
            RESOURCE + "/",
            [RESOURCE, "https://evil.example"],
        ):
            code = self.authorize()
            response = self.exchange(code, resource=resource)
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(response.json()["error"], "invalid_target")
        self.assertEqual(AccessToken.objects.count(), 0)

    def test_unregistered_client_and_non_exact_redirect_are_rejected(self):
        for overrides in (
            {"client_id": "https://evil.example/client.json"},
            {"redirect_uri": REDIRECT + "/"},
            {"redirect_uri": REDIRECT + "?extra=yes"},
        ):
            response = self.get("/oauth/authorize/", self.auth_parameters(**overrides))
            self.assertEqual(response.status_code, 400, response.content)
        code = self.authorize()
        self.assertEqual(
            self.exchange(code, redirect_uri=REDIRECT + "/").status_code, 400
        )

    def test_unknown_scopes_and_missing_read_scope_are_rejected(self):
        for scopes in ("blog:read admin", "posts:publish", "drafts:write"):
            response = self.get("/oauth/authorize/", self.auth_parameters(scope=scopes))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(
                parse_qs(urlsplit(response.url).query)["error"], ["invalid_scope"]
            )

    def test_read_only_consent_stays_read_only(self):
        token = self.issue(scope="blog:read")
        self.assertEqual(self.verify(token["access_token"]).scopes, ["blog:read"])

    def test_refresh_rotates_and_inherits_exact_resource(self):
        token = self.issue()
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": token["refresh_token"],
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        rotated = response.json()
        self.assertNotEqual(rotated["refresh_token"], token["refresh_token"])
        self.assertIsNone(self.verify(token["access_token"]))
        self.assertEqual(self.verify(rotated["access_token"]).resource, RESOURCE)
        self.assertEqual(
            RefreshToken.objects.get(revoked__isnull=True).resource, [RESOURCE]
        )
        replay = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": token["refresh_token"],
            },
        )
        self.assertEqual(replay.status_code, 400)
        self.assertIsNone(
            self.verify(rotated["access_token"])
        )  # family reuse protection

    def test_refresh_cannot_change_audience_or_escalate_scopes(self):
        token = self.issue(scope="blog:read")
        values = {
            "grant_type": "refresh_token",
            "client_id": "test-chatgpt",
            "refresh_token": token["refresh_token"],
        }
        response = self.post(
            "/oauth/token/", {**values, "resource": "https://evil.example/mcp"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_target")
        response = self.post(
            "/oauth/token/", {**values, "scope": "blog:read posts:publish"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_scope")

    def test_revocation_immediately_invalidates_the_token(self):
        tokens = self.issue()
        response = self.post(
            "/oauth/revoke/",
            {
                "client_id": "test-chatgpt",
                "token": tokens["refresh_token"],
                "token_type_hint": "refresh_token",
            },
            client=Client(enforce_csrf_checks=True),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(self.verify(tokens["access_token"]))
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": tokens["refresh_token"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_inactive_user_cannot_verify_or_refresh(self):
        token = self.issue()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertIsNone(self.verify(token["access_token"]))
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": token["refresh_token"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_user_disabled_after_consent_cannot_exchange_code(self):
        code = self.authorize()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.exchange(code).status_code, 400)

    def test_expired_unbound_wrong_audience_and_unknown_tokens_are_rejected(self):
        self.assertIsNone(self.verify("unknown-token"))
        self.assertIsNone(self.verify(self.user.api_key))
        tokens = self.issue()
        record = AccessToken.objects.get()
        for resource in (
            [],
            ["https://other.example/mcp"],
            [RESOURCE, "https://other.example"],
            [RESOURCE + "/"],
        ):
            record.resource = resource
            record.save(update_fields=["resource"])
            self.assertIsNone(self.verify(tokens["access_token"]))
        record.resource = [RESOURCE]
        record.expires = timezone.now() - timedelta(seconds=1)
        record.save(update_fields=["resource", "expires"])
        self.assertIsNone(self.verify(tokens["access_token"]))

    def test_removed_client_allowlist_revokes_access_without_cached_identity(self):
        token = self.issue()
        with override_settings(MATAROA_CHATGPT_CLIENT_IDS=()):
            self.assertIsNone(self.verify(token["access_token"]))
            self.assertEqual(
                self.post(
                    "/oauth/token/",
                    {
                        "grant_type": "refresh_token",
                        "client_id": "test-chatgpt",
                        "refresh_token": token["refresh_token"],
                    },
                ).status_code,
                401,
            )

    def test_other_grant_types_and_skip_consent_clients_are_rejected(self):
        for grant in ("client_credentials", "password"):
            response = self.post(
                "/oauth/token/", {"client_id": "test-chatgpt", "grant_type": grant}
            )
            self.assertIn(response.status_code, (400, 401))
        self.application.skip_authorization = True
        self.application.save(update_fields=["skip_authorization"])
        self.assertEqual(
            self.get("/oauth/authorize/", self.auth_parameters()).status_code, 400
        )

    def test_unbound_legacy_refresh_token_cannot_acquire_resource_binding(self):
        tokens = self.issue()
        RefreshToken.objects.update(resource=[])
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": tokens["refresh_token"],
                "resource": RESOURCE,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_verifier_refuses_unknown_scope_and_no_user(self):
        raw = "test-forged-row-token"
        token = AccessToken(
            user=self.user,
            application=self.application,
            scope="blog:read admin",
            resource=[RESOURCE],
            expires=timezone.now() + timedelta(minutes=5),
        )
        set_token_value(token, raw)
        token.save()
        self.assertIsNone(self.verify(raw))
        token.scope = "blog:read"
        token.user = None
        token.save(update_fields=["scope", "user"])
        self.assertIsNone(self.verify(raw))

    def test_confidential_client_requires_secret_and_s256(self):
        self.application.client_type = Application.CLIENT_CONFIDENTIAL
        self.application.client_secret = "disposable-confidential-client-test-secret"
        self.application.save(update_fields=["client_type", "client_secret"])
        code = self.authorize()
        self.assertEqual(self.exchange(code).status_code, 401)
        self.assertEqual(
            self.exchange(code, client_secret="wrong-test-secret").status_code, 401
        )
        response = self.exchange(
            code, client_secret="disposable-confidential-client-test-secret"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.verify(response.json()["access_token"]).subject, str(self.user.pk)
        )

    def test_confidential_client_basic_auth(self):
        self.application.client_type = Application.CLIENT_CONFIDENTIAL
        self.application.client_secret = "disposable-basic-auth-test-secret"
        self.application.save(update_fields=["client_type", "client_secret"])
        code = self.authorize()
        basic = base64.b64encode(
            b"test-chatgpt:disposable-basic-auth-test-secret"
        ).decode()
        response = self.client.post(
            "/oauth/token/",
            urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "resource": RESOURCE,
                    "code_verifier": VERIFIER,
                }
            ),
            content_type="application/x-www-form-urlencoded",
            secure=True,
            HTTP_HOST="mataroa.blog",
            HTTP_AUTHORIZATION=f"Basic {basic}",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_code_cannot_be_exchanged_by_another_registered_client(self):
        Application.objects.create(
            client_id="second-client",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT,
        )
        code = self.authorize()
        with override_settings(
            MATAROA_CHATGPT_CLIENT_IDS=("test-chatgpt", "second-client")
        ):
            self.assertEqual(
                self.exchange(code, client_id="second-client").status_code, 400
            )
        self.assertEqual(AccessToken.objects.count(), 0)

    def test_expired_code_and_unbound_legacy_grant_cannot_be_exchanged(self):
        code = self.authorize()
        Grant.objects.filter(code=code).update(
            expires=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.exchange(code).status_code, 400)
        code = self.authorize()
        Grant.objects.filter(code=code).update(resource=[])
        self.assertEqual(self.exchange(code).status_code, 400)
        self.assertEqual(AccessToken.objects.count(), 0)

    def test_code_exchange_requires_explicit_redirect_and_valid_verifier_syntax(self):
        code = self.authorize()
        self.assertEqual(self.exchange(code, redirect_uri="").status_code, 400)
        self.assertEqual(self.exchange(code, code_verifier="short").status_code, 400)
        self.assertEqual(self.exchange(code, code_verifier="+" * 43).status_code, 400)
        self.assertEqual(self.exchange(code, code_verifier="x" * 129).status_code, 400)
        self.assertEqual(AccessToken.objects.count(), 0)

    def test_duplicate_oauth_parameters_are_not_silently_overwritten(self):
        self.assertEqual(
            self.get(
                "/oauth/authorize/", self.auth_parameters(state=["first", "second"])
            ).status_code,
            400,
        )
        code = self.authorize()
        self.assertEqual(
            self.exchange(code, client_id=["wrong", "test-chatgpt"]).status_code, 400
        )
        self.assertEqual(AccessToken.objects.count(), 0)

    def test_expired_refresh_token_is_rejected(self):
        tokens = self.issue()
        AccessToken.objects.update(expires=timezone.now() - timedelta(days=31))
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": tokens["refresh_token"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_refresh_can_narrow_scopes_and_preserves_narrowing(self):
        tokens = self.issue()
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": tokens["refresh_token"],
                "scope": "blog:read",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        narrowed = response.json()
        self.assertEqual(self.verify(narrowed["access_token"]).scopes, ["blog:read"])
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": narrowed["refresh_token"],
                "scope": SCOPES,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_failed_code_consumption_rolls_back_new_tokens(self):
        code = self.authorize()
        with patch(
            "mataroa.oauth.MataroaOAuth2Validator.invalidate_authorization_code",
            side_effect=InvalidGrantError(),
        ):
            response = self.exchange(code)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(AccessToken.objects.count(), 0)
        self.assertEqual(RefreshToken.objects.count(), 0)
        self.assertTrue(Grant.objects.filter(code=code).exists())

    def test_auto_approval_cannot_bypass_explicit_consent(self):
        self.issue()
        response = self.get(
            "/oauth/authorize/", self.auth_parameters(approval_prompt="auto")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Grant.objects.count(), 0)
        response = self.get(
            "/oauth/authorize/", self.auth_parameters(approval_prompt="force")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Grant.objects.count(), 0)

    def test_unknown_or_deleted_client_consent_post_returns_safe_error(self):
        values = {**self.auth_parameters(client_id="unknown-client"), "allow": "true"}
        response = self.post("/oauth/authorize/", values)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], "invalid_client")
        values = {**self.auth_parameters(), "allow": "true"}
        self.assertEqual(
            self.get("/oauth/authorize/", self.auth_parameters()).status_code, 200
        )
        self.application.delete()
        response = self.post("/oauth/authorize/", values)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], "invalid_client")
        self.assertEqual(Grant.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_error_reports_redact_oauth_secrets_and_all_stack_locals(self):
        from mataroa.oauth import MataroaTokenView

        secret = hashlib.sha256(b"disposable-error-report-fixture").hexdigest()
        values = {
            "grant_type": "refresh_token",
            "client_id": "test-chatgpt",
            "refresh_token": secret,
            "code": secret,
            "code_verifier": secret,
            "client_secret": secret,
        }
        request = RequestFactory().post(
            "/oauth/token/", values, secure=True, HTTP_HOST="mataroa.blog"
        )
        with patch(
            "oauth2_provider.views.base.TokenView.create_token_response",
            side_effect=RuntimeError("synthetic test failure"),
        ):
            try:
                MataroaTokenView.as_view()(request)
            except RuntimeError as error:
                frame = error.__traceback__.tb_frame
            else:
                self.fail("Synthetic token failure did not run")
        self.assertEqual(request.sensitive_post_parameters, "__ALL__")
        standard_post = SafeExceptionReporterFilter().get_post_parameters(request)
        for key in values:
            self.assertNotEqual(standard_post[key], values[key])
        filtered = request.exception_reporter_filter.get_traceback_frame_variables(
            request, frame
        )
        self.assertTrue(
            all(
                value == SafeExceptionReporterFilter.cleansed_substitute
                for _, value in filtered
            )
        )
        self.assertNotIn(
            secret, str(request.exception_reporter_filter.get_post_parameters(request))
        )

    def test_refresh_lock_is_acquired_before_validation_and_rotation(self):
        from mataroa.oauth import MataroaOAuth2Validator

        tokens = self.issue()
        locked = []
        original_lock = QuerySet.select_for_update
        original_validate = MataroaOAuth2Validator.validate_refresh_token

        def track_lock(queryset, *args, **kwargs):
            if queryset.model is RefreshToken:
                locked.append(queryset.model)
            return original_lock(queryset, *args, **kwargs)

        def track_validate(validator, *args, **kwargs):
            self.assertTrue(locked, "Refresh row lock must precede Toolkit validation")
            self.assertTrue(connection.in_atomic_block)
            return original_validate(validator, *args, **kwargs)

        with (
            patch.object(QuerySet, "select_for_update", track_lock),
            patch.object(
                MataroaOAuth2Validator, "validate_refresh_token", track_validate
            ),
        ):
            response = self.post(
                "/oauth/token/",
                {
                    "grant_type": "refresh_token",
                    "client_id": "test-chatgpt",
                    "refresh_token": tokens["refresh_token"],
                },
            )
        self.assertEqual(response.status_code, 200, response.content)


@skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL row-lock concurrency needs PostgreSQL",
)
@override_settings(MATAROA_CHATGPT_CLIENT_IDS=("test-chatgpt",))
class OAuthRefreshConcurrencyTests(OAuthTestHelpers, TransactionTestCase):
    def test_concurrent_refresh_serializes_and_replay_revokes_family(self):
        tokens = self.issue()
        barrier = Barrier(2)

        def rotate():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self.post(
                    "/oauth/token/",
                    {
                        "grant_type": "refresh_token",
                        "client_id": "test-chatgpt",
                        "refresh_token": tokens["refresh_token"],
                    },
                    client=Client(),
                )
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(rotate) for _ in range(2)]
            outcomes = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(status for status, _ in outcomes), [200, 400])
        success = next(body for status, body in outcomes if status == 200)
        self.assertIsNone(self.verify(success["access_token"]))
        self.assertFalse(RefreshToken.objects.filter(revoked__isnull=True).exists())

    def test_concurrent_code_exchange_consumes_grant_exactly_once(self):
        code = self.authorize()
        barrier = Barrier(2)

        def exchange():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self.post(
                    "/oauth/token/",
                    {
                        "grant_type": "authorization_code",
                        "client_id": "test-chatgpt",
                        "code": code,
                        "code_verifier": VERIFIER,
                        "redirect_uri": REDIRECT,
                        "resource": RESOURCE,
                    },
                    client=Client(),
                )
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(exchange) for _ in range(2)]
            outcomes = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(status for status, _ in outcomes), [200, 400])
        success = next(body for status, body in outcomes if status == 200)
        self.assertEqual(
            self.verify(success["access_token"]).subject, str(self.user.pk)
        )
        self.assertEqual(AccessToken.objects.count(), 1)
        self.assertEqual(RefreshToken.objects.count(), 1)
        self.assertEqual(RefreshToken.objects.filter(revoked__isnull=True).count(), 1)
        self.assertEqual(Grant.objects.count(), 0)
