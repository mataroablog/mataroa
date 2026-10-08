# Verification

## Checked locally

- Django suite with the integration enabled: **432 tests**, passing with three PostgreSQL-only concurrency tests skipped on SQLite
- Django suite with the integration disabled: **387 tests**, passing with the OAuth modules and PostgreSQL-only publication test skipped
- **22 frontend tests**, TypeScript checking, and the self-contained HTML build
- The relocated UI bundle is byte-for-byte unchanged
- OAuth consent renders successfully with production manifest storage after static collection
- Django system checks with integration enabled and disabled
- Ruff lint/format checks, workflow YAML/shell syntax, and `git diff --check`

Python code now uses the main project's dependencies and Django test runner. There is no separate Python plugin package or pytest environment. The unused REST adapter and its tests have been removed.

## Run checks

From the Mataroa repository root:

```sh
uv sync --all-groups
uv run python manage.py test --settings=mataroa.settings_test
uv run ruff check
uv run ruff format --check
```

The test settings enable OAuth and use an isolated SQLite test database by default. The suite covers owner isolation, scopes, PKCE, audience binding, consent, revocation, token rotation, error redaction, and draft revision guards. SQLite cannot establish PostgreSQL row-lock behavior.

For the frontend, run `npm test`, `npm run typecheck`, and `npm run build` from `plugins/chatgpt`.

## PostgreSQL concurrency checks

Use a dedicated disposable server. Set `MATAROA_TEST_POSTGRES_DB`, `MATAROA_TEST_POSTGRES_HOST`, `MATAROA_TEST_POSTGRES_PORT`, and `MATAROA_TEST_POSTGRES_USER` (plus `MATAROA_TEST_POSTGRES_PASSWORD` through secure environment configuration), then run the same Django test command above. Django creates a separate test database. CI runs this configuration as well.

This enables the three concurrent-operation tests: exactly-once publication, single-use authorization codes, and refresh-token replay-family revocation. Do not point the test configuration at a production server.

## Still requires staging verification

- Live ChatGPT registration, account linking, installation, and UI rendering in its sandbox
- Concurrent different-user requests through one long-lived MCP worker; checked-in HTTP tests create fresh app instances
- Production TLS/reverse proxy, rate limits, operational logging, and the deployment's PostgreSQL version
- Browser/screenshot layout checks; DOM and protocol tests do not establish layout quality
- Public-directory review or publication

The fixture preview and Playwright browser checks use fictional posts. Local tests do not create production data or OAuth grants.
