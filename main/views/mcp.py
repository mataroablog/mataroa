"""Stateless MCP Streamable HTTP with JSON responses, served by Django/WSGI.

No sessions, SSE streams, server requests, or background tasks. Each POST is
independently authenticated; notifications never dispatch tools or mutate data.
"""

import base64
import binascii
import json
import logging
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from main.mcp.backend import MataroaError
from main.mcp.server import (
    APP_MIME_TYPE,
    INSTRUCTIONS,
    LIBRARY_URI,
    READ,
    TOOL_BY_NAME,
    ToolService,
    _status,
    icons,
    library_resource,
    tool_catalog,
)
from mataroa.oauth import verify_access_token

logger = logging.getLogger(__name__)
LEGACY_VERSIONS = ("2025-03-26", "2025-06-18", "2025-11-25")
MODERN_VERSION = "2026-07-28"
VERSIONS = (*LEGACY_VERSIONS, MODERN_VERSION)
META_PREFIX = "io.modelcontextprotocol/"
CAPABILITIES = {
    "tools": {},
    "resources": {},
    "extensions": {"io.modelcontextprotocol/ui": {}, "openai/extensions": {}},
}
SERVER_INFO = {
    "name": "mataroa",
    "title": "Mataroa",
    "version": "0.1.0",
    "websiteUrl": "https://mataroa.blog",
}
MAX_BODY = 2 * 1024 * 1024
RESOURCE = {
    "uri": LIBRARY_URI,
    "name": "mataroa-library",
    "title": "Blog Library",
    "mimeType": APP_MIME_TYPE,
}


class RPCError(Exception):
    def __init__(self, code, message, data=None):
        self.code, self.message, self.data = code, message, data


def rpc_error(code, message, request_id=None, status=200, data=None):
    return JsonResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
                **({"data": data} if data is not None else {}),
            },
        },
        status=status,
    )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("Non-finite JSON number")


def request_version(request, method, params):
    """Check the 2026 per-request envelope as well as the older header format."""
    version = request.headers.get("MCP-Protocol-Version")
    meta = params.get("_meta", {})
    if not isinstance(meta, dict):
        raise RPCError(-32602, "Metadata must be an object.")
    modern = version == MODERN_VERSION or any(
        key.startswith(META_PREFIX) for key in meta
    )
    if not modern:
        if version is not None and version not in LEGACY_VERSIONS:
            raise RPCError(-32600, "Unsupported MCP protocol version.")
        return False
    if META_PREFIX + "protocolVersion" not in meta or not isinstance(
        meta.get(META_PREFIX + "clientCapabilities"), dict
    ):
        raise RPCError(-32602, "Missing or invalid protocol metadata.")
    client = meta.get(META_PREFIX + "clientInfo")
    if client is not None and (
        not isinstance(client, dict)
        or not isinstance(client.get("name"), str)
        or not isinstance(client.get("version"), str)
    ):
        raise RPCError(-32602, "Invalid client information.")
    if (
        version is None
        or version != meta[META_PREFIX + "protocolVersion"]
        or request.headers.get("Mcp-Method") != method
    ):
        raise RPCError(-32020, "Protocol version or method header mismatch.")
    key = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}.get(
        method
    )
    if key and params.get(key) is not None:
        header = request.headers.get("Mcp-Name", "")
        if header.startswith("=?base64?") and header.endswith("?="):
            try:
                header = base64.b64decode(header[9:-2], validate=True).decode("utf-8")
            except (ValueError, UnicodeError, binascii.Error):
                raise RPCError(-32020, "Invalid encoded name header.") from None
        if header != params[key]:
            raise RPCError(-32020, "Name header mismatch.")
    if version != MODERN_VERSION:
        raise RPCError(
            -32022,
            "Unsupported protocol version.",
            {"supported": list(VERSIONS), "requested": version},
        )
    return True


def dispatch(method, params, service, modern=False):
    if (
        modern
        and method in {"initialize", "ping"}
        or not modern
        and method == "server/discover"
    ):
        raise RPCError(-32601, "Method not found for this protocol version.")
    if method == "server/discover":
        return {
            "supportedVersions": list(VERSIONS),
            "capabilities": CAPABILITIES,
            "instructions": INSTRUCTIONS,
        }
    if method == "initialize":
        client = params.get("clientInfo")
        if (
            not isinstance(params.get("protocolVersion"), str)
            or not isinstance(params.get("capabilities"), dict)
            or not isinstance(client, dict)
            or not isinstance(client.get("name"), str)
            or not isinstance(client.get("version"), str)
        ):
            raise RPCError(-32602, "Invalid initialization parameters.")
        requested = params["protocolVersion"]
        return {
            "protocolVersion": requested
            if requested in LEGACY_VERSIONS
            else LEGACY_VERSIONS[-1],
            "capabilities": CAPABILITIES,
            "serverInfo": {**SERVER_INFO, "icons": icons()},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method in {"tools/list", "resources/list", "resources/templates/list"}:
        # The fixed catalog fits in one response; it never issues cursors.
        if params.get("cursor") is not None:
            raise RPCError(-32602, "Unknown cursor.")
        return {
            "tools/list": {"tools": tool_catalog()},
            "resources/list": {"resources": [RESOURCE]},
            "resources/templates/list": {
                "resourceTemplates": [
                    {
                        "uriTemplate": "mataroa://posts/{slug}",
                        "name": "post_resource",
                        "mimeType": "text/markdown",
                    }
                ]
            },
        }[method]
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOL_BY_NAME:
            raise RPCError(-32602, "Unknown tool.")
        if "task" in params:
            raise RPCError(-32602, "Background tasks are not supported.")
        try:
            return service.call_tool(name, params.get("arguments", {}))
        except MataroaError as exc:
            return {
                "content": [{"type": "text", "text": f"{exc.code}: {exc}"}],
                "isError": True,
            }
    if method == "resources/read":
        uri = params.get("uri")
        if uri == LIBRARY_URI:
            html, origins = library_resource()
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": APP_MIME_TYPE,
                        "text": html,
                        "_meta": {
                            "ui": {
                                "csp": {
                                    "connectDomains": [],
                                    "resourceDomains": origins,
                                }
                            },
                            "openai/ui": {
                                "preferredDisplayMode": "fullscreen",
                                "availableDisplayModes": ["inline", "fullscreen"],
                            },
                        },
                    }
                ]
            }
        match = (
            re.fullmatch(r"mataroa://posts/([A-Za-z0-9_-]{1,300})", uri)
            if isinstance(uri, str)
            else None
        )
        if not match:
            raise RPCError(-32002, "Resource not found.")
        try:
            post = service.call_tool("get_post", {"slug": match[1]})[
                "structuredContent"
            ]["post"]
        except MataroaError as exc:
            raise RPCError(-32002, str(exc)) from None

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": f"# {post['title']}\n\nStatus: {_status(post)}\n\n{post.get('body') or ''}",
                }
            ]
        }
    raise RPCError(-32601, "Method not found.")


def handle(request):
    issuer = urlsplit(settings.MATAROA_MCP_ISSUER_URL)
    if request.get_host() != issuer.netloc or request.scheme != issuer.scheme:
        return rpc_error(-32600, "Use the canonical HTTPS MCP endpoint.", status=400)
    origin = request.headers.get("Origin")
    if origin is not None and origin not in {
        settings.MATAROA_MCP_ISSUER_URL,
        "https://chatgpt.com",
    }:
        return rpc_error(-32600, "Invalid Origin.", status=403)
    authorization = request.headers.get("Authorization", "")
    bearer = re.fullmatch(
        r"Bearer ([A-Za-z0-9._~+/-]+=*)", authorization, re.IGNORECASE
    )
    access = verify_access_token(bearer[1]) if bearer else None
    if access is None:
        response = JsonResponse({"error": "invalid_token"}, status=401)
        response["WWW-Authenticate"] = (
            f'Bearer resource_metadata="{settings.MATAROA_MCP_ISSUER_URL}/.well-known/oauth-protected-resource/mcp", scope="{READ}"'
        )
        return response
    if request.method != "POST":
        # MCP explicitly permits 405 for GET when SSE is not offered.
        response = HttpResponse(status=405)
        response["Allow"] = "POST"
        return response
    if request.content_type != "application/json":
        return rpc_error(-32600, "Content-Type must be application/json.", status=415)
    if not request.accepts("application/json") or not request.accepts(
        "text/event-stream"
    ):
        return rpc_error(
            -32600,
            "Accept must allow application/json and text/event-stream.",
            status=406,
        )
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return rpc_error(-32600, "Invalid Content-Length.", status=400)
    if length > MAX_BODY:
        return rpc_error(-32600, "Request body too large.", status=413)
    raw = request.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        return rpc_error(-32600, "Request body too large.", status=413)
    try:
        message = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        return rpc_error(-32700, "Invalid JSON.", status=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return rpc_error(-32600, "Expected one JSON-RPC 2.0 message.", status=400)
    has_id = "id" in message
    request_id = message.get("id")
    if has_id and type(request_id) not in (str, int):
        return rpc_error(-32600, "Invalid request ID.", status=400)
    if "method" not in message:
        error = message.get("error")
        is_result = (
            "result" in message
            and "error" not in message
            and isinstance(message["result"], dict)
        )
        is_error = (
            "result" not in message
            and isinstance(error, dict)
            and type(error.get("code")) is int
            and isinstance(error.get("message"), str)
        )
        if (
            has_id
            and (is_result or is_error)
            and request.headers.get("MCP-Protocol-Version") != MODERN_VERSION
        ):
            # We never initiate requests, so there is nothing to correlate.
            return HttpResponse(status=202)
        return rpc_error(-32600, "Invalid response message.", status=400)
    method = message["method"]
    params = message.get("params", {})
    if not isinstance(method, str) or "result" in message or "error" in message:
        return rpc_error(-32600, "Invalid request message.", status=400)
    if not isinstance(params, dict):
        return rpc_error(
            -32602, "Parameters must be an object.", request_id, status=400
        )
    if not has_id:
        if method.startswith("notifications/"):
            return HttpResponse(status=202)
        # Never execute a tools/call disguised as a notification.
        return rpc_error(-32600, "Requests require an ID.", status=400)
    try:
        modern = request_version(request, method, params)
    except RPCError as exc:
        return rpc_error(exc.code, exc.message, request_id, status=400, data=exc.data)
    try:
        service = ToolService(access.grant.user_id, access.scope.split())
        result = dispatch(method, params, service, modern)
        if modern:
            result["resultType"] = "complete"
            result["_meta"] = {
                META_PREFIX + "serverInfo": {**SERVER_INFO, "icons": icons()}
            }
            if method != "tools/call":
                result.update(ttlMs=0, cacheScope="private")
        return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
    except RPCError as exc:
        return rpc_error(
            exc.code,
            exc.message,
            request_id,
            status=404 if modern and exc.code == -32601 else 200,
            data=exc.data,
        )
    except Exception:
        # Do not log exceptions/locals: they can contain tokens or private drafts.
        logger.error(
            "MCP request failed; details suppressed to protect private content."
        )
        return rpc_error(-32603, "Internal error.", request_id)


@csrf_exempt
def endpoint(request):
    # Authentication uses only the bearer token; Django session cookies grant no access.
    try:
        response = handle(request)
    except Exception:
        logger.error(
            "MCP transport failed; details suppressed to protect private content."
        )
        response = rpc_error(-32603, "Internal error.", status=500)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response
