import { test, afterEach, assert, createFrame } from './runner.js';
const base = { title: 'A quiet place', slug: 'quiet', published_at: '2024-01-01', url: 'https://example.mataroa.blog/blog/quiet/', excerpt: 'Some words.' };
const draft = { ...base, title: 'An unfinished page', slug: 'draft', published_at: null };
const scheduled = { ...base, title: 'Coming soon', slug: 'soon', published_at: '2999-01-01' };
const envelope = (posts = [base, draft, scheduled], total = posts.length) => ({ content: [], structuredContent: { posts, total } });
const complete = summary => ({ content: [], structuredContent: { post: { ...summary, body: '# A heading\n\nSome words.', content_sha256: 'a'.repeat(64) } } });
let frame, app, document, window;
afterEach(() => { app?.dispose(); frame?.remove(); app = null; frame = null; });
async function setup(bridge = {}) {
  frame = await createFrame('/unit.html');
  window = frame.contentWindow;
  document = window.document;
  const { Posts } = window.Mataroa;
  const calls = [];
  app = new Posts({ callTool: async (name, args) => { calls.push({ name, args }); return name === 'list_posts' ? envelope() : complete([base, draft, scheduled].find(post => post.slug === args.slug)); }, openLink: async () => ({}), ...bridge });
  return { app, calls };
}
const byId = id => document.getElementById(id);
const click = selector => document.querySelector(selector).click();
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

test('initial notification renders immediately and handshake enables controls without a duplicate call', async () => {
  const { app, calls } = await setup();
  app.receiveInitial(envelope());
  assert.equal(document.querySelectorAll('.post-row').length, 3);
  assert.equal(document.querySelector('.post-row').disabled, true);
  app.ready();
  assert.equal(document.querySelector('.post-row').disabled, false);
  assert.deepEqual(calls, []);
  assert.match(document.querySelector('.badge-scheduled').textContent, /Scheduled/);
});
test('refresh resets filters and query with exact bounded read-only arguments', async () => {
  const { app, calls } = await setup(); app.ready(); app.receiveInitial(envelope());
  byId('search').value = 'quiet';
  click('[data-status="draft"]'); await tick();
  assert.deepEqual(calls.at(-1), { name: 'list_posts', args: { query: 'quiet', status: 'draft', limit: 50, offset: 0 } });
  click('#refresh'); await tick();
  assert.deepEqual(calls.at(-1), { name: 'list_posts', args: { query: '', status: 'all', limit: 50, offset: 0 } });
  assert.equal(byId('search').value, '');
});
test('reader renders markdown as inert text and back restores row focus', async () => {
  const unsafe = { ...base, title: '<img src=x onerror=alert(1)>', excerpt: '<script>evil()</script>' };
  const body = '<script>window.hacked = true</script>\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))';
  const { app } = await setup({ callTool: async () => ({ structuredContent: { post: { ...unsafe, body, content_sha256: 'abc' } } }) });
  app.ready(); app.receiveInitial(envelope([unsafe])); click('.post-row'); await tick();
  assert.equal(byId('post-title').textContent, unsafe.title);
  assert.equal(byId('post-body').textContent, body);
  assert.equal(document.querySelectorAll('#reader img, #reader script').length, 0);
  assert.equal(byId('reader-content').getAttribute('aria-busy'), 'false');
  click('#back');
  assert.equal(byId('reader').hidden, true);
  assert.equal(document.activeElement.className, 'post-row cursor-interaction');
});
test('draft and future-dated posts do not expose public links', async () => {
  const { app } = await setup(); app.ready(); app.receiveInitial(envelope());
  click('[data-slug="draft"]'); await tick(); assert.equal(byId('open-post').hidden, true);
  click('#back'); click('[data-slug="soon"]'); await tick(); assert.equal(byId('open-post').hidden, true);
});
test('published link requests use only the verified HTTPS URL', async () => {
  const links = [];
  const { app } = await setup({ openLink: async url => { links.push(url); return {}; } });
  app.ready(); app.receiveInitial(envelope()); click('.post-row'); await tick();
  click('#open-post'); await tick(); assert.deepEqual(links, [base.url]);
});
test('late post response cannot reopen a dismissed reader or overwrite a newer selection', async () => {
  const old = deferred();
  const { app } = await setup({ callTool: async (_name, { slug }) => slug === base.slug ? old.promise : complete(draft) });
  app.ready(); app.receiveInitial(envelope()); click('[data-slug="quiet"]'); click('#back'); click('[data-slug="draft"]'); await tick();
  old.resolve(complete(base)); await tick();
  assert.equal(byId('post-title').textContent, draft.title);
  click('#back'); assert.equal(byId('reader').hidden, true);
});
test('late search results cannot overwrite a newer query', async () => {
  const old = deferred();
  const { app } = await setup({ callTool: async (_name, { query }) => query === 'old' ? old.promise : envelope([draft]) });
  app.ready(); app.receiveInitial(envelope());
  byId('search').value = 'old'; byId('search-form').dispatchEvent(new window.Event('submit', { cancelable: true }));
  byId('search').value = 'new'; byId('search-form').dispatchEvent(new window.Event('submit', { cancelable: true })); await tick();
  old.resolve(envelope([base])); await tick();
  assert.equal(document.querySelector('.row-title').textContent, draft.title);
});
test('list failure provides a working retry and hides misleading empty state', async () => {
  let fail = true;
  const { app } = await setup({ callTool: async () => { if (fail) throw Error('Private raw error'); return envelope(); } });
  app.ready(); app.receiveInitial(envelope()); click('#refresh'); await tick();
  assert.equal(byId('posts-notice').hidden, false);
  assert.equal(byId('empty-state').hidden, true);
  assert.doesNotMatch(byId('posts-notice').textContent, /Private raw error/);
  fail = false; click('#posts-notice button'); await tick();
  assert.equal(document.querySelectorAll('.post-row').length, 3);
  assert.equal(byId('posts-notice').hidden, true);
});
test('initial error remains clear after handshake and can be retried', async () => {
  const { app } = await setup(); app.receiveInitial({ isError: true }); app.ready();
  assert.equal(byId('count').textContent, 'Posts unavailable');
  assert.equal(byId('empty-state').hidden, true);
  click('#posts-notice button'); await tick();
  assert.equal(document.querySelectorAll('.post-row').length, 3);
});
test('post failure can be retried, and mismatched returned slugs are rejected', async () => {
  let mismatch = true;
  const { app } = await setup({ callTool: async () => complete(mismatch ? draft : base) });
  app.ready(); app.receiveInitial(envelope()); click('[data-slug="quiet"]'); await tick();
  assert.equal(byId('reader-notice').hidden, false);
  assert.equal(byId('post-body').textContent, '');
  mismatch = false; click('#reader-notice button'); await tick();
  assert.match(byId('post-body').textContent, /A heading/);
});
test('pagination tracks server offsets and deduplicates rows', async () => {
  const calls = [];
  const { app } = await setup({ callTool: async (_name, args) => { calls.push(args); return envelope([base, draft], 3); } });
  app.ready(); app.receiveInitial(envelope([base], 3)); click('#load-more'); await tick();
  assert.equal(calls[0].offset, 1);
  assert.equal(document.querySelectorAll('.post-row').length, 2);
  assert.equal(byId('load-more').hidden, true);
});
test('zero-row pagination ends rather than offering an endless load more', async () => {
  const { app } = await setup({ callTool: async () => envelope([], 100) });
  app.ready(); app.receiveInitial(envelope([base], 100)); click('#load-more'); await tick();
  assert.equal(byId('load-more').hidden, true);
});
test('empty and no-match states explain the next step', async () => {
  const { app } = await setup({ callTool: async () => envelope([]) });
  app.ready(); app.receiveInitial(envelope([])); assert.equal(byId('empty-state').hidden, false);
  assert.equal(byId('reset-search').hidden, true);
  byId('search').value = 'nothing'; byId('search-form').dispatchEvent(new window.Event('submit', { cancelable: true })); await tick();
  assert.equal(byId('empty-title').textContent, 'No matching words, yet');
  assert.equal(byId('reset-search').hidden, false);
});

for (const body of [null, '']) {
  test(`draft with ${body === null ? 'null' : 'empty'} body opens as an empty read-only post`, async () => {
    const { app } = await setup({ callTool: async () => ({ structuredContent: { post: { ...draft, body, content_sha256: body === null ? 'null-hash' : 'empty-hash' } } }) });
    app.ready(); app.receiveInitial(envelope([draft])); click('.post-row'); await tick();
    assert.equal(byId('post-body').textContent, 'This post is empty.');
    assert.equal(byId('word-count').textContent, '0 words');
    assert.equal(byId('reader-notice').hidden, true);
    assert.equal(byId('open-post').hidden, true);
    assert.equal(byId('reader-content').getAttribute('aria-busy'), 'false');
  });
}
