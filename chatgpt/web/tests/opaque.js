// Assertions run inside the opaque iframe; its parent cannot inspect the DOM.
(async () => {
  const errors = [];
  window.addEventListener('error', event => errors.push(event.message));
  window.addEventListener('securitypolicyviolation', event => errors.push(`CSP: ${event.blockedURI}`));
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  async function wait(condition) {
    const deadline = performance.now() + 2500;
    while (!condition()) {
      if (performance.now() > deadline) throw new Error('Sandbox UI timed out');
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  }
  try {
    await wait(() => document.querySelectorAll('.post-row').length === 6 && !document.getElementById('refresh').disabled);
    check(document.documentElement.dataset.theme === 'dark', 'Host theme was not applied');
    document.querySelector('[data-slug="untrusted"]').click();
    await wait(() => document.getElementById('reader-content').getAttribute('aria-busy') === 'false');
    check(document.getElementById('post-body').textContent.includes('<script>'), 'Missing literal Markdown');
    check(!window.hacked && !document.querySelector('#reader script, #reader img'), 'Untrusted content became HTML');
    check(document.getElementById('open-post').hidden, 'Unsafe public URL was exposed');
    document.getElementById('back').click();
    document.querySelector('[data-slug="small-things"]').click();
    await wait(() => !document.getElementById('open-post').hidden);
    document.getElementById('open-post').click();
    await wait(() => !document.getElementById('open-post').disabled);
    check(errors.length === 0, errors.join('\n'));
    window.parent.postMessage({ fixtureCheck: { passed: true } }, '*');
  } catch (error) {
    window.parent.postMessage({ fixtureCheck: { passed: false, error: error.message } }, '*');
  }
})();
