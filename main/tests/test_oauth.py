"""Real local OAuth flow tests: Django views, Mataroa database and MCP verifier.

All clients, users and grants here are disposable test data, never deployment
credentials. No network service is contacted.
"""

import base64
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html import unescape
from threading import Barrier
from unittest import SkipTest, skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
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

from main.models import OAuthClient, OAuthGrant, OAuthToken  # noqa: E402
from mataroa.oauth import token_hash, verify_access_token  # noqa: E402

RESOURCE = "https://mataroa.blog/mcp"
REDIRECT = "https://chatgpt.com/connector_platform/oauth/callback"
VERIFIER = "this-is-a-disposable-test-code-verifier-" + "x" * 32
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)
SCOPES = "blog:read drafts:write posts:publish posts:delete"


class OAuthTestHelpers:
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "oauthalice", password="test-password"
        )
        self.application = OAuthClient.objects.create(
            client_id="test-chatgpt",
            name="ChatGPT test fixture",
            client_type="public",
            redirect_uris=REDIRECT,
        )
        self.client.force_login(self.user)

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
        return verify_access_token(token)


@override_settings(MATAROA_CHATGPT_CLIENT_IDS=("test-chatgpt",))
class OAuthFlowTests(OAuthTestHelpers, TestCase):
    def test_end_to_end_consent_pkce_and_opaque_user_bound_token(self):
        tokens = self.issue()
        verified = self.verify(tokens["access_token"])
        self.assertEqual(verified.grant.user_id, self.user.pk)
        self.assertEqual(verified.grant.resource, RESOURCE)
        self.assertEqual(verified.scope.split(), SCOPES.split())
        self.assertEqual(verified.grant.client.client_id, "test-chatgpt")
        self.assertGreater(verified.access_expires, timezone.now())
        self.assertEqual(OAuthToken.objects.get().grant.resource, RESOURCE)
        record = OAuthToken.objects.get()
        self.assertEqual(record.access_hash, token_hash(tokens["access_token"]))
        self.assertEqual(record.refresh_hash, token_hash(tokens["refresh_token"]))
        self.assertNotIn(tokens["access_token"], str(record.__dict__))
        self.assertNotIn(tokens["refresh_token"], str(record.__dict__))
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

    def test_delete_permission_is_disclosed_and_cannot_be_added_by_refresh(self):
        response = self.get(
            "/oauth/authorize/", self.auth_parameters(scope="blog:read posts:delete")
        )
        self.assertContains(
            response, "Permanently delete posts, their comments and page-view records"
        )
        tokens = self.issue(scope="blog:read posts:publish")
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": tokens["refresh_token"],
                "scope": "blog:read posts:publish posts:delete",
                "resource": RESOURCE,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_scope")

    def test_two_accounts_have_different_subjects(self):
        alice = self.issue()
        bob = get_user_model().objects.create_user("oauthbob", password="test-password")
        self.client.force_login(bob)
        bob_tokens = self.issue()
        self.assertEqual(self.verify(alice["access_token"]).grant.user_id, self.user.pk)
        self.assertEqual(self.verify(bob_tokens["access_token"]).grant.user_id, bob.pk)

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
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)
        self.assertEqual(
            self.get("/oauth/authorize/", self.auth_parameters()).status_code, 200
        )
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

    def test_consent_cancel_creates_no_grant(self):
        response = self.post("/oauth/authorize/", self.auth_parameters())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(response.url).query)["error"], ["access_denied"]
        )
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

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
            self.verify(tokens.json()["access_token"]).grant.user_id, self.user.pk
        )

    def test_signup_resumes_oauth_after_validation_error_with_csrf_checks(self):
        browser = Client(enforce_csrf_checks=True)
        parameters = self.auth_parameters(state="state + / ? & = %")
        response = self.get("/oauth/authorize/", parameters, client=browser)
        next_url = parse_qs(urlsplit(response.url).query)["next"][0]
        response = self.get(response.url, client=browser)
        signup_url = unescape(
            re.search(r'href="([^"]+)">Sign up</a>', response.content.decode())[1]
        )
        self.assertEqual(parse_qs(urlsplit(signup_url).query)["next"], [next_url])

        def submit(path, values):
            return browser.post(
                path,
                urlencode(
                    {
                        **values,
                        "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
                    }
                ),
                content_type="application/x-www-form-urlencoded",
                secure=True,
                HTTP_HOST="mataroa.blog",
                HTTP_REFERER="https://mataroa.blog" + path,
            )

        response = self.get(signup_url, client=browser)
        self.assertContains(
            response, f'<input type="hidden" name="next" value="{next_url}">', html=True
        )
        response = submit(signup_url, {"next": response.context["next"]})
        second_url = response.url
        response = self.get(second_url, client=browser)
        self.assertEqual(response.context["next"], next_url)
        # Switching back to login must also retain the authorization request.
        login_url = unescape(
            re.search(r'href="([^"]+)">Log in</a>', response.content.decode())[1]
        )
        self.assertEqual(parse_qs(urlsplit(login_url).query)["next"], [next_url])
        values = {
            "username": "oauthnewuser",
            "password1": "abcdef123456",
            "password2": "does-not-match",
            "next": response.context["next"],
        }
        response = submit(second_url, values)
        self.assertEqual(response.status_code, 200)
        self.assertIn("password2", response.context["form"].errors)
        self.assertContains(
            response, f'<input type="hidden" name="next" value="{next_url}">', html=True
        )
        values.update(password2=values["password1"], next=response.context["next"])
        response = submit(second_url, values)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, next_url)
        self.assertFalse(OAuthGrant.objects.exists())

        response = self.get(response.url, client=browser)
        self.assertContains(response, "Allow access")
        self.assertEqual(response.context["fields"], parameters)
        self.assertEqual(response.context["user"].username, "oauthnewuser")
        response = submit(
            "/oauth/authorize/", {**response.context["fields"], "allow": "true"}
        )
        self.assertEqual(response.status_code, 302)
        callback = parse_qs(urlsplit(response.url).query)
        self.assertEqual(callback["state"], [parameters["state"]])
        tokens = self.exchange(callback["code"][0])
        self.assertEqual(tokens.status_code, 200, tokens.content)
        self.assertEqual(
            self.verify(tokens.json()["access_token"]).grant.user.username,
            "oauthnewuser",
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
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

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
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

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
        self.assertEqual(OAuthToken.objects.count(), 0)

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

    def test_repeated_scope_names_are_stored_once(self):
        tokens = self.issue(scope=" ".join(["blog:read"] * 100))
        self.assertEqual(tokens["scope"], "blog:read")
        response = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": tokens["refresh_token"],
                "scope": " ".join(["blog:read"] * 100),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "blog:read")

    def test_read_only_consent_stays_read_only(self):
        token = self.issue(scope="blog:read")
        self.assertEqual(
            self.verify(token["access_token"]).scope.split(), ["blog:read"]
        )

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
        self.assertEqual(self.verify(rotated["access_token"]).grant.resource, RESOURCE)
        self.assertEqual(OAuthToken.objects.get(revoked=False).grant.resource, RESOURCE)
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
        record = OAuthGrant.objects.get()
        for resource in ("", "https://other.example/mcp", RESOURCE + "/"):
            record.resource = resource
            record.save(update_fields=["resource"])
            self.assertIsNone(self.verify(tokens["access_token"]))
        record.resource = RESOURCE
        record.save(update_fields=["resource"])
        OAuthToken.objects.update(access_expires=timezone.now() - timedelta(seconds=1))
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

    def test_other_grant_types_are_rejected(self):
        for grant in ("client_credentials", "password"):
            response = self.post(
                "/oauth/token/", {"client_id": "test-chatgpt", "grant_type": grant}
            )
            self.assertIn(response.status_code, (400, 401))

    def test_unbound_refresh_token_cannot_acquire_resource_binding(self):
        tokens = self.issue()
        OAuthGrant.objects.update(resource="")
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

    def test_verifier_refuses_unknown_scope_and_deleted_user(self):
        tokens = self.issue()
        OAuthToken.objects.update(scope="blog:read admin")
        self.assertIsNone(self.verify(tokens["access_token"]))
        OAuthToken.objects.update(scope="blog:read")
        self.user.delete()
        self.assertIsNone(self.verify(tokens["access_token"]))

    def test_confidential_client_requires_secret_and_s256(self):
        self.application.client_type = "confidential"
        self.application.set_secret("disposable-confidential-client-test-secret")
        self.application.save(update_fields=["client_type", "secret_hash"])
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
            self.verify(response.json()["access_token"]).grant.user_id, self.user.pk
        )

    def test_confidential_client_basic_auth(self):
        self.application.client_type = "confidential"
        self.application.set_secret("disposable-basic-auth-test-secret")
        self.application.save(update_fields=["client_type", "secret_hash"])
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
        OAuthClient.objects.create(
            client_id="second-client",
            client_type="public",
            redirect_uris=REDIRECT,
        )
        code = self.authorize()
        with override_settings(
            MATAROA_CHATGPT_CLIENT_IDS=("test-chatgpt", "second-client")
        ):
            self.assertEqual(
                self.exchange(code, client_id="second-client").status_code, 400
            )
        self.assertEqual(OAuthToken.objects.count(), 0)

    def test_expired_code_and_unbound_grant_cannot_be_exchanged(self):
        code = self.authorize()
        OAuthGrant.objects.filter(code_hash=token_hash(code)).update(
            code_expires=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.exchange(code).status_code, 400)
        code = self.authorize()
        OAuthGrant.objects.filter(code_hash=token_hash(code)).update(resource="")
        self.assertEqual(self.exchange(code).status_code, 400)
        self.assertEqual(OAuthToken.objects.count(), 0)

    def test_code_exchange_requires_explicit_redirect_and_valid_verifier_syntax(self):
        code = self.authorize()
        self.assertEqual(self.exchange(code, redirect_uri="").status_code, 400)
        self.assertEqual(self.exchange(code, code_verifier="short").status_code, 400)
        self.assertEqual(self.exchange(code, code_verifier="+" * 43).status_code, 400)
        self.assertEqual(self.exchange(code, code_verifier="x" * 129).status_code, 400)
        self.assertEqual(OAuthToken.objects.count(), 0)

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
        self.assertEqual(OAuthToken.objects.count(), 0)

    def test_expired_refresh_token_is_rejected(self):
        tokens = self.issue()
        OAuthToken.objects.update(refresh_expires=timezone.now() - timedelta(seconds=1))
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
        self.assertEqual(
            self.verify(narrowed["access_token"]).scope.split(), ["blog:read"]
        )
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

    def test_failed_token_issuance_rolls_back_code_consumption(self):
        code = self.authorize()
        with (
            patch(
                "mataroa.oauth.OAuthToken.objects.create",
                side_effect=RuntimeError("synthetic failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.exchange(code)
        self.assertEqual(OAuthToken.objects.count(), 0)
        self.assertFalse(OAuthGrant.objects.get(code_hash=token_hash(code)).consumed)
        self.assertEqual(self.exchange(code).status_code, 200)

    def test_auto_approval_cannot_bypass_explicit_consent(self):
        self.issue()
        response = self.get(
            "/oauth/authorize/", self.auth_parameters(approval_prompt="auto")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)
        response = self.get(
            "/oauth/authorize/", self.auth_parameters(approval_prompt="force")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

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
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

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
            "/oauth/token/",
            urlencode(values),
            content_type="application/x-www-form-urlencoded",
            secure=True,
            HTTP_HOST="mataroa.blog",
        )
        with patch(
            "mataroa.oauth.authenticate_client",
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

    def test_authorization_code_is_hashed_and_replay_revokes_only_its_family(self):
        separate = self.issue()
        code = self.authorize()
        grant = OAuthGrant.objects.get(code_hash=token_hash(code))
        self.assertNotIn(code, str(grant.__dict__))
        self.assertFalse(grant.consumed)
        tokens = self.exchange(code).json()
        # An incorrect verifier cannot revoke another client's valid credentials.
        self.assertEqual(self.exchange(code, code_verifier="x" * 43).status_code, 400)
        self.assertIsNotNone(self.verify(tokens["access_token"]))
        self.assertEqual(self.exchange(code).status_code, 400)
        self.assertIsNone(self.verify(tokens["access_token"]))
        self.assertIsNotNone(self.verify(separate["access_token"]))

    def test_failed_refresh_rolls_back_rotation(self):
        tokens = self.issue()
        values = {
            "grant_type": "refresh_token",
            "client_id": "test-chatgpt",
            "refresh_token": tokens["refresh_token"],
        }
        with (
            patch(
                "mataroa.oauth.OAuthToken.objects.create",
                side_effect=RuntimeError("synthetic failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.post("/oauth/token/", values)
        self.assertFalse(OAuthToken.objects.get().revoked)
        self.assertIsNotNone(self.verify(tokens["access_token"]))
        self.assertEqual(self.post("/oauth/token/", values).status_code, 200)

    def test_oldest_refresh_replay_revokes_all_descendants(self):
        first = self.issue()
        current = first
        for _ in range(3):
            response = self.post(
                "/oauth/token/",
                {
                    "grant_type": "refresh_token",
                    "client_id": "test-chatgpt",
                    "refresh_token": current["refresh_token"],
                },
            )
            self.assertEqual(response.status_code, 200)
            current = response.json()
        self.assertIsNotNone(self.verify(current["access_token"]))
        for tokens in (first, current):
            response = self.post(
                "/oauth/token/",
                {
                    "grant_type": "refresh_token",
                    "client_id": "test-chatgpt",
                    "refresh_token": tokens["refresh_token"],
                },
            )
            self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.verify(current["access_token"]))

    def test_revocation_is_private_and_accepts_access_or_refresh_tokens(self):
        other = OAuthClient.objects.create(
            client_id="other",
            name="Other",
            client_type="public",
            redirect_uris=REDIRECT,
        )
        for kind in ("access_token", "refresh_token"):
            tokens = self.issue()
            with override_settings(
                MATAROA_CHATGPT_CLIENT_IDS=("test-chatgpt", "other")
            ):
                for value in ("unknown", tokens[kind]):
                    response = self.post(
                        "/oauth/revoke/", {"client_id": other.client_id, "token": value}
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {})
                self.assertIsNotNone(self.verify(tokens["access_token"]))
                response = self.post(
                    "/oauth/revoke/",
                    {
                        "client_id": "test-chatgpt",
                        "token": tokens[kind],
                        "token_type_hint": "unrecognized-hint",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(self.verify(tokens["access_token"]))

    def test_token_and_revocation_reject_query_json_and_duplicate_credentials(self):
        tokens = self.issue()
        values = {
            "client_id": "test-chatgpt",
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "token": tokens["access_token"],
        }
        for endpoint in ("/oauth/token/", "/oauth/revoke/"):
            self.assertEqual(self.get(endpoint, values).status_code, 405)
            response = self.client.post(
                endpoint,
                values,
                content_type="application/json",
                secure=True,
                HTTP_HOST="mataroa.blog",
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                self.post(
                    endpoint, {**values, "client_id": ["test-chatgpt", "test-chatgpt"]}
                ).status_code,
                400,
            )
            response = self.client.post(
                endpoint + "?" + urlencode(values),
                "",
                content_type="application/x-www-form-urlencoded",
                secure=True,
                HTTP_HOST="mataroa.blog",
            )
            self.assertIn(response.status_code, (400, 401))
        self.assertIsNotNone(self.verify(tokens["access_token"]))

    def test_basic_auth_decodes_form_values_and_rejects_ambiguous_credentials(self):
        from urllib.parse import quote_plus

        self.application.client_id = "client:with+special"
        self.application.client_type = "confidential"
        secret = "special+secret:with percent%and space"
        self.application.set_secret(secret)
        self.application.save()
        self.assertNotEqual(self.application.secret_hash, secret)
        with override_settings(
            MATAROA_CHATGPT_CLIENT_IDS=(self.application.client_id,)
        ):
            code = self.authorize()
            values = {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": VERIFIER,
                "redirect_uri": REDIRECT,
                "resource": RESOURCE,
            }
            encoded = base64.b64encode(
                f"{quote_plus(self.application.client_id)}:{quote_plus(secret)}".encode()
            ).decode()
            for authorization, extra in [
                ("Basic ???", {}),
                ("Bearer fake", {}),
                (f"Basic {encoded}", {"client_secret": secret}),
                (f"Basic {encoded}", {"client_id": "other"}),
            ]:
                response = self.client.post(
                    "/oauth/token/",
                    urlencode({**values, **extra}),
                    content_type="application/x-www-form-urlencoded",
                    secure=True,
                    HTTP_HOST="mataroa.blog",
                    HTTP_AUTHORIZATION=authorization,
                )
                self.assertEqual(response.status_code, 401)
            response = self.client.post(
                "/oauth/token/",
                urlencode({**values, "client_id": self.application.client_id}),
                content_type="application/x-www-form-urlencoded",
                secure=True,
                HTTP_HOST="mataroa.blog",
                HTTP_AUTHORIZATION=f"Basic {encoded}",
            )
            self.assertEqual(response.status_code, 200, response.content)

    def test_public_client_cannot_silently_accept_secret_authentication(self):
        code = self.authorize()
        self.assertEqual(self.exchange(code, client_secret="").status_code, 401)
        self.assertEqual(self.exchange(code, client_secret="invented").status_code, 401)
        self.assertEqual(self.exchange(code).status_code, 200)

    def test_default_scope_and_consent_hidden_fields_match_permissions(self):
        from html.parser import HTMLParser

        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "input" and "name" in attrs:
                    self.values[attrs["name"]] = attrs.get("value", "")

        values = self.auth_parameters()
        del values["scope"]
        response = self.get("/oauth/authorize/", values)
        parser = Inputs()
        parser.feed(response.content.decode())
        self.assertEqual(parser.values["scope"], "blog:read")
        self.assertNotContains(response, "Publish or schedule")
        approved = self.post("/oauth/authorize/", {**parser.values, "allow": "true"})
        code = parse_qs(urlsplit(approved.url).query)["code"][0]
        self.assertEqual(self.exchange(code).json()["scope"], "blog:read")

    def test_security_headers_and_untrusted_redirects(self):
        response = self.get("/oauth/authorize/", self.auth_parameters())
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        for uri in (
            "http://example.org/",
            "https://user:secret@example.org/",
            "https://example.org/#fragment",
            "https://*.example.org/",
            "https://example.org:bad/",
            "javascript:alert(1)",
        ):
            self.application.redirect_uris = uri
            self.application.save()
            self.assertEqual(
                self.get(
                    "/oauth/authorize/", self.auth_parameters(redirect_uri=uri)
                ).status_code,
                400,
            )

    def test_operator_secret_form_hashes_secrets_and_preserves_existing_hash(self):
        from main.admin import OAuthClientForm

        values = {
            "name": "Confidential",
            "client_id": "confidential",
            "client_type": "confidential",
            "redirect_uris": REDIRECT,
        }
        self.assertFalse(OAuthClientForm(data=values).is_valid())
        secret = "disposable-operator-secret-" + "x" * 32
        form = OAuthClientForm(data={**values, "new_secret": secret})
        self.assertTrue(form.is_valid(), form.errors)
        client = form.save()
        self.assertNotEqual(client.secret_hash, secret)
        original = client.secret_hash
        form = OAuthClientForm(data=values, instance=client)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().secret_hash, original)

    def test_cleanup_keeps_replay_history_until_whole_family_expires(self):
        from io import StringIO

        from django.core.management import call_command

        first = self.issue()
        old = OAuthToken.objects.get()
        current = self.post(
            "/oauth/token/",
            {
                "grant_type": "refresh_token",
                "client_id": "test-chatgpt",
                "refresh_token": first["refresh_token"],
            },
        ).json()
        OAuthGrant.objects.update(code_expires=timezone.now() - timedelta(days=1))
        OAuthToken.objects.filter(pk=old.pk).update(
            refresh_expires=timezone.now() - timedelta(seconds=1)
        )
        call_command("clearoauth", stdout=StringIO())
        self.assertEqual(OAuthToken.objects.count(), 2)
        self.assertIsNotNone(self.verify(current["access_token"]))
        OAuthToken.objects.update(refresh_expires=timezone.now() - timedelta(seconds=1))
        call_command("clearoauth", stdout=StringIO())
        self.assertEqual(OAuthGrant.objects.count(), 0)
        self.assertEqual(OAuthToken.objects.count(), 0)


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
        self.assertTrue(OAuthGrant.objects.get().revoked)

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
        self.assertIsNone(
            self.verify(success["access_token"])
        )  # Code reuse revokes its family.
        self.assertEqual(OAuthToken.objects.count(), 1)
        self.assertEqual(OAuthToken.objects.filter(revoked=False).count(), 1)
        self.assertEqual(OAuthGrant.objects.filter(consumed=False).count(), 0)

    def test_old_refresh_replay_racing_with_new_generation_revokes_every_token(self):
        first = self.issue()
        current = self.post(
            "/oauth/token/",
            {
                "client_id": "test-chatgpt",
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
            },
        ).json()
        outcomes = self.race_requests(
            [
                (
                    "/oauth/token/",
                    {
                        "client_id": "test-chatgpt",
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                    },
                )
                for tokens in (first, current)
            ]
        )
        self.assertEqual(outcomes[0][0], 400)
        self.assertIn(outcomes[1][0], (200, 400))
        self.assertTrue(OAuthGrant.objects.get().revoked)
        self.assertIsNone(self.verify(current["access_token"]))
        for status, body in outcomes:
            if status == 200:
                self.assertIsNone(self.verify(body["access_token"]))

    def test_revocation_racing_with_refresh_cannot_leave_live_descendants(self):
        tokens = self.issue()
        outcomes = self.race_requests(
            [
                (
                    "/oauth/revoke/",
                    {"client_id": "test-chatgpt", "token": tokens["access_token"]},
                ),
                (
                    "/oauth/token/",
                    {
                        "client_id": "test-chatgpt",
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                    },
                ),
            ]
        )
        self.assertEqual(outcomes[0][0], 200)
        self.assertIn(outcomes[1][0], (200, 400))
        self.assertTrue(OAuthGrant.objects.get().revoked)
        self.assertIsNone(self.verify(tokens["access_token"]))
        if outcomes[1][0] == 200:
            self.assertIsNone(self.verify(outcomes[1][1]["access_token"]))

    def race_requests(self, requests):
        barrier = Barrier(len(requests))

        def request(item):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self.post(*item, client=Client())
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            futures = [pool.submit(request, item) for item in requests]
            return [future.result(timeout=20) for future in futures]
