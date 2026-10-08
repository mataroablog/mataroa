import { test, afterEach, assert, eventually, tick, watchFrame } from './runner.js';
import { fixtureHost } from '/fixture-host.js';
let frame, host, doc;
afterEach(() => { host?.close(); host = null; frame = null; doc = null; });
async function open(options = {}) {
  frame = document.createElement('iframe');
  frame.title = 'Library integration test';
  frame.onload = () => watchFrame(frame);
  host = fixtureHost(frame, options);
  frame.src = '/library.html';
  document.getElementById('fixture').append(frame);
  await eventually(() => frame.contentDocument?.getElementById('refresh')?.disabled === false, 'Library did not initialize');
  doc = frame.contentDocument;
  return host;
}
const byId = id => doc.getElementById(id);
const click = selector => doc.querySelector(selector).click();
async function rows(count) { await eventually(() => doc.querySelectorAll('.post-row').length === count, `Expected ${count} post rows`); }
function search(value, submit = true) {
  byId('search').value = value;
  byId('search').dispatchEvent(new frame.contentWindow.Event('input'));
  if (submit) byId('search-form').dispatchEvent(new frame.contentWindow.Event('submit', { cancelable: true }));
}
async function read(selector = '.post-row') {
  click(selector);
  await eventually(() => byId('reader-content').getAttribute('aria-busy') === 'false', 'Post did not finish loading');
}

test('app: served scripts initialize and render initial results without a redundant call', async () => {
  await open(); await rows(5);
  assert.equal(byId('count').textContent, '5 posts in your library');
  assert.equal(doc.querySelector('.badge-scheduled').textContent, 'Scheduled');
  assert.deepEqual(host.calls, []);
  assert.equal(doc.querySelectorAll('script:not([src])').length, 0);
  assert.equal(doc.querySelectorAll('script[defer]').length, 4);
  assert.equal(doc.querySelectorAll('link[rel="stylesheet"]').length, 1);
});
test('app: search debounce, filters and refresh use the server contract', async () => {
  await open(); await rows(5);
  search('slower', false); await rows(1);
  click('[data-status="draft"]');
  await eventually(() => host.calls.at(-1)?.args.status === 'draft');
  await eventually(() => !byId('refresh').disabled);
  assert.deepEqual(host.calls.at(-1), { name: 'list_posts', args: { query: 'slower', status: 'draft', limit: 50, offset: 0 } });
  click('#refresh'); await rows(5);
  assert.deepEqual(host.calls.at(-1), { name: 'list_posts', args: { query: '', status: 'all', limit: 50, offset: 0 } });
});
test('app: reader, host-mediated public link, drafts and back navigation', async () => {
  await open(); await rows(5); await read('[data-slug="small-things"]');
  assert.match(byId('post-body').textContent, /## Making room/);
  click('#open-post'); await eventually(() => host.links.length === 1);
  assert.deepEqual(host.links, ['https://example.mataroa.blog/blog/small-things/']);
  click('#back'); assert.equal(byId('reader').hidden, true);
  await read('[data-slug="slower-internet"]');
  assert.match(byId('post-body').textContent, /unfinished thought/);
  assert.equal(byId('open-post').hidden, true);
});
test('app: late post responses cannot reopen dismissed content', async () => {
  await open(); await rows(5);
  host.holdPosts = true; click('.post-row');
  await eventually(() => host.held.length === 1);
  assert.equal(byId('post-body').textContent, 'Loading post…');
  click('#back'); host.releasePosts(); await tick(50);
  assert.equal(byId('reader').hidden, true);
  assert.equal(byId('library').hidden, false);
});
test('app: latest query wins over slow earlier results', async () => {
  await open(); await rows(5);
  host.delays.slow = 700;
  search('slow'); await eventually(() => host.calls.some(call => call.args.query === 'slow'));
  search('autumn'); await rows(1);
  assert.equal(doc.querySelector('.row-title').textContent, 'Letters from the edge of autumn');
  await tick(850);
  assert.equal(doc.querySelector('.row-title').textContent, 'Letters from the edge of autumn');
});
test('app: list and post failures offer working retries', async () => {
  await open(); await rows(5);
  host.failNext = 'list_posts'; click('#refresh');
  await eventually(() => !byId('library-notice').hidden);
  click('#library-notice button'); await rows(5);
  host.failNext = 'get_post'; await read();
  assert.equal(byId('reader-notice').hidden, false);
  click('#reader-notice button');
  await eventually(() => byId('post-body').textContent.includes('Making room'));
});
test('app: host theme and cursor updates apply; narrow layout does not overflow', async () => {
  await open(); await rows(5);
  host.send({ method: 'ui/notifications/host-context-changed', params: { theme: 'dark', 'openai/interactionCursor': 'default' } });
  await eventually(() => doc.documentElement.dataset.theme === 'dark');
  assert.equal(doc.documentElement.style.getPropertyValue('--cursor-interaction'), 'default');
  frame.style.width = '280px';
  await eventually(() => frame.contentWindow.innerWidth === 280);
  assert.equal(doc.body.scrollWidth > frame.contentWindow.innerWidth, false);
  host.send({ method: 'ui/notifications/host-context-changed', params: { theme: 'light', 'openai/interactionCursor': 'pointer' } });
  await eventually(() => doc.documentElement.dataset.theme === 'light');
  assert.equal(doc.documentElement.style.getPropertyValue('--cursor-interaction'), 'pointer');
});
test('app: untrusted title, excerpt and Markdown stay inert; unsafe links stay hidden', async () => {
  await open({ mode: 'injection' }); await rows(6); await read();
  assert.match(byId('post-body').textContent, /<script>window.hacked = true<\/script>/);
  assert.equal(byId('open-post').hidden, true);
  assert.equal(frame.contentWindow.hacked, undefined);
  assert.equal(doc.querySelectorAll('#reader img, #reader script').length, 0);
});
test('app: empty libraries and searches show helpful empty states', async () => {
  await open({ mode: 'empty' });
  await eventually(() => !byId('empty-state').hidden);
  assert.equal(byId('empty-title').textContent, 'A little room for words');
  host.mode = 'normal'; click('#refresh'); await rows(5);
  search('no-such-post'); await eventually(() => !byId('empty-state').hidden);
  assert.equal(byId('empty-title').textContent, 'No matching words, yet');
  click('#reset-search'); await rows(5);
});
test('app: initial error can recover without reconnecting', async () => {
  await open({ mode: 'error' });
  await eventually(() => !byId('library-notice').hidden);
  click('#refresh'); await rows(5);
});
test('app: pagination appends without duplication and uses the server offset', async () => {
  await open({ mode: 'pagination' }); await rows(50);
  click('#load-more'); await rows(59);
  assert.equal(byId('load-more').hidden, true);
  assert.equal(host.calls.at(-1).args.offset, 50);
});
test('app: opaque-origin sandbox loads assets, renders safely, and calls the host', async () => {
  frame = document.createElement('iframe');
  frame.title = 'Opaque-origin sandbox check';
  frame.sandbox = 'allow-scripts';
  host = fixtureHost(frame, { mode: 'injection', theme: 'dark', opaque: true });
  let result;
  const receive = event => {
    if (event.source === frame.contentWindow && event.origin === 'null' && event.data?.fixtureCheck) result = event.data.fixtureCheck;
  };
  window.addEventListener('message', receive);
  try {
    frame.src = '/opaque.html';
    document.getElementById('fixture').append(frame);
    await eventually(() => result, 'Opaque sandbox did not report its result');
    if (!result.passed) throw new Error(result.error);
    assert.deepEqual(host.links, ['https://example.mataroa.blog/blog/small-things/']);
  } finally { window.removeEventListener('message', receive); }
});
