// Local-only host simulator. Never loaded by the production app.
export function fixtureHost(iframe, { mode = 'normal', theme = 'light', opaque = false } = {}) {
const timers = new Set();
const now = new Date();
const ago = days => new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10);
const future = new Date(now.getTime() + 14 * 86400000).toISOString().slice(0, 10);
const posts = [
  { title: 'The small things we choose to keep', slug: 'small-things', published_at: ago(2), excerpt: 'A notebook, an afternoon walk, the kind of conversation that stays with you. Notes on making room for what matters.', body: 'A notebook, an afternoon walk, the kind of conversation that stays with you.\n\n## Making room\n\nLately I have been paying attention to the small things. The ordinary rituals that give a day its shape.\n\nThere is something to be said for keeping a little space unfilled.\n\n- A place to notice\n- A moment to think\n- A few words, written down\n\nPerhaps that is all a blog needs to be.', body_html: "<p>A notebook, an afternoon walk, the kind of conversation that stays with you.</p>\n<h2>Making room</h2>\n<p>Lately I have been paying attention to the small things. The ordinary rituals that give a day its shape.</p>\n<p>There is something to be said for keeping a little space unfilled.</p>\n<ul>\n<li>A place to notice</li>\n<li>A moment to think</li>\n<li>A few words, written down</li>\n</ul>\n<p>Perhaps that is all a blog needs to be.</p>" },
  { title: 'A slower kind of internet', slug: 'slower-internet', published_at: null, excerpt: 'What would the web feel like if we built it around attention, rather than interruption?', body: '# An unfinished thought\n\nWhat would the web feel like if we built it around attention, rather than interruption?\n\nI am still finding the words.', body_html: "<h1>An unfinished thought</h1>\n<p>What would the web feel like if we built it around attention, rather than interruption?</p>\n<p>I am still finding the words.</p>" },
  { title: 'Letters from the edge of autumn', slug: 'autumn-letters', published_at: future, excerpt: 'The light is changing. So are the things I want to write about.', body: 'The light is changing.\n\nSo are the things I want to write about.', body_html: "<p>The light is changing.</p>\n<p>So are the things I want to write about.</p>" },
  { title: 'On writing things down', slug: 'writing-things-down', published_at: ago(18), excerpt: 'Not everything needs to become something. Sometimes a few honest sentences are enough.', body: 'Not everything needs to become something.\n\nSometimes a few honest sentences are enough.', body_html: "<p>Not everything needs to become something.</p>\n<p>Sometimes a few honest sentences are enough.</p>" },
  { title: 'Hello, world. Again.', slug: 'hello-again', published_at: ago(40), excerpt: 'A new beginning, a blank page, and no particular plan.', body: 'A new beginning, a blank page, and no particular plan.', body_html: "<p>A new beginning, a blank page, and no particular plan.</p>" },
].map(post => ({ ...post, url: `https://example.mataroa.blog/blog/${post.slug}/`, content_sha256: 'a'.repeat(64) }));
if (mode === 'injection') posts.unshift({ title: '<img src=x onerror=alert(1)>', slug: 'untrusted', published_at: ago(1), excerpt: '<script>window.hacked = true</script>', body: '<script>window.hacked = true</script>\n<img src=x onerror=alert(1)>\n[Link](javascript:alert(1))', body_html: "&lt;script&gt;window.hacked = true&lt;/script&gt;\n<p>\n<a>Link</a></p>", url: 'javascript:alert(1)', content_sha256: 'b'.repeat(64) });
if (mode === 'pagination') {
  for (let i = 0; i < 54; i++) posts.push({ ...posts[0], title: `Archive note ${i + 1}`, slug: `archive-${i + 1}` });
}
const state = { posts, calls: [], links: [], delays: {}, failNext: null, held: [], holdPosts: false, mode };
function send(message) { iframe.contentWindow.postMessage({ jsonrpc: '2.0', ...message }, '*'); }
function list(args = {}) {
  const q = (args.query || '').toLowerCase();
  const filtered = (state.mode === 'empty' ? [] : posts).filter(post =>
    (args.status !== 'draft' || !post.published_at) &&
    (args.status !== 'published' || (Boolean(post.published_at) && post.published_at <= now.toISOString().slice(0, 10))) &&
    (!q || `${post.title} ${post.slug} ${post.excerpt}`.toLowerCase().includes(q)));
  return { content: [], structuredContent: { posts: filtered.slice(args.offset || 0, (args.offset || 0) + (args.limit || 50)).map(({ body, body_html, content_sha256, ...post }) => post), total: filtered.length } };
}
state.theme = theme => send({ method: 'ui/notifications/host-context-changed', params: { theme } });
state.releasePosts = () => { state.held.splice(0).forEach(reply => reply()); state.holdPosts = false; };
const receive = event => {
  if (event.source !== iframe.contentWindow || event.origin !== (opaque ? 'null' : location.origin)) return;
  const message = event.data;
  if (message?.jsonrpc !== '2.0') return;
  if (message.method === 'ui/initialize') {
    send({ id: message.id, result: { protocolVersion: message.params.protocolVersion, hostInfo: { name: 'Local Mataroa fixture host', version: '1.0.0' }, hostCapabilities: { serverTools: {}, openLinks: {} }, hostContext: { theme: theme, displayMode: 'inline' } } });
  } else if (message.method === 'ui/notifications/initialized') {
    send({ method: 'ui/notifications/tool-input', params: { arguments: {} } });
    send({ method: 'ui/notifications/tool-result', params: state.mode === 'error' ? { isError: true, content: [{ type: 'text', text: 'Fixture failure' }] } : list() });
  } else if (message.method === 'tools/call') {
    const { name, arguments: args } = message.params;
    state.calls.push({ name, args });
    const reply = () => {
      if (state.failNext === name) { state.failNext = null; send({ id: message.id, result: { isError: true, content: [{ type: 'text', text: 'Fixture failure' }] } }); return; }
      if (name === 'list_posts') send({ id: message.id, result: list(args) });
      else if (name === 'get_post') {
        const post = posts.find(post => post.slug === args.slug);
        send({ id: message.id, result: post ? { content: [], structuredContent: { post } } : { isError: true, content: [] } });
      } else send({ id: message.id, error: { code: -32601, message: 'This fixture only permits read tools.' } });
    };
    if (name === 'get_post' && state.holdPosts) state.held.push(reply);
    else { const timer = setTimeout(() => { timers.delete(timer); reply(); }, state.delays[args.query] || 20); timers.add(timer); }
  } else if (message.method === 'ui/open-link') {
    state.links.push(message.params.url);
    send({ id: message.id, result: {} });
  }
};
window.addEventListener('message', receive);
state.close = () => { window.removeEventListener('message', receive); timers.forEach(clearTimeout); iframe.remove(); };
state.send = send;
return state;
}
