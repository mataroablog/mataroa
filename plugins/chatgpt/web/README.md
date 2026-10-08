# Mataroa post library

A native, read-only MCP App for the ChatGPT plugin sidebar. The production app
uses the official `@modelcontextprotocol/ext-apps` bridge and bundles OpenAI's
MCP App stylesheet. No framework runtime, remote scripts, analytics, account
credentials, or direct Mataroa requests are included in the HTML.

## Build and check

Use a Node version supported by [package.json](../package.json), then run:

```sh
npm ci
npm run typecheck
npm run build
npm test
```

The build writes the self-contained resource at
[main/mcp/library.html](../../../main/mcp/library.html).
The Python server serves that resource with MCP App metadata. Commit the rebuilt
HTML when changing the frontend; Node is not needed by the production server.

`npm test` covers runtime response validation, publication states, inert-text
rendering, search/filter requests, paging, retries, late-response handling, focus
restoration, and the official bridge handshake against the actual bundled HTML.
The bundle test also checks initial tool-result delivery, server tool calls,
external-link requests, theme changes, and absence of external asset references.

## Local fixture preview

```sh
npm run preview
```

Open the printed loopback URL. It hosts a **fictional sample library**, not a
connected Mataroa account. The parent frame implements a minimal JSON-RPC host
for the real production app in an iframe. Only `list_posts`, `get_post`, and
`ui/open-link` are supported; opening a link is recorded instead of navigating.

Optional query parameters:

- `?theme=dark` starts with the dark host theme.
- `?mode=empty` shows an empty library.
- `?mode=error` returns an initial tool failure, recoverable with Refresh.
- `?mode=injection` inserts HTML-like text and an unsafe URL to check isolation.
- `?mode=pagination` provides 59 fictional posts.

For real rendering and screenshot checks:

```sh
npx playwright install chromium
npm run test:ui
```

The runner uses `/usr/bin/chromium` when available, otherwise Playwright's
installed Chromium. Set `CHROMIUM_PATH` to choose another installed binary.
Screenshots go in `web/test-results/`, which is excluded from source control.
The browser runner checks the actual bundle under a restrictive CSP with no
network access, including light/dark/narrow layouts and interrupted navigation.
If the environment prevents Chromium from creating sockets or launching, these
checks cannot run there; DOM/protocol tests do not establish pixel-level quality.

## Server contract

Register the initial result handler **before** calling `app.connect()`. The app
renders `open_library`'s initial tool result without a redundant `list_posts`
request. Once the user navigates, correlated tool-call responses take precedence
over uncorrelated host notifications.

All responses must provide `structuredContent`:

```ts
// open_library and list_posts
{
  posts: Array<{
    title: string;
    slug: string;
    published_at: string | null;
    url: string | null;
    excerpt?: string;
  }>;
  total: number;
}

// get_post
{
  post: {
    title: string;
    slug: string;
    body: string | null;
    published_at: string | null;
    url: string | null;
    content_sha256: string;
  };
}
```

Legacy `body: null` is displayed as an empty post with zero words. This is a
view-only normalization: the returned `content_sha256` is retained verbatim,
and the server must keep null distinct from an empty string when hashing.
A missing or non-string/non-null body is rejected.

The UI calls:

- `list_posts({query, status: 'all' | 'draft' | 'published', limit: 50, offset})`
- `get_post({slug})`

Refresh clears search and filters and requests `query: ''`, `status: 'all'`,
`limit: 50`, `offset: 0`. Search is debounced by 300 ms and Enter submits it
immediately. The server owns search/filter semantics and must authorize both
tools against the connected account. The sidebar never accepts credentials.

A missing publication date is a draft. A future date is labeled Scheduled,
separately from Published. Only already-published HTTPS URLs without embedded
credentials receive a View on blog button. All blog text, including Markdown,
is rendered using `textContent`. No rich-HTML renderer is used. App-originating
write tools and model-message actions are intentionally absent in this version.
