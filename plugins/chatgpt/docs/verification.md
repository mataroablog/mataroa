# Verification

## Checked locally

- Django suite with the integration enabled: **461 tests on PostgreSQL 17**, all passing, including six concurrency tests
- The same **461 tests on SQLite**, with six PostgreSQL-only tests skipped
- Integration disabled: **387 tests on SQLite**, passing with three skips
- Official MCP Python client 2.3 successfully initialized/discovered the Django endpoint over real HTTP with both 2025-11-25 and 2026-07-28 protocols, listed all 11 tools, opened the library, searched mentions, and read post/UI resources (one-off compatibility check before uninstalling the SDK)
- **40 checks on the browser test page**, using the production scripts and rendered Django template
- Includes real DOM interactions, narrow layout, and an opaque-origin sandbox check
- Manifest-hashed static URLs and matching resource CSP verified for same-site and CDN static hosting
- OAuth consent uses Mataroa’s own layout and renders without Toolkit static assets
- Django system checks with integration enabled and disabled
- Ruff lint/format checks, workflow YAML/shell syntax, and `git diff --check`

Python code uses the main project’s dependencies and Django test runner. The MCP transport and tool schemas use Django and the standard library; `mcp`, `openai-mcp-extensions`, `pydantic`, `uvicorn`, and the test dependency `httpx` have been removed. The full suites pass with these packages absent. The UI and its browser checks are unchanged by the transport rewrite. The OAuth implementation uses Django and the standard library; `django-oauth-toolkit`, `oauthlib`, and `jwcrypto` have been removed from the dependency lock. Client registration, grant revocation, token issuance rollback, hashed credential storage, and expired-family cleanup have automated coverage.

## Run checks

From the Mataroa repository root:

```sh
uv sync --all-groups
uv run python manage.py test --settings=mataroa.settings_test
uv run ruff check
uv run ruff format --check
```

The test settings enable OAuth and use an isolated SQLite test database by default. The suite covers owner isolation, scopes, PKCE, audience binding, consent, revocation, token rotation, error redaction, and draft revision guards. SQLite cannot establish PostgreSQL row-lock behavior.

For the frontend, run `uv run python plugins/chatgpt/web/preview.py` from the repository root and open `http://127.0.0.1:4173/tests/`. The page displays pass/fail results. It replaces the Node/jsdom/Playwright setup; automatic browser launch, CI exit codes, real input automation, and screenshots are no longer included. Use `/preview/` for manual interaction and appearance checks. See [the frontend README](../web/README.md).

## PostgreSQL concurrency checks

Use a dedicated disposable server. Set `MATAROA_TEST_POSTGRES_DB`, `MATAROA_TEST_POSTGRES_HOST`, `MATAROA_TEST_POSTGRES_PORT`, and `MATAROA_TEST_POSTGRES_USER` (plus `MATAROA_TEST_POSTGRES_PASSWORD` through secure environment configuration), then run the same Django test command above. Django creates a separate test database. CI runs this configuration as well.

This enables six concurrent-operation tests: two owners using one WSGI application, exactly-once publication, single-use authorization codes, refresh-token replay, replay across different token generations, and revocation racing with renewal. Do not point the test configuration at a production server.

## Still requires staging verification

- Live ChatGPT registration, account linking, installation, and UI rendering in its sandbox
- Production TLS/reverse proxy, rate limits, operational logging, and the deployment's PostgreSQL version
- Public-directory review or publication

The fixture preview and browser test page use fictional posts. Local tests do not create production data or OAuth grants.
