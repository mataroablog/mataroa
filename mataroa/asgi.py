"""Serve Django and, when enabled, the authenticated ChatGPT MCP endpoint."""

import os
from urllib.parse import urlsplit

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mataroa.settings")

django_application = get_asgi_application()

from django.conf import settings  # noqa: E402

if settings.MATAROA_CHATGPT_ENABLED:
    from asgiref.sync import sync_to_async
    from django.db import close_old_connections
    from mataroa_chatgpt.server import create_server
    from mcp.server.transport_security import TransportSecuritySettings

    resource = urlsplit(settings.MATAROA_MCP_RESOURCE_URL)
    server = create_server(
        issuer_url=settings.MATAROA_MCP_ISSUER_URL,
        resource_url=settings.MATAROA_MCP_RESOURCE_URL,
    )
    mcp_application = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=2 * 1024 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[resource.netloc],
            allowed_origins=[
                f"{resource.scheme}://{resource.netloc}",
                "https://chatgpt.com",
            ],
        ),
    )

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            await mcp_application(scope, receive, send)
        elif scope.get("path") == "/mcp":
            # This route bypasses Django's ASGI request signals. Clean up on the
            # same thread as verifier/backend ORM work, including failed auth.
            await sync_to_async(close_old_connections, thread_sensitive=True)()
            try:
                await mcp_application(scope, receive, send)
            finally:
                await sync_to_async(close_old_connections, thread_sensitive=True)()
        else:
            await django_application(scope, receive, send)
else:
    application = django_application
