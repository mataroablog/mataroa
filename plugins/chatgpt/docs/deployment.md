# Deployment and registration

These are operator steps, not actions already performed by the build. Use a staging deployment and disposable blogs first. Production hosting, OAuth client registration, and public submission each need the owner's approval.

## 1. Install and configure the server

The MCP server is part of Mataroa's ASGI application, alongside the existing Django views. The default WSGI server does **not** expose `/mcp`.

```sh
uv sync --extra chatgpt --all-groups
```

Set these deployment variables through the deployment's normal configuration system:

- `MATAROA_CHATGPT_ENABLED=1`
- `MATAROA_MCP_ISSUER_URL=https://<canonical-mataroa-host>` (must match `DOMAIN`; no trailing slash)
- `MATAROA_CHATGPT_CLIENT_IDS=<registered-client-id>` (comma-separated explicit allowlist)

The resource identifier is derived as the exact issuer plus `/mcp`. There is no Mataroa API key variable. Normal Mataroa database/session/email settings still apply. Never put real credentials into source files, sample manifests, chat, or build artifacts.

For the repository's GitHub Actions deployment, also set the repository variable `MATAROA_CHATGPT_ENABLED=1` to match the service configuration. The workflow passes this flag to migrations and static collection; the service's environment is configured separately. The workflow always installs the `chatgpt` extra so later deployments retain its dependencies, while the enable flag defaults to `0`.

Enabling the integration loads Django OAuth Toolkit's installed app, migrations, and static assets. Run these commands with the deployment variables above set, before starting or reloading the service:

```sh
uv run --extra chatgpt python manage.py migrate
uv run --extra chatgpt python manage.py collectstatic --no-input
uv run --extra chatgpt python manage.py check
```

Static collection must run with `MATAROA_CHATGPT_ENABLED=1` so the consent stylesheet is included in the production static manifest. Without it, the authorization screen returns HTTP 500 with a missing manifest entry.

Run an ASGI worker behind the existing HTTPS reverse proxy, for example:

```sh
uv run --extra chatgpt uvicorn mataroa.asgi:application \
  --host 127.0.0.1 --port 8000 --no-access-log \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
```

Replace the proxy IP allowlist with the actual trusted proxy only. Do not use `*`. The authorization endpoints check the canonical HTTPS scheme and host; forwarding the wrong scheme causes a deliberate rejection. The deployment must forward `/mcp`, `/oauth/`, and the discovery paths to this application without redirects, token logging, buffering problems, or authentication-replacing headers.

The MCP transport independently restricts Host and Origin; configure a dedicated staging canonical hostname rather than weakening these checks. TLS termination, rate limits, and request log redaction need staging verification. Use production PostgreSQL, not SQLite.

## 2. Pre-register the ChatGPT OAuth client

Use Django admin's OAuth Toolkit Applications interface under the normal secured admin flow. Keep registration restricted to authorized operators.

- Authorization grant: authorization code
- Client type: the public or confidential type selected in ChatGPT's connection settings
- Redirect URIs: copy the **exact** URI displayed by ChatGPT; HTTPS only, no wildcard
- Skip authorization: off
- Put the created client ID in `MATAROA_CHATGPT_CLIENT_IDS`

No dynamic client registration or outbound client-metadata fetching is enabled. A client not on the allowlist cannot connect. A confidential client's secret must be supplied only through secure administrative forms; it is never tool input. The app is interoperable with `client_secret_basic`, `client_secret_post`, and public-client `none`, all with S256 PKCE.

Discovery supports issuer identification and exposes only code/refresh grants. ChatGPT may show a stable callback URI when issuer identification is supported; use the URI it actually shows, not a guessed callback.

## 3. Connect in ChatGPT

In [ChatGPT Plugins](https://chatgpt.com/plugins), choose the plus button, then **Add custom MCP server**. Enter the staging HTTPS endpoint including `/mcp`, configure OAuth with the pre-registered client, review the permissions, and choose **Create as a plugin**. This step establishes persistent access and must be done with the user's approval.

Verify the discovered tool list. Start with `blog:read`, then test separately granting `drafts:write` and `posts:publish`. Each user signs in to their own Mataroa account on Mataroa's OAuth screen. Real passwords and tokens must never be passed to an assistant.

After changing server metadata, refresh the custom MCP connection and start a new chat. Test the [acceptance cases](acceptance.md), especially denied consent, reconnecting, stale drafts, and account switching.

## 4. Package and submit

`plugin.json` is the portable Agent Plugins manifest; `mcp.json` points to the intended production endpoint. Change that endpoint for staging. Skills live under `skills/` and UI assets under `assets/`.

If using a registered MCP-server mapping, obtain its real `plugin_asdk_app...` ID from ChatGPT after registration and package the supported `.app.json` mapping using the current [packaging documentation](https://developers.openai.com/plugins/build/plugins). This build intentionally contains no fabricated registration ID.

Follow the current [upload and submission flow](https://developers.openai.com/plugins/deploy/submission): upload the plugin ZIP in the OpenAI Plugins portal under the verified developer identity, resolve metadata and skill findings, then connect the public HTTPS endpoint under **MCPs**. Complete domain verification and authentication, inspect the tool scan, supply review information, and submit for review. Publish only after approval and the owner's go-ahead. Reviewer credentials belong in the portal's private review fields, never in the ZIP. Keep consent scopes, advertised capabilities, and implemented tools aligned.

The prepared code is not evidence of a successful live connection, production deployment, or public-directory approval.

## Operation and rollback

- Rotate/revoke OAuth grants through the normal operator flow. Every MCP request rechecks token existence, expiry, active user status, allowed client, resource, and scopes.
- Set `MATAROA_CHATGPT_ENABLED=0` and restart to remove MCP/OAuth routes. This does not delete existing data or grants; explicitly revoke grants if retiring the service.
- Schedule the toolkit's expired-token cleanup through normal deployment operations after reviewing retention requirements.
- Never log request authorization headers, OAuth request bodies, authorization codes, refresh tokens, or tool bodies containing private drafts.
