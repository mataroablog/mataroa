"""Verify opaque, first-party Django OAuth Toolkit tokens for the MCP server."""

import hashlib

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from mcp.server.auth.provider import AccessToken


class DjangoTokenVerifier:
    """Each request rechecks the database; revocation is effective immediately.

    No API key fallback, external introspection, token passthrough or shared user.
    The authenticated subject is the token's real Django user primary key.
    """

    def __init__(self, resource_url: str | None = None):
        self.resource_url = resource_url or settings.MATAROA_MCP_RESOURCE_URL

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or len(token) > 4096:
            return None
        return await sync_to_async(self._verify_token, thread_sensitive=True)(token)

    def _verify_token(self, token: str) -> AccessToken | None:
        # Delayed imports keep Django app loading out of module import time.
        from mataroa.oauth import client_is_allowed
        from oauth2_provider.models import get_access_token_model

        checksum = hashlib.sha256(token.encode("utf-8")).hexdigest()
        access = (
            get_access_token_model()
            .objects.select_related("application", "user")
            .filter(token_checksum=checksum)
            .first()
        )
        if (
            access is None
            or not access.is_valid(["blog:read"])
            or access.resource != [self.resource_url]
            or self.resource_url != settings.MATAROA_MCP_RESOURCE_URL
            or access.user is None
            or not access.user.is_active
            or not client_is_allowed(access.application)
        ):
            return None
        scopes = access.scope.split()
        if not set(scopes).issubset({"blog:read", "drafts:write", "posts:publish"}):
            return None
        expires = access.expires
        if timezone.is_naive(expires):
            expires = timezone.make_aware(expires, timezone.get_default_timezone())
        return AccessToken(
            token=token,
            client_id=access.application.client_id,
            scopes=scopes,
            expires_at=int(expires.timestamp()),
            resource=self.resource_url,
            subject=str(access.user_id),
            claims={"iss": settings.MATAROA_MCP_ISSUER_URL},
        )
