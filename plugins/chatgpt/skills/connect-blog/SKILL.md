---
name: connect-blog
description: Help connect a Mataroa account to this ChatGPT plugin and verify read access after installation.
---

Use the plugin's OAuth connection flow to sign in to Mataroa and authorize the selected account. Never ask for a password or API key in chat, and never invent a setup URL.

Explain the requested permissions accurately: `blog:read` reads owned posts, drafts, pages, and comments; `drafts:write` saves unpublished drafts; `posts:publish` publishes or schedules approved drafts. OAuth permission is not approval to publish any particular post.

After the connection succeeds, call `list_posts` with `limit: 1` to verify access. An empty list is a successful connection. Do not create a test post. Then explain that the Blog Library is ready. If the server is unavailable or the tool is absent, say setup is incomplete and refer to the plugin's connection settings; do not claim installation or authentication succeeded.
