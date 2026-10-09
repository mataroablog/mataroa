# Verification

## Checked locally

- Django suite with the integration enabled: **461 tests on PostgreSQL 17**, all passing, including six concurrency tests
- The same **461 tests on SQLite**, with six PostgreSQL-only tests skipped
- Integration disabled: **387 tests on SQLite**, passing with three skips
- Official MCP Python client 2.3 successfully initialized/discovered the Django endpoint over real HTTP with both 2025-11-25 and 2026-07-28 protocols, listed all 11 tools, opened Posts, searched mentions, and read post/UI resources (one-off compatibility check; the SDK is not a project dependency)
- **40 checks on the browser test page**, using the production scripts and rendered Django template
- Includes real DOM interactions, narrow layout, and an opaque-origin sandbox check
- Manifest-hashed static URLs and matching resource CSP verified for same-site and CDN static hosting
- OAuth consent uses Mataroa’s own layout
- Django system checks with integration enabled and disabled
- Ruff lint/format checks, workflow YAML/shell syntax, and `git diff --check`

Python code uses the main project’s dependencies and Django test runner. The MCP transport, tool validation, and OAuth implementation use Django and the standard library. Client registration, grant revocation, token issuance rollback, hashed credential storage, and expired-family cleanup have automated coverage.

## Run checks

From the Mataroa repository root:

```sh
uv sync --all-groups
uv run python manage.py test --settings=mataroa.settings_test
uv run ruff check
uv run ruff format --check
```

The test settings enable OAuth and use an isolated SQLite test database by default. The suite covers owner isolation, scopes, PKCE, audience binding, consent, revocation, token rotation, error redaction, and draft revision guards. SQLite cannot establish PostgreSQL row-lock behavior.

For the frontend, run `uv run python plugins/chatgpt/web/preview.py` from the repository root and open `http://127.0.0.1:4173/tests/`. The page displays pass/fail results. Run this page manually; it does not provide automatic browser launch, CI exit codes, real input automation, or screenshots. Use `/preview/` for manual interaction and appearance checks. See [the frontend README](../web/README.md).

## PostgreSQL concurrency checks

Use a dedicated disposable server. Set `MATAROA_TEST_POSTGRES_DB`, `MATAROA_TEST_POSTGRES_HOST`, `MATAROA_TEST_POSTGRES_PORT`, and `MATAROA_TEST_POSTGRES_USER` (plus `MATAROA_TEST_POSTGRES_PASSWORD` through secure environment configuration), then run the same Django test command above. Django creates a separate test database. CI runs this configuration as well.

This enables six concurrent-operation tests: two owners using one WSGI application, exactly-once publication, single-use authorization codes, refresh-token replay, replay across different token generations, and revocation racing with renewal. Do not point the test configuration at a production server.

## Still requires staging verification

- Live ChatGPT registration, account linking, installation, and UI rendering in its sandbox
- Production TLS/reverse proxy, rate limits, operational logging, and the deployment's PostgreSQL version
- Public-directory review or publication

The fixture preview and browser test page use fictional posts. Local tests do not create production data or OAuth grants.
