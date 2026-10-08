import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInContext } from 'node:vm';
import { JSDOM } from 'jsdom';
const html = await readFile(new URL('../../src/mataroa_chatgpt/static/library.html', import.meta.url), 'utf8');
const post = { title: 'Bridge fixture', slug: 'bridge-fixture', published_at: '2024-01-01', url: 'https://example.mataroa.blog/blog/bridge-fixture/', excerpt: 'A local protocol test.' };
const initial = { content: [], structuredContent: { posts: [post], total: 1 } };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('built HTML is self-contained, uses official bridge handshake and consumes initial result', async () => {
  const dom = new JSDOM(html, { url: 'https://fixture.local/library.html', pretendToBeVisual: true, runScripts: 'outside-only' });
  const win = dom.window;
  const errors = [];
  const calls = [];
  const links = [];
  win.TextEncoder = TextEncoder;
  win.TextDecoder = TextDecoder;
  win.ResizeObserver = class { observe() {} disconnect() {} };
  win.scrollTo = () => {};
  win.console = { ...console, debug: () => {}, error: message => errors.push(message) };
  win.bundleError = error => errors.push(error.message);
  const deliver = message => queueMicrotask(() => win.dispatchEvent(new win.MessageEvent('message', { data: { jsonrpc: '2.0', ...message }, source: win })));
  win.postMessage = message => {
    if (message.method === 'ui/initialize') deliver({ id: message.id, result: { protocolVersion: message.params.protocolVersion, hostInfo: { name: 'Fixture', version: '1.0' }, hostCapabilities: { serverTools: {}, openLinks: {} }, hostContext: { theme: 'dark', 'openai/interactionCursor': 'default' } } });
    if (message.method === 'ui/notifications/initialized') deliver({ method: 'ui/notifications/tool-result', params: initial });
    if (message.method === 'tools/call') {
      calls.push(message.params);
      deliver({ id: message.id, result: message.params.name === 'get_post' ? { content: [], structuredContent: { post: { ...post, body: '# Read through the bridge', content_sha256: 'abc' } } } : initial });
    }
    if (message.method === 'ui/open-link') { links.push(message.params.url); deliver({ id: message.id, result: {} }); }
  };
  try {
    assert.equal(win.document.querySelectorAll('script[src], link[rel="stylesheet"]').length, 0);
    const script = win.document.querySelector('script[type="module"]').textContent;
    await runInContext(`(async () => { ${script}\n})().catch(window.bundleError)`, dom.getInternalVMContext(), { timeout: 5000 });
    await tick();
    assert.deepEqual(errors, []);
    assert.equal(win.document.documentElement.dataset.theme, 'dark');
    assert.equal(win.document.documentElement.style.getPropertyValue('--cursor-interaction'), 'default');
    assert.equal(win.document.querySelectorAll('.post-row').length, 1);
    assert.equal(win.document.querySelector('.post-row').disabled, false);
    assert.deepEqual(calls, []);
    win.document.querySelector('.post-row').click(); await tick();
    assert.equal(win.document.getElementById('post-body').textContent, '# Read through the bridge');
    assert.equal(calls[0].name, 'get_post');
    assert.equal(calls[0].arguments.slug, post.slug);
    win.document.getElementById('open-post').click(); await tick();
    assert.deepEqual(links, [post.url]);
    deliver({ method: 'ui/notifications/host-context-changed', params: { theme: 'light', 'openai/interactionCursor': 'pointer' } }); await tick();
    assert.equal(win.document.documentElement.dataset.theme, 'light');
    assert.equal(win.document.documentElement.style.getPropertyValue('--cursor-interaction'), 'pointer');
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});
