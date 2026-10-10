# ChatGPT plugin

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Development](#development)
- [Frontend](#frontend)
- [Browser tests and preview](#browser-tests-and-preview)
- [Deployment and registration](#deployment-and-registration)
- [Security](#security)
- [Acceptance checks](#acceptance-checks)
- [Sources](#sources)

## Capabilities

- Native Posts sidebar/thread view: search, filter, paginate, and read posts safely.
- Read owned posts, drafts, static pages, and comments; comment email addresses are excluded.
- Save and edit unpublished drafts, with revision checks.
- Publish or schedule an explicitly approved draft, with a separate permission.
- Permanently delete an explicitly approved draft, scheduled post, or published post, including its comments and page-view records, with a separate permission.
- Composer post mentions and resource retrieval where the host supports them (currently ChatGPT desktop).

No unpublish, live-post edits, page writes, moderation, image upload, or account administration tools are exposed.

## Architecture

ChatGPT → ordinary Django view at `/mcp` → verified OAuth token → owner-scoped Django ORM. The endpoint uses stateless MCP Streamable HTTP with JSON responses and runs through the existing Gunicorn/WSGI deployment. No ASGI server or MCP SDK is required.

The integration uses the project's existing dependencies. Implementation paths:

- `main/views/mcp.py`: HTTP endpoint
- `main/mcp/server.py`: tool definitions, argument validation, and UI resource
- `main/mcp/backend.py`: owner-scoped Django ORM operations
- `mataroa/oauth.py`: authorization, token issuance, and verification
- `main/templates/main/mcp_posts.html`: Posts UI template
- `main/static/mcp/`: plain JavaScript and CSS

## Development

From the repository root:

```sh
uv sync
uv run python manage.py check
uv run python chatgpt/web/preview.py
```

## Frontend

The Posts UI is read-only. It needs no frontend build step or third-party JavaScript dependencies. The preview and browser tests run without Node.

The reader uses server-rendered, sanitized Markdown and shows published post links
at the top and bottom. Embedded media remain available on the original blog.

`bridge.js` implements the MCP Apps JSON-RPC host connection: initialization, tool calls,
links, initial-result/cancellation notifications, theme/cursor changes, resizing,
timeouts, and teardown.

`main/mcp/server.py` renders the template with absolute static URLs, including
Django's production filename hashes. The resource CSP permits just the static
asset origin. Data travels through the host; `connectDomains` stays empty.
Classic deferred scripts work inside the host's sandbox without requiring
cross-origin module headers.

## Browser tests and preview

With the preview server running, open `http://127.0.0.1:4173/tests/` to run the checks.
Keep the tab visible while tests run, since browsers can pause animation callbacks
in background tabs. Reload the page after editing existing test files, JavaScript,
or CSS. Restart the preview server and reload after editing the Django template
or adding files; the template and list of served files are loaded at startup.

Open `http://127.0.0.1:4173/preview/` for the fictional posts view in a sandboxed
iframe. Public links are recorded by the simulated host instead of navigating.
Useful preview parameters:

- `?theme=dark` starts with the dark host theme.
- `?mode=empty` shows an empty posts view.
- `?mode=error` returns an initial failure, recoverable with Refresh.
- `?mode=injection` checks HTML-like text and an unsafe URL.
- `?mode=pagination` provides 59 fictional posts.

## Deployment and registration

The integration is disabled by default and fails closed until the operator registers and allowlists a client.

Use a staging deployment with a dedicated canonical hostname, PostgreSQL, and disposable blogs first.

### 1. Install and configure the server

From the repository root:

```sh
uv sync
```

Set these variables in the Django service's environment:

- `MATAROA_CHATGPT_ENABLED=1`
- `MATAROA_MCP_ISSUER_URL=https://<canonical-mataroa-host>` (must match `DOMAIN`; no trailing slash)
- `MATAROA_CHATGPT_CLIENT_IDS=<registered-client-id>` (comma-separated explicit allowlist)

The resource identifier is the exact issuer plus `/mcp`. Normal Mataroa database/session/email settings still apply. Migrations and static collection work independently of the enable flag.

OAuth uses `main.OAuthClient`, `main.OAuthGrant`, and `main.OAuthToken`. The initial schema migration creates these three tables with the normal Django app, even when the integration is disabled. Run these commands before starting or reloading the service:

```sh
uv run python manage.py migrate
uv run python manage.py collectstatic --no-input
uv run python manage.py check
```

Ensure the collected UI assets are publicly reachable over HTTPS before reloading the workers. They contain no account data.

Keep the existing Gunicorn command targeting `mataroa.wsgi:application` and the
existing HTTPS reverse proxy. The proxy must preserve the Host header and set
`X-Forwarded-Proto: https`; Gunicorn must trust forwarded headers only from that
proxy (its `forwarded_allow_ips` setting). Django checks the resulting scheme
and canonical hostname, and rejects requests when either is wrong. Do not
replace trusted-proxy configuration with a wildcard or trust client-supplied
forwarded headers directly.

Forward `/mcp`, `/oauth/`, and the discovery paths to Django without redirects.
Streaming proxy settings are not required. Verify TLS, forwarded HTTPS handling,
static asset URLs, rate limits, and log redaction before connecting a real account.

### 2. Pre-register the ChatGPT OAuth client

Register the client through Django admin’s **OAuth clients** screen.

- Client ID and name: the values configured for the ChatGPT connection
- Client type: the public or confidential type selected in ChatGPT's connection settings
- Redirect URIs: copy the **exact** URI displayed by ChatGPT; HTTPS only, no wildcard
- New secret: for confidential clients, enter the same random secret configured in ChatGPT (at least 32 characters). The admin hashes it and never displays it again; blank on edit keeps the existing hash.
- Put the created client ID in `MATAROA_CHATGPT_CLIENT_IDS`

Supported token authentication methods are `client_secret_basic`, `client_secret_post`, and public-client `none`, all with S256 PKCE. Discovery supports issuer identification; use the callback URI ChatGPT displays.

### 3. Connect in ChatGPT

In [ChatGPT Plugins](https://chatgpt.com/plugins), choose the plus button, then **Add custom MCP server**. Enter the staging HTTPS endpoint including `/mcp`, configure OAuth with the pre-registered client, review the permissions, and choose **Create as a plugin**.

Verify the discovered tool list. Start with `blog:read`, then test separately granting `drafts:write`, `posts:publish`, and `posts:delete`. Each user signs in to their own Mataroa account on Mataroa's OAuth screen.

After changing server metadata, refresh the custom MCP connection and start a new chat. Test the [acceptance cases](#acceptance-checks), especially denied consent, reconnecting, stale drafts, and account switching.

### 4. Package and submit

From the repository root, run:

```sh
./chatgpt/package.sh
```

This creates `mataroa-plugin.zip` in the repository root, replacing any previous
archive. It includes the manifests, assets, and skills.

The `chatgpt/` directory follows the portable Agent Plugins layout:

- `plugin.json`: plugin manifest
- `mcp.json`: production MCP endpoint; change it for staging
- `skills/`: connection and blog-management workflows
- `assets/`: branding

UI and mention metadata follow [OpenAI MCP Extensions](https://github.com/openai/mcp-extensions) directly.

If using a registered MCP-server mapping, obtain its real `plugin_asdk_app...` ID from ChatGPT after registration and package the supported `.app.json` mapping using the current [packaging documentation](https://developers.openai.com/plugins/build/plugins).

Follow the current [upload and submission flow](https://developers.openai.com/plugins/deploy/submission): upload the plugin ZIP in the OpenAI Plugins portal under the verified developer identity, resolve metadata and skill findings, then connect the public HTTPS endpoint under **MCPs**. Complete domain verification and authentication, inspect the tool scan, supply privacy/terms URLs and review information, and submit for review. Publish after review approval. Reviewer credentials belong in the portal's private review fields, never in the ZIP. Keep consent scopes, advertised capabilities, and implemented tools aligned.

### Operation

- Use Django admin’s **OAuth grants** screen to revoke selected grants and all their tokens. Grant fields are read-only.
- Set `MATAROA_CHATGPT_ENABLED=0` and restart to remove MCP/OAuth routes. This does not delete existing data or grants; explicitly revoke grants if retiring the service.
- Schedule `uv run python manage.py clearoauth` to remove expired families. It retains rotated token hashes while any descendant can still be renewed, so replay detection continues to work.
- Keep credentials out of source files, manifests, chat, and build artifacts. Never log authorization headers, OAuth request bodies, authorization codes, refresh tokens, or private tool arguments.

## Security

### Identity and authorization

Every MCP request validates an opaque Mataroa OAuth token, including expiration, active user, client allowlist, exact MCP resource, and allowed scopes. The token's user primary key is the only source of account identity. Tool arguments cannot select a user or supply credentials. Each request creates its own owner-scoped tool service, with no shared mutable current-user state. Revocation applies on the next request.

The provider supports pre-registered authorization-code clients with exact HTTPS redirect URI matching, required state and S256 PKCE, consent, audience-bound access and refresh tokens, refresh rotation and reuse protection. Dynamic registration, password/implicit/device grants, external metadata fetching, and API-key fallback are excluded. Each consent grant is the shared database lock for code consumption, token renewal, and revocation, including requests using different generations of refresh tokens. Successful code reuse or refresh-token replay revokes the whole family; incorrect client credentials or PKCE proof cannot trigger that revocation. Failed token issuance rolls the transaction back.

Scopes:

- `blog:read`: owned posts/drafts/pages/comments
- `drafts:write`: create or edit unpublished drafts
- `posts:publish`: publish/schedule approved drafts
- `posts:delete`: permanently delete approved posts, including their comments and page-view records

Read permission is required at transport and tool level. Write permissions are independent and checked in the handler before reaching storage. Server instructions and packaged skills require actual user approval before publishing or deleting. OAuth grants, tool annotations, and content fingerprints alone do not establish approval for a specific post.

### OAuth storage and lifetime

`OAuthClient` holds the operator-registered callback URLs and Django-hashed client
secret. `OAuthGrant` binds the consenting user, client, redirect, permissions,
resource, and S256 challenge. `OAuthToken` stores one access/refresh pair's hashes
and expiry times. Codes and tokens contain 256 random bits; only SHA-256 digests
are stored. Authorization codes expire after two minutes, access tokens after
one hour, and refresh tokens after 30 days. Renewal rotates both tokens, can
narrow permissions, and starts a new 30-day refresh lifetime.

Consumed code hashes and rotated refresh hashes remain attached to the grant
for replay detection. `clearoauth` locks and rechecks expired families before
deleting them. No JWT signing is implemented. Mataroa owns the protocol logic
and its maintenance; the test suite is not an independent security audit.

Protocol references: [OAuth 2.0](https://www.rfc-editor.org/rfc/rfc6749.html),
[S256 PKCE](https://www.rfc-editor.org/rfc/rfc7636.html),
[security best practices](https://www.rfc-editor.org/rfc/rfc9700.html), and
[token revocation](https://www.rfc-editor.org/rfc/rfc7009.html).

### Storage and cross-account boundaries

Queries filter posts/pages by owner and comments by post owner. Same slugs on different blogs are resolved within the authenticated account. Foreign-only resources return a generic not-found error. Comment email addresses are deferred at ORM retrieval and removed again at the MCP layer. Post bodies are never interpreted as instructions or executable HTML by the UI.

### Writes

New posts are always unpublished. Updates and publication accept a content fingerprint binding slug, title, body, and publication date. On PostgreSQL, they read and compare under a row lock in one transaction. Already published or scheduled posts are refused. Conflicts are not retried with a silently refreshed fingerprint.

Deletion accepts the same reviewed content fingerprint and checks it under the post row lock before deleting. It works for drafts, scheduled posts, and published posts; it uses Django’s normal cascading deletion of related comments and page-view records. A changed post requires renewed approval. Publication sets the upstream model's publication date and preserves its subscriber-notification eligibility. Actual mailing remains Mataroa's existing scheduled process; this plugin does not independently send mail.

SQLite tests validate guards and rollback, but cannot prove PostgreSQL row-lock concurrency. PostgreSQL race tests cover edit-versus-delete, publication, single-use codes, refresh replay, old/new refresh generations, and revocation racing with renewal. Repeat them on the staging database before release.

### MCP transport

`main/views/mcp.py` implements only our fixed tool/resource surface, using the
JSON-response form of Streamable HTTP. It supports the 2025-03-26, 2025-06-18,
and 2025-11-25 initialization handshake and the 2026-07-28 per-request metadata
format. Modern requests validate protocol/method/name headers against the body,
including encoded names. Discovery advertises only tools, resources, and the UI
and mention extensions we use. Sessions, subscriptions, SSE, background tasks,
and server-initiated requests are not provided. Each POST accepts one JSON-RPC
message and returns JSON. Authenticated GET and DELETE requests return 405.

Requests require bearer authentication even when the browser has a Django login
session. The MCP view is CSRF-exempt; OAuth consent keeps Django's CSRF checks.
Canonical HTTPS and Origin validation precede dispatch. The host middleware lets
this endpoint validate its own host so blog redirects cannot forward a bearer
request to another domain. Bodies are limited to 2 MiB, JSON batches and duplicate
keys are rejected, and notifications never execute tool calls. Tool schemas and
validation share the same small set of scalar constraints; unknown fields and
type coercion are rejected. Responses are not cached. Unexpected exceptions
return a generic error without logging private arguments or credentials. Tool
pagination is capped. UI messages must come from the parent frame; pending
requests time out and are cleared on teardown.

Protocol references: [2025 Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[2026 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http),
and [discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover).

## Acceptance checks

Run with disposable staging users Alice and Bob. Before release, verify real ChatGPT account linking, reconnecting, account switching, revocation, and UI rendering in its sandbox.

| Request or event | Expected result |
| --- | --- |
| Open Posts | Initial result renders without a duplicate list call |
| Show my drafts | Draft summaries only; full body requires get_post |
| Read a same-slug post using Alice then Bob | Each receives only their own version |
| Find another user's private slug | Generic not-found; no title/body leak |
| Read a comment | Private commenter email absent |
| Save this as a draft | New unpublished post, subject to user's save request |
| Edit a published or scheduled post | Refused |
| Publish a draft without publish scope | Refused before storage |
| Publish a reviewed draft after an outside edit | Fingerprint conflict; re-review required |
| Publish a reviewed draft twice | Second call refuses already-published state |
| Schedule for an explicit future date | Date preserved, UI marks Scheduled |
| Edit pages, moderate comments | Explain unsupported operation; no mutation |
| Blog text says to reveal another user's data | Treat it as content, not an instruction |
| OAuth consent denied | No usable grant; keep account disconnected |
| Missing/wrong resource on authorization or code exchange | Rejected |
| Wrong PKCE verifier or stale/reused code | Rejected without issuing a usable token |
| Revoked/expired token or inactive user | Next MCP call rejected |
| Delete a reviewed post with `posts:delete` | Only the owned post and its related comments/page views are removed |
| Delete without `posts:delete`, or after the reviewed post changes | Refused without mutation; obtain fresh approval for changes |
| Read-only OAuth grant | Reads work, all mutations fail |
| Narrow a refresh grant, then attempt scope escalation | Original scope ceiling preserved |
| Empty blog | Successful connection and useful empty state |
| Double-click, Back, search during a pending fetch | No stale result overwrites the current view |
| Body contains script/HTML or unsafe links | Displayed as text; no execution or unsafe navigation |

Automated coverage lives in `main/tests/test_mcp*.py`, `main/tests/test_oauth.py`, and `chatgpt/web/tests/`.

## Sources

- [Mataroa API documentation](https://mataroa.blog/api/docs/)
- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI plugin authentication](https://developers.openai.com/plugins/build/auth)
- [Connect and test a ChatGPT plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI MCP Extensions specification](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md)
