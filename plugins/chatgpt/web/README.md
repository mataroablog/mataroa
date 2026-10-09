# Mataroa posts

A native, read-only MCP App for the ChatGPT plugin sidebar. Production uses a
Django template and ordinary JavaScript/CSS in `main/static/mcp/`. There is no
bundler, TypeScript, frontend SDK, or generated HTML. The small `bridge.js`
implements the MCP Apps JSON-RPC host connection: initialization, tool calls,
links, initial-result/cancellation notifications, theme/cursor changes, resizing,
timeouts, and teardown.

`main/mcp/server.py` renders the template with absolute static URLs, including
Django's production filename hashes. The resource CSP permits just the static
asset origin (or the configured static CDN). Data travels through the host;
`connectDomains` stays empty. Classic deferred scripts work inside the host's
sandbox without requiring cross-origin module headers.

## Browser tests and preview

From the repository root:

```sh
uv sync --all-groups
uv run python plugins/chatgpt/web/preview.py
```

Open **http://127.0.0.1:4173/tests/** in your browser. It runs 40 checks and shows
an individual pass/fail result for each. Use **Run again** to repeat them. Keep
the tab visible while tests run, since browsers can pause animation callbacks
in background tabs. Reload the page after editing test or production files.

There is no Node, npm, package lockfile, browser driver, or downloaded test
framework. A small Python server uses Django's template engine and serves only
the fixture files on loopback. It needs no database, account, or environment
configuration. Stop it with Ctrl-C; use `--port 4174` to select another port.

The tests cover validation, publication states, inert-text rendering, search,
filters, pagination, retries, late responses, focus restoration, host messages,
timeouts, themes, cursor changes, resizing, and teardown. The app scenarios load
the actual production scripts and stylesheet. An additional opaque-origin
sandbox check verifies asset loading, safe text rendering, and host-mediated
links under the resource CSP. Nothing contacts a real Mataroa account.

The former Playwright scenarios now run as assertions on this page. Automated
browser launch, process exit codes for CI, simulated keyboard/pointer input,
and automatic screenshot capture are no longer included. Tests dispatch DOM
events and click controls in code; use the preview for hands-on interaction and
visual checks. The JavaScript tests are a manual browser check, separate from
`manage.py test`. Results are also exposed as `window.testResults` for future
automation. Live ChatGPT compatibility still requires a real host check.

Open **http://127.0.0.1:4173/preview/** for the fictional posts view in a sandboxed
iframe. Public links are recorded by the simulated host instead of navigating.
Useful preview parameters:

- `?theme=dark` starts with the dark host theme.
- `?mode=empty` shows an empty posts view.
- `?mode=error` returns an initial failure, recoverable with Refresh.
- `?mode=injection` checks HTML-like text and an unsafe URL.
- `?mode=pagination` provides 59 fictional posts.

## Server contract

Register the initial result handler **before** calling `bridge.connect()`. The app
renders `open_posts`'s initial tool result without a redundant `list_posts`
request. Once the user navigates, correlated tool-call responses take precedence
over uncorrelated host notifications.

All responses must provide `structuredContent`:

```ts
// open_posts and list_posts
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

Mataroa’s post model allows `body: null`, which is displayed as an empty post
with zero words. This is a view-only normalization: the returned `content_sha256` is retained verbatim,
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
