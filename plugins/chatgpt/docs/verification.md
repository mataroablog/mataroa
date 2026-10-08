# Verification — 8 October 2026

## Passed

- **226 plugin Python tests and 62 subtests** on PostgreSQL 16.15, zero skips
- **338 existing Mataroa regression tests** on PostgreSQL 16.15
- **Three explicit concurrent-operation tests**: exactly-once draft publication, single-use OAuth authorization code, and refresh-token replay-family revocation
- **22 frontend tests**: real bundled MCP bridge handshake, initial-result rendering, null/empty drafts, search, filters, pagination, retries, interrupted navigation, light/dark host updates, inert text, and safe links
- Frontend TypeScript check and self-contained HTML build
- `npm audit`: zero reported vulnerabilities
- Ruff lint/format checks, `git diff --check`
- Django system checks with integration enabled and disabled
- Migration drift check: no new Mataroa model migrations required; OAuth Toolkit's own migrations are installed when enabled
- Plugin/MCP manifests validated against official Agent Plugins 1.0.0 JSON schemas
- Packaged skill validation
- Python wheel/source-distribution build; wheel contains the native UI HTML

Tests ran on Python 3.13.5 and Django 6.0.9. The PostgreSQL source archive was downloaded from the official distribution and verified against its published SHA-256. The cluster contained disposable fixtures only, listened on loopback, and was shut down after verification.

## Security review

An independent review identified and verified fixes for:

1. An upstream `approval_prompt=auto` override that could skip the intended fresh consent screen
2. A missing/deleted OAuth client causing an unsafe error path during consent POST
3. OAuth secrets surviving Toolkit's narrower exception-redaction decorator
4. Concurrent refresh rotation interacting incorrectly with hashed token storage
5. Missing Django connection cleanup when MCP requests bypass Django's ASGI handler

Authorization-code issuance/consumption is also serialized and rolled back if consumption fails. Real local OAuth and HTTP tests verify user isolation, scope enforcement, expiry/revocation, PKCE, audience binding, CSRF-protected login/consent, and secret redaction.

## Not yet verified

- Live ChatGPT registration, account linking, installation, and UI rendering in its sandbox
- Simultaneous requests from different users through one long-lived MCP worker: existing HTTP isolation tests use fresh app instances, so they do not establish concurrent same-worker isolation
- Production TLS/reverse proxy, rate limits, and operational logging
- Pixel-level browser/screenshot QA: Chromium cannot create the required sockets in this execution environment; DOM and protocol tests passed, but they do not establish layout quality
- Public-directory review or publication

The source includes a fixture preview and Playwright browser checks for an environment that can launch Chromium. No real user accounts, production writes, external deployments, or OAuth grants were used.

## Repeat PostgreSQL tests

Use a dedicated disposable server. Set `MATAROA_TEST_POSTGRES_DB`, `MATAROA_TEST_POSTGRES_HOST`, `MATAROA_TEST_POSTGRES_PORT`, and `MATAROA_TEST_POSTGRES_USER` (plus a password through secure environment configuration if needed). From this package:

```sh
DJANGO_SETTINGS_MODULE=tests.postgres_settings uv run pytest -q
```

Django creates a separate test database; do not point the test harness at a production server.
