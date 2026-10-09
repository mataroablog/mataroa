import { test, afterEach, assert, createFrame } from './runner.js';
const instances = [];
afterEach(() => { for (const { bridge, frame, postMessage } of instances.splice(0).reverse()) { bridge.close(); frame.remove(); window.postMessage = postMessage; } });
async function setup(handlers = {}) {
  const frame = await createFrame('/bridge.html');
  const win = frame.contentWindow;
  const postMessage = window.postMessage;
  const sent = [];
  let resize;
  let disconnected = false;
  win.ResizeObserver = class { constructor(callback) { resize = callback; } observe() {} disconnect() { disconnected = true; } };
  window.postMessage = message => sent.push(message);
  const bridge = new win.Mataroa.HostBridge(handlers);
  const deliver = (message, source = window) => win.dispatchEvent(new win.MessageEvent('message', { data: { jsonrpc: '2.0', ...message }, source }));
  const host = { protocolVersion: '2026-01-26', hostCapabilities: { serverTools: {}, openLinks: {} }, hostContext: { theme: 'dark' } };
  async function connect(result = host) { const promise = bridge.connect(); deliver({ id: sent.at(-1).id, result }); await promise; }
  instances.push({ bridge, frame, postMessage });
  return { win, bridge, sent, deliver, connect, resize: () => resize(), disconnected: () => disconnected };
}
const tick = () => new Promise(resolve => setTimeout(resolve, 30));

test('rejects foreign sources and malformed messages, correlates out-of-order responses', async () => {
  const { bridge, sent, deliver, connect } = await setup();
  await connect();
  const first = bridge.callTool('get_post', { slug: 'first' });
  const firstId = sent.at(-1).id;
  const second = bridge.callTool('get_post', { slug: 'second' });
  const secondId = sent.at(-1).id;
  let resolved = false; first.then(() => { resolved = true; });
  deliver({ id: firstId, result: {} }, null);
  deliver({ jsonrpc: '1.0', id: firstId, result: {} });
  deliver({ id: firstId });
  deliver({ id: 'unknown', result: {} });
  await tick(); assert.equal(resolved, false);
  deliver({ id: secondId, result: { slug: 'second' } });
  deliver({ id: firstId, result: { slug: 'first' } });
  assert.equal((await first).slug, 'first');
  assert.equal((await second).slug, 'second');
  assert.equal(bridge.pending.size, 0);
});
test('registers initial-result and cancellation handlers before initialization finishes', async () => {
  const results = []; let cancelled = 0;
  const { bridge, sent, deliver } = await setup({ result: result => results.push(result), cancelled: () => cancelled++ });
  const ready = bridge.connect();
  const id = sent.at(-1).id;
  deliver({ method: 'ui/notifications/tool-result', params: { structuredContent: { posts: [], total: 0 } } });
  deliver({ method: 'ui/notifications/tool-cancelled', params: {} });
  assert.equal(results.length, 1); assert.equal(cancelled, 1);
  deliver({ id, result: { protocolVersion: '2026-01-26', hostCapabilities: {} } });
  await ready;
  assert.equal(sent.at(-1).method, 'ui/notifications/initialized');
  await assert.rejects(bridge.callTool('list_posts', {}), /unavailable/);
  await assert.rejects(bridge.openLink('https://example.org'), /unavailable/);
});
test('reports RPC errors and cancels timed-out requests without retaining callbacks', async () => {
  const { bridge, sent, deliver, connect } = await setup(); await connect();
  const error = bridge.callTool('list_posts', {});
  deliver({ id: sent.at(-1).id, error: { code: -32603, message: 'Disconnected account' } });
  await assert.rejects(error, /Disconnected account/);
  const timeout = bridge.request('tools/call', {}, 5);
  const id = sent.at(-1).id;
  await assert.rejects(timeout, /timed out/);
  assert.equal(bridge.pending.size, 0);
  assert.equal(sent.at(-1).method, 'notifications/cancelled');
  assert.equal(sent.at(-1).params.requestId, id);
  deliver({ id, result: {} }); // A late response is ignored.
});
test('failed and timed-out initialization closes the connection', async () => {
  const { bridge, connect } = await setup();
  await assert.rejects(connect({ protocolVersion: 'unknown', hostCapabilities: {} }), /Unsupported/);
  assert.equal(bridge.closed, true);
  const other = await setup();
  await assert.rejects(other.bridge.connect(5), /timed out/);
  assert.equal(other.bridge.closed, true);
});
test('context patches preserve theme variables and cursor when omitted', async () => {
  const { win, deliver, connect } = await setup(); await connect();
  deliver({ method: 'ui/notifications/host-context-changed', params: { styles: { variables: { '--color-text-primary': '#123456' } }, 'openai/interactionCursor': 'default' } });
  deliver({ method: 'ui/notifications/host-context-changed', params: { theme: 'light' } });
  const root = win.document.documentElement;
  assert.equal(root.dataset.theme, 'light');
  assert.equal(root.style.getPropertyValue('--color-text-primary'), '#123456');
  assert.equal(root.style.getPropertyValue('--cursor-interaction'), 'default');
});
test('resize reports intrinsic height changes and teardown stops all work', async () => {
  let closed = 0;
  const { win, bridge, sent, deliver, connect, resize, disconnected } = await setup({ closed: () => closed++ });
  let height = 800;
  win.document.documentElement.getBoundingClientRect = () => ({ height });
  await connect(); await tick();
  assert.equal(sent.at(-1).method, 'ui/notifications/size-changed');
  assert.equal(sent.at(-1).params.height, 800);
  height = 200; resize(); await tick();
  assert.equal(sent.at(-1).params.height, 200);
  assert.equal(win.document.documentElement.style.height, '');
  const count = sent.length; resize(); await tick(); assert.equal(sent.length, count);
  deliver({ id: 'ping', method: 'ping' });
  assert.equal(sent.at(-1).id, 'ping');
  deliver({ id: 'unsupported', method: 'unknown' });
  assert.equal(sent.at(-1).error.code, -32601);
  const pending = bridge.callTool('list_posts', {});
  deliver({ id: 'teardown', method: 'ui/resource-teardown' });
  await assert.rejects(pending, /closed/);
  assert.equal(sent.at(-1).id, 'teardown');
  assert.equal(closed, 1); assert.equal(disconnected(), true);
  resize(); await tick();
  assert.equal(sent.at(-1).id, 'teardown');
  await assert.rejects(bridge.callTool('list_posts', {}), /unavailable/);
});
test('pagehide releases pending requests', async () => {
  const { win, bridge, connect } = await setup(); await connect();
  const pending = bridge.openLink('https://example.org');
  win.dispatchEvent(new win.Event('pagehide'));
  await assert.rejects(pending, /closed/);
  assert.equal(bridge.pending.size, 0);
});
