# Deployment and registration

These are operator steps, not actions already performed by the build. Use a staging deployment and disposable blogs first. Production hosting, OAuth client registration, and public submission each need the owner's approval.

The library UI has no frontend build step. Deploy its plain JavaScript/CSS with
the normal Django `collectstatic` command before starting the ASGI workers.
The MCP resource uses absolute, manifest-hashed static URLs and declares their
origin in its CSP. Ensure those static files are publicly reachable over HTTPS;
they contain no account data. Local tests use the Python preview server and a browser; Node is not required.

## 1. Install and configure the server

The MCP server is part of Mataroa's ASGI application, alongside the existing Django views. The default WSGI server does **not** expose `/mcp`.

```sh
uv sync --all-groups
```

Set these deployment variables through the deployment's normal configuration system:

- `MATAROA_CHATGPT_ENABLED=1`
- `MATAROA_MCP_ISSUER_URL=https://<canonical-mataroa-host>` (must match `DOMAIN`; no trailing slash)
- `MATAROA_CHATGPT_CLIENT_IDS=<registered-client-id>` (comma-separated explicit allowlist)

The resource identifier is derived as the exact issuer plus `/mcp`. There is no Mataroa API key variable. Normal Mataroa database/session/email settings still apply. Never put real credentials into source files, sample manifests, chat, or build artifacts.

For the repository's GitHub Actions deployment, also set the repository variable `MATAROA_CHATGPT_ENABLED=1` to match the service configuration. The workflow passes this flag to migrations and static collection; the service's environment is configured separately. MCP dependencies are included in the main project installation. The enable flag defaults to `0`.

OAuth uses `main.OAuthClient`, `main.OAuthGrant`, and `main.OAuthToken`. Their migration runs with the normal Django app even when the integration is disabled. Run these commands before starting or reloading the service:

```sh
uv run python manage.py migrate
uv run python manage.py collectstatic --no-input
uv run python manage.py check
```

The consent screen uses the normal Mataroa layout. Static collection includes the library UI independently of the enable flag.

This replaces the undeployed Toolkit-based implementation. Existing experimental Toolkit registrations and tokens are not imported: register clients in the new admin screen and reconnect test accounts. Old Toolkit tables are left untouched and unused; reverting code does not migrate new credentials back into them.

Run an ASGI worker behind the existing HTTPS reverse proxy, for example:

```sh
uv run uvicorn mataroa.asgi:application \
  --host 127.0.0.1 --port 8000 --no-access-log \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
```

Replace the proxy IP allowlist with the actual trusted proxy only. Do not use `*`. The authorization endpoints check the canonical HTTPS scheme and host; forwarding the wrong scheme causes a deliberate rejection. The deployment must forward `/mcp`, `/oauth/`, and the discovery paths to this application without redirects, token logging, buffering problems, or authentication-replacing headers.

The MCP transport independently restricts Host and Origin; configure a dedicated staging canonical hostname rather than weakening these checks. TLS termination, rate limits, and request log redaction need staging verification. Use production PostgreSQL, not SQLite.

## 2. Pre-register the ChatGPT OAuth client

Use Django admin’s **OAuth clients** screen under the normal secured admin flow. Keep registration restricted to authorized operators.

- Client ID and name: the values configured for the ChatGPT connection
- Client type: the public or confidential type selected in ChatGPT's connection settings
- Redirect URIs: copy the **exact** URI displayed by ChatGPT; HTTPS only, no wildcard
- New secret: for confidential clients, enter the same random secret configured in ChatGPT (at least 32 characters). The admin hashes it and never displays it again; blank on edit keeps the existing hash.
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

- Use Django admin’s **OAuth grants** screen to revoke selected grants and all their tokens. Each request rechecks the grant, token expiry, active user, allowed client, resource, and scopes. Grant fields are read-only.
- Set `MATAROA_CHATGPT_ENABLED=0` and restart to remove MCP/OAuth routes. This does not delete existing data or grants; explicitly revoke grants if retiring the service.
- Schedule `uv run python manage.py clearoauth` to remove expired families. It retains rotated token hashes while any descendant can still be renewed, so replay detection continues to work.
- Never log request authorization headers, OAuth request bodies, authorization codes, refresh tokens, or tool bodies containing private drafts.
