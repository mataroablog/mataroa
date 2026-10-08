# Verification

## Checked locally

- Django suite with the integration enabled: **446 tests on PostgreSQL 17**, all passing, including five concurrency tests
- The same **446 tests on SQLite**, with five PostgreSQL-only tests skipped
- Integration disabled: **388 tests on SQLite**, passing with three skips
- **29 frontend tests** against the plain JavaScript files and rendered Django template
- **11 Chromium browser scenarios** in an opaque-origin sandbox; light/dark/narrow screenshots inspected
- Manifest-hashed static URLs and matching resource CSP verified for same-site and CDN static hosting
- OAuth consent uses Mataroa’s own layout and renders without Toolkit static assets
- Django system checks with integration enabled and disabled
- Ruff lint/format checks, workflow YAML/shell syntax, and `git diff --check`

Python code uses the main project’s dependencies and Django test runner. The OAuth implementation uses Django and the standard library; `django-oauth-toolkit`, `oauthlib`, and `jwcrypto` have been removed from the dependency lock. Client registration, grant revocation, token issuance rollback, hashed credential storage, and expired-family cleanup have automated coverage.

## Run checks

From the Mataroa repository root:

```sh
uv sync --all-groups
uv run python manage.py test --settings=mataroa.settings_test
uv run ruff check
uv run ruff format --check
```

The test settings enable OAuth and use an isolated SQLite test database by default. The suite covers owner isolation, scopes, PKCE, audience binding, consent, revocation, token rotation, error redaction, and draft revision guards. SQLite cannot establish PostgreSQL row-lock behavior.

For the frontend, run `npm ci`, `npm test`, and `npm run test:ui` from `plugins/chatgpt`. There is no build or type-check step. The preview uses the repository’s Python `.venv` to render the production template. Browser checks need an installed Chromium; see [the frontend README](../web/README.md).

## PostgreSQL concurrency checks

Use a dedicated disposable server. Set `MATAROA_TEST_POSTGRES_DB`, `MATAROA_TEST_POSTGRES_HOST`, `MATAROA_TEST_POSTGRES_PORT`, and `MATAROA_TEST_POSTGRES_USER` (plus `MATAROA_TEST_POSTGRES_PASSWORD` through secure environment configuration), then run the same Django test command above. Django creates a separate test database. CI runs this configuration as well.

This enables five concurrent-operation tests: exactly-once publication, single-use authorization codes, refresh-token replay, replay across different token generations, and revocation racing with renewal. Do not point the test configuration at a production server.

## Still requires staging verification

- Live ChatGPT registration, account linking, installation, and UI rendering in its sandbox
- Concurrent different-user requests through one long-lived MCP worker; checked-in HTTP tests create fresh app instances
- Production TLS/reverse proxy, rate limits, operational logging, and the deployment's PostgreSQL version
- Public-directory review or publication

The fixture preview and Playwright browser checks use fictional posts. Local tests do not create production data or OAuth grants.
