# Mataroa for ChatGPT

An **official-integration candidate**, built into Mataroa's Django application. It is a local, tested implementation, **not yet deployed, registered with ChatGPT, or published**. The `https://mataroa.blog/mcp` URL in `mcp.json` is the intended endpoint, not a claim that it is live.

## What it does

- Native **Posts** sidebar/thread view: search, filter, paginate, and read posts safely.
- Read owned posts, drafts, static pages, and comments; comment email addresses are excluded.
- Save and edit unpublished drafts, with revision checks.
- Publish or schedule an explicitly approved draft, with a separate permission.
- Composer post mentions and resource retrieval where the host supports them (currently ChatGPT desktop).

No delete, unpublish, live-post edits, page writes, moderation, image upload, or account administration tools are exposed.

## Architecture

ChatGPT → ordinary Django view at `/mcp` → verified OAuth token → owner-scoped Django ORM. The endpoint uses stateless MCP Streamable HTTP with JSON responses and runs through the existing Gunicorn/WSGI deployment. No ASGI server or MCP SDK is required.

Mataroa’s own Django views and three models handle authorization code + S256 PKCE, consent, opaque hashed tokens, refresh rotation, and revocation. The implementation supports explicitly allowlisted, pre-registered clients and one exact MCP resource. There is no OAuth framework dependency. It does not store users' Mataroa API keys, pass MCP tokens to the REST API, or share a global blog account.

Permissions are independent: `blog:read`, `drafts:write`, and `posts:publish`. Every operation enforces scopes in code. OAuth consent grants access, not permission to publish arbitrary content. ChatGPT's tool approval and the packaged workflow handle the user's authorization for each publication; tool annotations and a fingerprint are not proof of human approval by themselves.

The implementation lives in `main/mcp/`: `server.py` defines the tools and validates their arguments, `backend.py` uses Django models, and the UI is a Django template (`main/templates/main/mcp_library.html`) with plain JavaScript and CSS in `main/static/mcp/`. The HTTP endpoint is `main/views/mcp.py`; OAuth issuance and verification live in `mataroa/oauth.py`. Draft mutations verify a content fingerprint while holding the PostgreSQL row lock.

This directory contains the ChatGPT manifests, skills, frontend tests/preview, and integration documentation. Python dependencies and tests belong to the main Mataroa project; there is no separately installed plugin server.

## Development

From the **Mataroa repository root**:

```sh
uv sync --all-groups
uv run python manage.py check
```

The native UI has no build step or runtime JavaScript dependencies. Django’s normal `collectstatic` publishes its files. Run the browser tests and fictional preview without Node:

```sh
uv run python plugins/chatgpt/web/preview.py
```

Open `http://127.0.0.1:4173/tests/` to run the checks, or `/preview/` to explore the posts. See [the frontend README](web/README.md) for coverage and the limits of manual browser testing.

For isolated Python tests, from the **Mataroa repository root**:

```sh
uv sync --all-groups
uv run python manage.py test --settings=mataroa.settings_test
uv run ruff check
uv run ruff format --check
```

Tests use disposable SQLite databases and fabricated credentials only. They do not connect to a real Mataroa account. CI runs the same Django suite on PostgreSQL, including publication and OAuth row-lock race tests. Repeat these checks on the deployment's database version; SQLite alone cannot validate row-lock scheduling. See [Verification](docs/verification.md).

## Deployment and ChatGPT connection

See [Deployment and registration](docs/deployment.md), [Security model](docs/security.md), and [Acceptance checks](docs/acceptance.md). The integration is disabled by default and fails closed until the operator registers and allowlists a client.

This plugin's `plugin.json`, `mcp.json`, skills, and assets follow the portable Agent Plugins layout. UI and mention metadata follow [OpenAI MCP Extensions](https://github.com/openai/mcp-extensions) directly; the implementation uses Django and the Python standard library. Public-directory submission additionally requires the deployed endpoint, verified developer details, privacy/terms URLs, screenshots, and review; those are not fabricated in this package.

## Sources

- [Mataroa API documentation](https://mataroa.blog/api/docs/)
- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI plugin authentication](https://developers.openai.com/plugins/build/auth)
- [Connect and test a ChatGPT plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI MCP Extensions specification](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md)

The parent repository's [AGPL-3.0 license](../../LICENSE) applies.
