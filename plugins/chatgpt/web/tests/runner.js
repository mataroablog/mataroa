// A small browser runner: sequential cases, bounded waits, and cleanup on failure.
const tests = [];
const cleanups = [];
export const test = (name, run) => tests.push({ name, run });
export const afterEach = cleanup => cleanups.push(cleanup);
const describe = value => JSON.stringify(value) ?? String(value);
const fail = message => { throw new Error(message); };
function same(a, b) {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a).sort();
  return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && same(a[key], b[key]));
}
export const assert = {
  equal(actual, expected) { if (!Object.is(actual, expected)) fail(`Expected ${describe(expected)}, received ${describe(actual)}`); },
  deepEqual(actual, expected) { if (!same(actual, expected)) fail(`Expected ${describe(expected)}, received ${describe(actual)}`); },
  match(value, pattern) { if (!pattern.test(value)) fail(`${describe(value)} does not match ${pattern}`); },
  doesNotMatch(value, pattern) { if (pattern.test(value)) fail(`${describe(value)} unexpectedly matches ${pattern}`); },
  throws(fn) { try { fn(); } catch { return; } fail('Expected an exception'); },
  async rejects(promise, pattern) { try { await promise; } catch (error) { if (pattern) assert.match(error.message, pattern); return; } fail('Expected a rejected promise'); },
};
export const tick = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms));
export async function eventually(check, message = 'Condition did not become true', timeout = 3000) {
  const deadline = performance.now() + timeout;
  while (!check()) {
    if (performance.now() >= deadline) fail(message);
    await tick(20);
  }
}
export function watchFrame(frame) {
  const win = frame.contentWindow;
  const report = event => window.dispatchEvent(new ErrorEvent('error', { message: event.error?.message || event.reason?.message || event.message }));
  win.addEventListener('error', report);
  win.addEventListener('unhandledrejection', report);
}
export function createFrame(src, parent = document.getElementById('fixture')) {
  const frame = document.createElement('iframe');
  frame.title = 'Current test fixture';
  const loaded = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Could not load ${src}`)), 3000);
    frame.onload = () => { clearTimeout(timer); watchFrame(frame); resolve(frame); };
  });
  frame.src = src;
  parent.append(frame);
  return loaded;
}
export async function run() {
  const button = document.getElementById('run');
  const output = document.getElementById('results');
  const summary = document.getElementById('summary');
  button.disabled = true;
  delete window.testResults;
  output.replaceChildren();
  const results = [];
  const errors = [];
  const onError = event => errors.push(event.error?.message || event.reason?.message || event.message || String(event.reason));
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onError);
  try {
    for (const { name, run } of tests) {
      const row = document.createElement('li');
      row.textContent = `Running: ${name}`;
      output.append(row);
      summary.textContent = `Running ${results.length + 1} of ${tests.length}…`;
      let error;
      let timer;
      try {
        await Promise.race([run(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Test exceeded 10 seconds')), 10000); })]);
        if (errors.length) throw new Error(errors.join('\n'));
      } catch (failure) { error = failure; }
      finally {
        clearTimeout(timer);
        for (const cleanup of cleanups) {
          try { await cleanup(); } catch (failure) { error ??= failure; }
        }
        errors.length = 0;
        document.getElementById('fixture').replaceChildren();
      }
      row.className = error ? 'fail' : 'pass';
      row.textContent = `${error ? 'FAIL' : 'PASS'}: ${name}`;
      if (error) { const details = document.createElement('pre'); details.textContent = error.stack || error.message; row.append(details); }
      results.push({ name, passed: !error, error: error?.message });
    }
    const failed = results.filter(result => !result.passed).length;
    summary.textContent = `${results.length - failed} passed, ${failed} failed`;
    summary.className = failed ? 'fail' : 'pass';
    // Also available to anyone who later automates opening this page.
    window.testResults = results;
  } finally {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onError);
    button.disabled = false;
  }
}
