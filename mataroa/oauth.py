"""OAuth security boundary for the optional, first-party ChatGPT integration.

Django OAuth Toolkit handles protocol parsing, consent, PKCE, rotation and
revocation. This module deliberately narrows it to pre-registered clients and
one resource; it never accepts Mataroa API keys or fetches client metadata URLs.
"""

import hashlib
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import router, transaction
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.debug import SafeExceptionReporterFilter
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from oauth2_provider.models import (
    get_application_model,
    get_grant_model,
    get_refresh_token_model,
)
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauth2_provider.views import (
    AuthorizationView,
    OAuthProtectedResourceMetadataView,
    OAuthServerMetadataView,
    RevokeTokenView,
    TokenView,
)
from oauthlib.oauth2.rfc6749 import errors


def client_is_allowed(application):
    """Fail closed until the operator explicitly enables a registered client."""
    if application is None:
        return False
    redirects = application.redirect_uris.split()
    return (
        application.client_id in settings.MATAROA_CHATGPT_CLIENT_IDS
        and application.authorization_grant_type == application.GRANT_AUTHORIZATION_CODE
        and not application.skip_authorization
        and bool(redirects)
        and all(
            urlsplit(uri).scheme == "https"
            and bool(urlsplit(uri).hostname)
            and not urlsplit(uri).username
            and not urlsplit(uri).password
            and not urlsplit(uri).fragment
            and "*" not in uri
            for uri in redirects
        )
    )


def exact_resource_validator(request_uri, audiences):
    return audiences == [settings.MATAROA_MCP_RESOURCE_URL] and (
        request_uri == settings.MATAROA_MCP_RESOURCE_URL
    )


def _invalid_target(request):
    raise errors.CustomOAuth2Error(
        error="invalid_target",
        description="The resource must be the canonical Mataroa MCP endpoint.",
        request=request,
    )


class MataroaOAuth2Validator(OAuth2Validator):
    """Require exact client, redirect, resource, user and grant bindings."""

    def _load_application(self, client_id, request):
        # Check before the toolkit lookup: no CIMD fetch, even if an operator
        # accidentally enables CIMD later.
        if client_id not in settings.MATAROA_CHATGPT_CLIENT_IDS:
            return None
        application = super()._load_application(client_id, request)
        return application if client_is_allowed(application) else None

    def validate_redirect_uri(self, client_id, redirect_uri, request, *args, **kwargs):
        return client_is_allowed(request.client) and (
            redirect_uri in request.client.redirect_uris.split()
        )

    def confirm_redirect_uri(
        self, client_id, code, redirect_uri, client, *args, **kwargs
    ):
        grant = get_grant_model().objects.filter(code=code, application=client).first()
        return bool(grant and redirect_uri == grant.redirect_uri)

    def validate_response_type(
        self, client_id, response_type, client, request, *args, **kwargs
    ):
        return response_type == "code" and client_is_allowed(client)

    def validate_grant_type(
        self, client_id, grant_type, client, request, *args, **kwargs
    ):
        return grant_type in {
            "authorization_code",
            "refresh_token",
        } and client_is_allowed(client)

    def is_pkce_required(self, client_id, request):
        return True

    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        return "blog:read" in scopes and super().validate_scopes(
            client_id, scopes, client, request, *args, **kwargs
        )

    def _validate_resource_uris(self, request, resources):
        super()._validate_resource_uris(request, resources)
        if resources and resources != [settings.MATAROA_MCP_RESOURCE_URL]:
            _invalid_target(request)

    def _create_authorization_code(self, request, code, expires=None):
        if getattr(request, "resource", None) != [settings.MATAROA_MCP_RESOURCE_URL]:
            _invalid_target(request)
        if request.code_challenge_method != "S256" or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", request.code_challenge or ""
        ):
            raise errors.InvalidRequestError(
                description="S256 PKCE is required.", request=request
            )
        if not request.user or not request.user.is_active:
            raise errors.AccessDeniedError(request=request)
        return super()._create_authorization_code(request, code, expires)

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        if not super().validate_code(client_id, code, client, request, *args, **kwargs):
            return False
        grant = get_grant_model().objects.get(code=code, application=client)
        return bool(
            grant.resource == [settings.MATAROA_MCP_RESOURCE_URL]
            and grant.code_challenge_method == "S256"
            and request.user
            and request.user.is_active
        )

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        if not super().validate_refresh_token(
            refresh_token, client, request, *args, **kwargs
        ):
            return False
        return bool(
            request.refresh_token_instance.resource
            == [settings.MATAROA_MCP_RESOURCE_URL]
            and request.user
            and request.user.is_active
        )

    def _check_and_set_request_resource(self, request):
        # MCP clients must send resource on the code exchange. Refresh may omit
        # it, in which case Toolkit inherits the existing, already-bound resource.
        if request.grant_type == "authorization_code" and not getattr(
            request, "resource", None
        ):
            _invalid_target(request)
        super()._check_and_set_request_resource(request)
        if request.resource != [settings.MATAROA_MCP_RESOURCE_URL]:
            _invalid_target(request)


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


class CanonicalOAuthEndpointMixin:
    def dispatch(self, request, *args, **kwargs):
        request.exception_reporter_filter = OAuthExceptionReporterFilter()
        issuer = urlsplit(settings.MATAROA_MCP_ISSUER_URL)
        if request.get_host() != issuer.netloc or request.scheme != issuer.scheme:
            return JsonResponse(
                {
                    "error": "invalid_request",
                    "error_description": "Use the canonical HTTPS host.",
                },
                status=400,
            )
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class MataroaAuthorizationView(CanonicalOAuthEndpointMixin, AuthorizationView):
    def dispatch(self, request, *args, **kwargs):
        parameters = request.POST if request.method == "POST" else request.GET
        if parameters.getlist("resource") != [settings.MATAROA_MCP_RESOURCE_URL]:
            return JsonResponse({"error": "invalid_target"}, status=400)
        if any(len(values) != 1 for _, values in parameters.lists()):
            return JsonResponse({"error": "invalid_request"}, status=400)
        if parameters.get("approval_prompt", "force") != "force":
            return JsonResponse(
                {
                    "error": "invalid_request",
                    "error_description": "Explicit consent is required.",
                },
                status=400,
            )
        client_id = parameters.get("client_id")
        if (
            client_id not in settings.MATAROA_CHATGPT_CLIENT_IDS
            or not client_is_allowed(
                get_application_model().objects.filter(client_id=client_id).first()
            )
        ):
            return JsonResponse({"error": "invalid_client"}, status=400)
        if (
            parameters.get("response_type") != "code"
            or parameters.get("code_challenge_method") != "S256"
            or not re.fullmatch(
                r"[A-Za-z0-9_-]{43}", parameters.get("code_challenge", "")
            )
            or not parameters.get("redirect_uri")
            or not parameters.get("state")
        ):
            return JsonResponse(
                {
                    "error": "invalid_request",
                    "error_description": "Code flow, state, exact redirect_uri and S256 PKCE are required.",
                },
                status=400,
            )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # An administrator may remove an application after the consent form was
        # rendered, or even between dispatch validation and Toolkit's lookup.
        try:
            return super().form_valid(form)
        except get_application_model().DoesNotExist:
            return JsonResponse({"error": "invalid_client"}, status=400)


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(sensitive_post_parameters(), name="dispatch")
class MataroaTokenView(CanonicalOAuthEndpointMixin, TokenView):
    def authorization_flow_token_response(self, request, *args, **kwargs):
        try:
            return super().authorization_flow_token_response(request, *args, **kwargs)
        finally:
            # Toolkit's narrower inner decorator otherwise overwrites our outer
            # marker, exposing code/verifier/refresh tokens to mail_admins.
            request.sensitive_post_parameters = "__ALL__"

    def post(self, request, *args, **kwargs):
        if request.POST.get("grant_type") not in {
            "authorization_code",
            "refresh_token",
        }:
            return JsonResponse({"error": "unsupported_grant_type"}, status=400)
        parameters = request.POST
        if any(
            len(values) != 1 for key, values in parameters.lists() if key != "resource"
        ):
            return JsonResponse({"error": "invalid_request"}, status=400)
        if (
            "resource" in parameters
            and parameters.getlist("resource") != [settings.MATAROA_MCP_RESOURCE_URL]
        ) or (
            parameters.get("grant_type") == "authorization_code"
            and "resource" not in parameters
        ):
            return JsonResponse({"error": "invalid_target"}, status=400)
        if parameters.get("grant_type") == "authorization_code" and (
            not parameters.get("redirect_uri")
            or not re.fullmatch(
                r"[A-Za-z0-9._~-]{43,128}", parameters.get("code_verifier", "")
            )
        ):
            return JsonResponse({"error": "invalid_request"}, status=400)
        if parameters.get("grant_type") == "authorization_code":
            # oauthlib saves tokens before consuming the authorization code.
            # Hold the grant lock through both operations, and roll back any
            # tokens if consuming the code fails. PostgreSQL serializes races.
            grant_model = get_grant_model()
            database = router.db_for_write(grant_model)
            with transaction.atomic(using=database):
                grant_model.objects.using(database).select_for_update().filter(
                    code=parameters.get("code", "")
                ).first()
                response = self.authorization_flow_token_response(
                    request, *args, **kwargs
                )
                if response.status_code != 200:
                    transaction.set_rollback(True, using=database)
        else:
            # Serialize refresh validation and rotation, including revoked-token
            # checks. Otherwise two requests can both validate a live token and
            # race into Toolkit's hashed-token response reconstruction.
            refresh_model = get_refresh_token_model()
            database = router.db_for_write(refresh_model)
            checksum = hashlib.sha256(
                parameters.get("refresh_token", "").encode()
            ).hexdigest()
            with transaction.atomic(using=database):
                refresh_model.objects.using(database).select_for_update().filter(
                    token_checksum=checksum
                ).first()
                response = self.authorization_flow_token_response(
                    request, *args, **kwargs
                )
                # Deliberately commit expected 400 errors: replay detection must
                # retain its revocation of the compromised token family.
        # Toolkit's backend catches errors raised by resource validation during
        # token saving, but does not add JSON headers to that exception path.
        response["Content-Type"] = "application/json"
        response["Pragma"] = "no-cache"
        return response


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(sensitive_post_parameters(), name="dispatch")
class MataroaRevokeTokenView(CanonicalOAuthEndpointMixin, RevokeTokenView):
    pass


class MataroaServerMetadataView(CanonicalOAuthEndpointMixin, OAuthServerMetadataView):
    pass


class MataroaResourceMetadataView(
    CanonicalOAuthEndpointMixin, OAuthProtectedResourceMetadataView
):
    pass
