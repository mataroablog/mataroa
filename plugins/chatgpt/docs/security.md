# Security model and review boundaries

## Identity and authorization

The official path is OAuth to Django ORM. Every HTTP call validates an opaque Mataroa OAuth token, including expiration, active user, client allowlist, exact MCP resource, and allowed scopes. The token's user primary key is the only source of account identity. Tool arguments cannot select a user or supply credentials. Revocation applies on the next request.

The provider supports pre-registered authorization-code clients with exact HTTPS redirect URI matching, required state and S256 PKCE, consent, audience-bound access and refresh tokens, refresh rotation and reuse protection. Dynamic registration, password/implicit/device grants, external metadata fetching, and API-key fallback are excluded. Each consent grant is the shared database lock for code consumption, token renewal, and revocation, including requests using different generations of refresh tokens. Successful code reuse or refresh-token replay revokes the whole family; incorrect client credentials or PKCE proof cannot trigger that revocation. Failed token issuance rolls the transaction back.

Scopes:

- `blog:read`: owned posts/drafts/pages/comments
- `drafts:write`: create or edit unpublished drafts
- `posts:publish`: publish/schedule approved drafts

Read permission is required at transport and tool level. Write permissions are independent and checked in the handler before reaching storage. Server instructions and packaged skills require actual user approval before publishing. OAuth grants and tool annotations alone do not establish approval for specific content.

## OAuth storage and lifetime

`OAuthClient` holds the operator-registered callback URLs and Django-hashed client
secret. `OAuthGrant` binds the consenting user, client, redirect, permissions,
resource, and S256 challenge. `OAuthToken` stores one access/refresh pair's hashes
and expiry times. Codes and tokens contain 256 random bits; only SHA-256 digests
are stored. Authorization codes expire after two minutes, access tokens after
one hour, and refresh tokens after 30 days. Renewal rotates both tokens, can
narrow permissions, and starts a new 30-day refresh lifetime.

Consumed code hashes and rotated refresh hashes remain attached to the grant
for replay detection. `clearoauth` locks and rechecks expired families before
deleting them. No JWT signing, dynamic registration, or additional grant flows
are implemented. Mataroa owns the protocol logic and its maintenance; the test
suite is evidence of the covered cases, not an independent security audit.

Protocol references: [OAuth 2.0](https://www.rfc-editor.org/rfc/rfc6749.html),
[S256 PKCE](https://www.rfc-editor.org/rfc/rfc7636.html),
[security best practices](https://www.rfc-editor.org/rfc/rfc9700.html), and
[token revocation](https://www.rfc-editor.org/rfc/rfc7009.html).

## Storage and cross-account boundaries

Queries filter posts/pages by owner and comments by post owner. Same slugs on different blogs are resolved within the authenticated account. Foreign-only resources return a generic not-found error. Comment email addresses are deferred at ORM retrieval and removed again at the MCP layer. Post bodies are never interpreted as instructions or executable HTML by the UI.

The MCP tools use the owner-scoped Django ORM backend directly. No API keys or REST adapter are involved.

## Writes

New posts are always unpublished. Updates and publication accept a content fingerprint binding slug, title, body, and publication date. On PostgreSQL, they read and compare under a row lock in one transaction. Already published or scheduled posts are refused. Conflicts are not retried with a silently refreshed fingerprint.

Neither deletion nor general published-content modification is exposed. Publication sets the upstream model's publication date and preserves its subscriber-notification eligibility. Actual mailing remains Mataroa's existing scheduled process; this plugin does not independently send mail.

SQLite tests validate guards and rollback, but cannot prove PostgreSQL row-lock concurrency. PostgreSQL race tests cover publication, single-use codes, refresh replay, old/new refresh generations, and revocation racing with renewal. Repeat them on the staging database before release.

## Deployment controls and remaining release gates

- Integration disabled by default; no clients are accepted until allowlisted
- Canonical HTTPS checks for OAuth and Host/Origin checks for MCP
- Stateless HTTP avoids cross-user session reuse
- Request-size bounds, capped tool pagination, sanitized errors
- Scoped consent descriptions and safe exception reporting for OAuth request data
- No third-party frontend dependencies; scripts and styles load from the configured Django static origin, explicitly allowed by the resource CSP
- Host messages must come from the parent frame; pending requests time out and are cleared on teardown

Before release: add concurrent different-user requests through one long-lived MCP worker (the current HTTP isolation tests create fresh app instances), repeat the verified database concurrency tests on staging, and check trusted reverse-proxy behavior, rate limiting, TLS, production log redaction, real ChatGPT OAuth/reconnect behavior, browser sandbox rendering, and revocation. Do not treat offline tests as a production security certification. Operator admin security, account recovery, data retention, and general Mataroa infrastructure remain upstream responsibilities.
