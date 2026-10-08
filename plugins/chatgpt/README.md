# Mataroa for ChatGPT

An **official-integration candidate**, built into Mataroa's Django application. It is a local, tested implementation, **not yet deployed, registered with ChatGPT, or published**. The `https://mataroa.blog/mcp` URL in `mcp.json` is the intended endpoint, not a claim that it is live.

## What it does

- Native **Blog Library** sidebar/thread view: search, filter, paginate, and read posts safely.
- Read owned posts, drafts, static pages, and comments; comment email addresses are excluded.
- Save and edit unpublished drafts, with revision checks.
- Publish or schedule an explicitly approved draft, with a separate permission.
- Composer post mentions and resource retrieval where the host supports them (currently ChatGPT desktop).

No delete, unpublish, live-post edits, page writes, moderation, image upload, or account administration tools are exposed.

## Architecture

ChatGPT → OAuth-protected Streamable HTTP `/mcp` → verified token subject → owner-scoped Django ORM.

[Django OAuth Toolkit](https://django-oauth-toolkit.readthedocs.io/) handles authorization code + S256 PKCE, consent, opaque hashed tokens, refresh rotation, and revocation. The plugin limits the provider to explicitly allowlisted, pre-registered clients and one exact MCP resource. It does not store users' Mataroa API keys, pass MCP tokens to the REST API, or share a global blog account.

Permissions are independent: `blog:read`, `drafts:write`, and `posts:publish`. Every operation enforces scopes in code. OAuth consent grants access, not permission to publish arbitrary content. ChatGPT's tool approval and the packaged workflow handle the user's authorization for each publication; tool annotations and a fingerprint are not proof of human approval by themselves.

Draft mutations verify a content fingerprint while holding the PostgreSQL row lock. The official server uses this ORM backend. `client.py` also contains an optional, unused REST adapter; its API read-before-write check cannot be atomic and is not the production server's backend.

## Development

From the **Mataroa repository root**:

```sh
uv sync --extra chatgpt --all-groups
uv run --extra chatgpt python manage.py check
```

To rebuild the native UI (Node 22+):

```sh
cd plugins/chatgpt
npm ci
npm run build
npm run typecheck
npm test
npm run test:ui
```

For isolated Python tests, from `plugins/chatgpt`:

```sh
uv sync --all-groups
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use disposable SQLite databases and fabricated credentials only. They do not connect to a real Mataroa account. The full plugin suite, all three row-lock race tests, and the existing Mataroa regression suite also passed on disposable PostgreSQL 16.15. Repeat these checks on the deployment's database version; SQLite alone cannot validate row-lock scheduling. See [Verification](docs/verification.md).

## Deployment and ChatGPT connection

See [Deployment and registration](docs/deployment.md), [Security model](docs/security.md), and [Acceptance checks](docs/acceptance.md). The integration is disabled by default and fails closed until the operator registers and allowlists a client.

This plugin's `plugin.json`, `mcp.json`, skills, and assets follow the portable Agent Plugins layout. UI and mentions use [OpenAI MCP Extensions](https://github.com/openai/mcp-extensions) 0.1.0 with MCP Python 2.3. Dependencies are locked. Public-directory submission additionally requires the deployed endpoint, verified developer details, privacy/terms URLs, screenshots, and review; those are not fabricated in this package.

## Sources

- [Mataroa API documentation](https://mataroa.blog/api/docs/)
- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI plugin authentication](https://developers.openai.com/plugins/build/auth)
- [Connect and test a ChatGPT plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI MCP Extensions specification](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md)

The parent repository's [AGPL-3.0 license](../../LICENSE) applies. Bundled dependency notices are retained in the built UI.
