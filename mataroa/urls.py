"""mataroa URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("dja/", admin.site.urls),
    path("", include("main.urls")),
]

if settings.MATAROA_CHATGPT_ENABLED:
    from main.views.mcp import endpoint

    from .oauth import MataroaResourceMetadataView, MataroaServerMetadataView

    urlpatterns = [
        path("mcp", endpoint, name="mcp"),
        path("oauth/", include("mataroa.oauth_urls")),
        path(
            ".well-known/oauth-authorization-server",
            MataroaServerMetadataView.as_view(),
            name="mcp-oauth-server-metadata",
        ),
        path(
            ".well-known/oauth-protected-resource/mcp",
            MataroaResourceMetadataView.as_view(),
            name="mcp-oauth-resource-metadata",
        ),
        path(
            ".well-known/oauth-protected-resource",
            MataroaResourceMetadataView.as_view(),
            name="mcp-oauth-resource-metadata-root",
        ),
    ] + urlpatterns
