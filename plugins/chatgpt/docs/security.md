# Security model and review boundaries

## Identity and authorization

The official path is OAuth to Django ORM. Every HTTP call validates an opaque Django OAuth Toolkit token, including expiration, active user, client allowlist, exact MCP resource, and allowed scopes. The token's user primary key is the only source of account identity. Tool arguments cannot select a user or supply credentials. Revocation applies on the next request.

The provider supports pre-registered authorization-code clients with exact HTTPS redirect URI matching, required state and S256 PKCE, consent, audience-bound access and refresh tokens, refresh rotation and reuse protection. Dynamic registration, password/implicit/device grants, external metadata fetching, and API-key fallback are excluded. Authorization-code consumption and refresh-token validation/rotation are serialized with row locks in database transactions. Replay rejection retains token-family revocation.

Scopes:

- `blog:read`: owned posts/drafts/pages/comments
- `drafts:write`: create or edit unpublished drafts
- `posts:publish`: publish/schedule approved drafts

Read permission is required at transport and tool level. Write permissions are independent and checked in the handler before reaching storage. Server instructions and packaged skills require actual user approval before publishing. OAuth grants and tool annotations alone do not establish approval for specific content.

## Storage and cross-account boundaries

Queries filter posts/pages by owner and comments by post owner. Same slugs on different blogs are resolved within the authenticated account. Foreign-only resources return a generic not-found error. Comment email addresses are deferred at ORM retrieval and removed again at the MCP layer. Post bodies are never interpreted as instructions or executable HTML by the UI.

The MCP tools use the owner-scoped Django ORM backend directly. No API keys or REST adapter are involved.

## Writes

New posts are always unpublished. Updates and publication accept a content fingerprint binding slug, title, body, and publication date. On PostgreSQL, they read and compare under a row lock in one transaction. Already published or scheduled posts are refused. Conflicts are not retried with a silently refreshed fingerprint.

Neither deletion nor general published-content modification is exposed. Publication sets the upstream model's publication date and preserves its subscriber-notification eligibility. Actual mailing remains Mataroa's existing scheduled process; this plugin does not independently send mail.

SQLite tests validate guards and rollback, but cannot prove PostgreSQL row-lock concurrency. Three PostgreSQL race tests passed on a disposable PostgreSQL 16.15 instance: publication, single-use authorization codes, and refresh replay. Repeat them on the staging database before release.

## Deployment controls and remaining release gates

- Integration disabled by default; no clients are accepted until allowlisted
- Canonical HTTPS checks for OAuth and Host/Origin checks for MCP
- Stateless HTTP avoids cross-user session reuse
- Request-size bounds, capped tool pagination, sanitized errors
- Scoped consent descriptions and safe exception reporting for OAuth request data
- No third-party frontend dependencies; scripts and styles load from the configured Django static origin, explicitly allowed by the resource CSP
- Host messages must come from the parent frame; pending requests time out and are cleared on teardown

Before release: add concurrent different-user requests through one long-lived MCP worker (the current HTTP isolation tests create fresh app instances), repeat the verified database concurrency tests on staging, and check trusted reverse-proxy behavior, rate limiting, TLS, production log redaction, real ChatGPT OAuth/reconnect behavior, browser sandbox rendering, and revocation. Do not treat offline tests as a production security certification. Operator admin security, account recovery, data retention, and general Mataroa infrastructure remain upstream responsibilities.
