"""Intentionally exclude registration, introspection, device and OIDC endpoints."""

from django.urls import path

from .oauth import MataroaAuthorizationView, MataroaRevokeTokenView, MataroaTokenView

app_name = "oauth"
urlpatterns = [
    path("authorize/", MataroaAuthorizationView.as_view(), name="authorize"),
    path("token/", MataroaTokenView.as_view(), name="token"),
    path("revoke/", MataroaRevokeTokenView.as_view(), name="revoke-token"),
]
