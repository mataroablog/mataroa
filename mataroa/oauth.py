"""Mataroa's fixed OAuth flow: consent, S256 code exchange, refresh and revoke.

Only pre-registered clients and the canonical MCP resource are supported.
Tokens and authorization codes are random opaque values, stored as SHA-256 hashes.
"""

import base64
import binascii
import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.debug import SafeExceptionReporterFilter
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters

from main.models import OAuthClient, OAuthGrant, OAuthToken

SCOPES = {
    "blog:read": "Read your posts, drafts, pages, and blog comments",
    "drafts:write": "Create and edit unpublished drafts",
    "posts:publish": "Publish or schedule approved drafts",
    "posts:delete": "Permanently delete posts, their comments and page-view records",
}
CLIENT_AUTH_METHODS = ["client_secret_basic", "client_secret_post", "none"]


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def client_is_allowed(client):
    return bool(
        client
        and client.client_id in settings.MATAROA_CHATGPT_CLIENT_IDS
        and client.client_type in {"public", "confidential"}
        and client.allowed_redirects()
    )


def valid_scope(scope):
    values = scope.split(" ")
    return "blog:read" in values and set(values).issubset(SCOPES)


def error_response(error, status=400):
    response = JsonResponse({"error": error}, status=status)
    if status == 401:
        response["WWW-Authenticate"] = 'Basic realm="Mataroa OAuth"'
    return response


def parameters(request):
    values = request.POST if request.method == "POST" else request.GET
    if any(len(items) != 1 or len(items[0]) > 4096 for _, items in values.lists()):
        return None
    return values


def authenticate_client(request, values):
    """Exactly one authentication method; Basic credentials use form decoding."""
    client_id, secret = values.get("client_id", ""), values.get("client_secret", "")
    authorization = request.headers.get("Authorization")
    if authorization:
        if "client_secret" in values:
            return None
        try:
            scheme, credentials = authorization.split(" ", 1)
            if scheme.lower() != "basic":
                return None
            client_id, secret = (
                base64.b64decode(credentials, validate=True).decode().split(":", 1)
            )
            client_id, secret = unquote_plus(client_id), unquote_plus(secret)
            if "client_id" in values and values["client_id"] != client_id:
                return None
        except (ValueError, UnicodeError, binascii.Error):
            return None
    if client_id not in settings.MATAROA_CHATGPT_CLIENT_IDS:
        return None
    client = OAuthClient.objects.filter(client_id=client_id).first()
    if not client_is_allowed(client):
        return None
    if client.client_type == "confidential":
        return client if secret and check_password(secret, client.secret_hash) else None
    return client if not authorization and "client_secret" not in values else None


def authorization_redirect(values, **result):
    parts = urlsplit(values["redirect_uri"])
    query = urlencode(
        {**result, "state": values["state"], "iss": settings.MATAROA_MCP_ISSUER_URL}
    )
    return HttpResponseRedirect(
        urlunsplit(
            parts._replace(query=parts.query + "&" + query if parts.query else query)
        )
    )


def issue_tokens(grant, scope):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = timezone.now()
    OAuthToken.objects.create(
        grant=grant,
        scope=scope,
        access_hash=token_hash(access),
        refresh_hash=token_hash(refresh),
        access_expires=now + timedelta(hours=1),
        refresh_expires=now + timedelta(days=30),
    )
    return JsonResponse(
        {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": scope,
        }
    )


class OAuthExceptionReporterFilter(SafeExceptionReporterFilter):
    """Never expose OAuth POST bodies or stack-frame locals in error reports."""

    def is_active(self, request):
        return True

    def get_post_parameters(self, request):
        if request is None:
            return {}
        return {key: self.cleansed_substitute for key in request.POST}

    def get_traceback_frame_variables(self, request, tb_frame):
        return [(key, self.cleansed_substitute) for key in tb_frame.f_locals]

    def get_safe_request_meta(self, request):
        values = super().get_safe_request_meta(request)
        for key in ("QUERY_STRING", "RAW_URI", "REQUEST_URI"):
            if key in values:
                values[key] = self.cleansed_substitute
        return values

    def get_safe_cookies(self, request):
        return {
            key: self.cleansed_substitute for key in getattr(request, "COOKIES", {})
        }


@method_decorator(sensitive_post_parameters(), name="dispatch")
class OAuthView(View):
    http_method_names = ["get", "post", "head", "options"]
    referrer_policy = "no-referrer"

    def dispatch(self, request, *args, **kwargs):
        request.exception_reporter_filter = OAuthExceptionReporterFilter()
        issuer = urlsplit(settings.MATAROA_MCP_ISSUER_URL)
        if (
            request.get_host() != issuer.netloc
            or request.scheme != issuer.scheme
            or request.method == "POST"
            and request.content_type != "application/x-www-form-urlencoded"
        ):
            response = error_response("invalid_request")
        else:
            response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        response["Referrer-Policy"] = self.referrer_policy
        return response


class MataroaAuthorizationView(OAuthView):
    # HTTPS consent POSTs need same-origin headers for Django's CSRF checks.
    referrer_policy = "same-origin"

    def get(self, request):
        values = parameters(request)
        if values is None:
            return error_response("invalid_request")
        if values.get("resource") != settings.MATAROA_MCP_RESOURCE_URL:
            return error_response("invalid_target")
        client = OAuthClient.objects.filter(
            client_id=values.get("client_id", "")
        ).first()
        if not client_is_allowed(client):
            return error_response("invalid_client")
        if (
            values.get("redirect_uri") not in client.allowed_redirects()
            or values.get("response_type") != "code"
            or values.get("response_mode", "query") != "query"
            or values.get("code_challenge_method") != "S256"
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", values.get("code_challenge", ""))
            or not values.get("state")
            or values.get("approval_prompt", "force") != "force"
        ):
            return error_response("invalid_request")
        scope = values.get("scope", "blog:read")
        if not valid_scope(scope):
            return authorization_redirect(values, error="invalid_scope")
        scope = " ".join(dict.fromkeys(scope.split()))
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_active:
            return error_response("access_denied")
        if request.method == "POST":
            if values.get("allow") != "true":
                return authorization_redirect(values, error="access_denied")
            code = secrets.token_urlsafe(32)
            OAuthGrant.objects.create(
                client=client,
                user=request.user,
                scope=scope,
                resource=values["resource"],
                redirect_uri=values["redirect_uri"],
                code_hash=token_hash(code),
                code_challenge=values["code_challenge"],
                code_expires=timezone.now() + timedelta(minutes=2),
            )
            return authorization_redirect(values, code=code)
        fields = {
            name: values[name]
            for name in (
                "client_id",
                "redirect_uri",
                "response_type",
                "code_challenge",
                "code_challenge_method",
                "state",
                "resource",
            )
        }
        fields["scope"] = scope
        return render(
            request,
            "main/oauth_authorize.html",
            {
                "application": client,
                "fields": fields,
                "permissions": [SCOPES[name] for name in dict.fromkeys(scope.split())],
            },
        )

    post = get


@method_decorator(csrf_exempt, name="dispatch")
class MataroaTokenView(OAuthView):
    def post(self, request):
        if "resource" in request.POST and request.POST.getlist("resource") != [
            settings.MATAROA_MCP_RESOURCE_URL
        ]:
            return error_response("invalid_target")
        values = parameters(request)
        if values is None:
            return error_response("invalid_request")
        client = authenticate_client(request, values)
        if client is None:
            return error_response("invalid_client", 401)
        grant_type = values.get("grant_type")
        if grant_type not in {"authorization_code", "refresh_token"}:
            return error_response("unsupported_grant_type")
        resource = values.get("resource")
        if (resource is not None and resource != settings.MATAROA_MCP_RESOURCE_URL) or (
            grant_type == "authorization_code" and resource is None
        ):
            return error_response("invalid_target")
        if grant_type == "authorization_code":
            return self.exchange_code(client, values)
        return self.refresh(client, values)

    @staticmethod
    @transaction.atomic
    def exchange_code(client, values):
        verifier = values.get("code_verifier", "")
        if not values.get("redirect_uri") or not re.fullmatch(
            r"[A-Za-z0-9._~-]{43,128}", verifier
        ):
            return error_response("invalid_request")
        grant = (
            OAuthGrant.objects.select_for_update()
            .filter(
                code_hash=token_hash(values.get("code", "")),
                client=client,
            )
            .first()
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            grant is None
            or grant.revoked
            or not grant.user.is_active
            or grant.redirect_uri != values["redirect_uri"]
            or grant.resource != settings.MATAROA_MCP_RESOURCE_URL
            or not valid_scope(grant.scope)
            or not secrets.compare_digest(grant.code_challenge, challenge)
        ):
            return error_response("invalid_grant")
        if grant.consumed:
            # RFC 6749: reject reuse and revoke credentials issued from this code.
            grant.revoked = True
            grant.save(update_fields=["revoked"])
            return error_response("invalid_grant")
        if grant.code_expires <= timezone.now():
            return error_response("invalid_grant")
        grant.consumed = True
        grant.save(update_fields=["consumed"])
        return issue_tokens(grant, grant.scope)

    @staticmethod
    @transaction.atomic
    def refresh(client, values):
        token = OAuthToken.objects.filter(
            refresh_hash=token_hash(values.get("refresh_token", "")),
            grant__client=client,
        ).first()
        if token is None:
            return error_response("invalid_grant")
        # All generations lock the same grant, including revocation/replay of an
        # older token racing with rotation of its newest descendant.
        grant = OAuthGrant.objects.select_for_update().filter(pk=token.grant_id).first()
        token = OAuthToken.objects.filter(pk=token.pk).first()
        if (
            grant is None
            or token is None
            or grant.revoked
            or not grant.consumed
            or not grant.user.is_active
            or grant.resource != settings.MATAROA_MCP_RESOURCE_URL
            or not valid_scope(token.scope)
            or not set(token.scope.split()).issubset(grant.scope.split())
        ):
            return error_response("invalid_grant")
        if token.revoked:
            grant.revoked = True
            grant.save(update_fields=["revoked"])
            return error_response("invalid_grant")
        if token.refresh_expires <= timezone.now():
            return error_response("invalid_grant")
        scope = values.get("scope", token.scope)
        if not valid_scope(scope) or not set(scope.split()).issubset(
            token.scope.split()
        ):
            return error_response("invalid_scope")
        token.revoked = True
        token.save(update_fields=["revoked"])
        return issue_tokens(grant, " ".join(dict.fromkeys(scope.split())))


@method_decorator(csrf_exempt, name="dispatch")
class MataroaRevokeTokenView(OAuthView):
    @transaction.atomic
    def post(self, request):
        from django.db.models import Q

        values = parameters(request)
        if values is None or not values.get("token"):
            return error_response("invalid_request")
        client = authenticate_client(request, values)
        if client is None:
            return error_response("invalid_client", 401)
        checksum = token_hash(values["token"])
        token = OAuthToken.objects.filter(
            Q(access_hash=checksum) | Q(refresh_hash=checksum), grant__client=client
        ).first()
        if token:
            OAuthGrant.objects.filter(pk=token.grant_id).update(revoked=True)
        return JsonResponse({})


class MataroaServerMetadataView(OAuthView):
    def get(self, request):
        issuer = settings.MATAROA_MCP_ISSUER_URL
        return JsonResponse(
            {
                "issuer": issuer,
                "authorization_endpoint": issuer + "/oauth/authorize/",
                "token_endpoint": issuer + "/oauth/token/",
                "revocation_endpoint": issuer + "/oauth/revoke/",
                "response_types_supported": ["code"],
                "response_modes_supported": ["query"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": list(SCOPES),
                "token_endpoint_auth_methods_supported": CLIENT_AUTH_METHODS,
                "revocation_endpoint_auth_methods_supported": CLIENT_AUTH_METHODS,
                "authorization_response_iss_parameter_supported": True,
                "client_id_metadata_document_supported": False,
            }
        )


class MataroaResourceMetadataView(OAuthView):
    def get(self, request):
        return JsonResponse(
            {
                "resource": settings.MATAROA_MCP_RESOURCE_URL,
                "authorization_servers": [settings.MATAROA_MCP_ISSUER_URL],
                "scopes_supported": list(SCOPES),
                "bearer_methods_supported": ["header"],
                "resource_name": "Mataroa ChatGPT plugin",
            }
        )


def verify_access_token(token):
    """Return the valid token and its owner; no SDK or process-wide auth context."""
    if not token or len(token) > 4096:
        return None
    access = (
        OAuthToken.objects.select_related("grant__client", "grant__user")
        .filter(access_hash=token_hash(token))
        .first()
    )
    if access is None:
        return None
    grant = access.grant
    if (
        access.revoked
        or grant.revoked
        or not grant.consumed
        or access.access_expires <= timezone.now()
        or not grant.user.is_active
        or not client_is_allowed(grant.client)
        or grant.resource != settings.MATAROA_MCP_RESOURCE_URL
        or not valid_scope(access.scope)
        or not set(access.scope.split()).issubset(grant.scope.split())
    ):
        return None
    return access
