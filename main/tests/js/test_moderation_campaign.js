const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../templates/partials/moderation_campaign_script.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
function setup({confirmed = false, checked = true, confirm = () => true, fetch} = {}) {
    let submitHandler, pauseHandler, cancelHandler;
    const progress = {textContent: ''};
    const last = {textContent: ''};
    const submit = {disabled: false};
    const pause = {hidden: true, addEventListener: (_, fn) => { pauseHandler = fn; }};
    const cancel = {disabled: false, addEventListener: (_, fn) => { cancelHandler = fn; }};
    const form = {action: '/selection/action/', dataset: {confirmed: String(confirmed), count: '1004'},
        addEventListener: (_, fn) => { submitHandler = fn; },
        querySelector: selector => selector.startsWith('button') ? submit : selector.includes('acknowledge') ? {checked} : {value: 'csrf'}};
    vm.runInNewContext(source, {URLSearchParams, fetch,
        window: {confirm, addEventListener() {}},
        document: {getElementById: id => ({'campaign-run': form, 'campaign-progress': progress, 'campaign-last': last, 'campaign-pause': pause, 'campaign-cancel': cancel}[id])},
    });
    return {progress, last, submit, form, run: () => submitHandler({preventDefault() {}}), pause: () => pauseHandler(), cancel: () => cancelHandler()};
}
const response = data => ({ok: true, json: async () => ({deleted: 0, skipped: 0, remaining: 1004, ...data})});
test('loading or cancelling confirmation sends no deletion requests', async () => {
    for (const options of [{checked: false}, {confirm: () => false}]) {
        const ui = setup({...options, fetch: () => assert.fail('must not send')});
        await ui.run();
        assert.equal(ui.submit.disabled, false);
    }
});
test('one confirmation processes a frozen selection beyond 1000 accounts', async () => {
    const calls = [];
    const ui = setup({fetch: async (url, options) => {
        assert.equal(options.method, 'POST');
        assert.equal(options.headers['X-CSRFToken'], 'csrf');
        const action = options.body.get('action');
        calls.push(action);
        if (action === 'confirm') {
            assert.equal(options.body.get('acknowledge'), 'yes');
            return response({});
        }
        return response({deleted: calls.length - 1, remaining: 1005 - calls.length, finished: calls.length === 1005});
    }});
    await ui.run();
    assert.equal(calls[0], 'confirm');
    assert.equal(calls.filter(x => x === 'process').length, 1004);
    assert.equal(ui.submit.disabled, true);
});
test('a saved confirmed selection resumes without widening or reconfirming', async () => {
    const ui = setup({confirmed: true, confirm: () => assert.fail('already confirmed'), fetch: async (_, options) => {
        assert.equal(options.body.get('action'), 'process');
        return response({deleted: 1004, remaining: 0, finished: true});
    }});
    await ui.run();
    assert.match(ui.last.textContent, /Finished/);
});
test('pause stops after current request and a second submit cannot race it', async () => {
    let finish;
    let calls = 0;
    const ui = setup({confirmed: true, fetch: () => { calls++; return new Promise(resolve => { finish = resolve; }); }});
    const pending = ui.run();
    await ui.run();
    ui.pause();
    finish(response({deleted: 1, remaining: 1003}));
    await pending;
    assert.equal(calls, 1);
    assert.equal(ui.submit.disabled, false);
});
test('uncertain network outcome stops; saved server progress can be resumed', async () => {
    let calls = 0;
    const ui = setup({confirmed: true, fetch: async () => { calls++; throw Error('Network error'); }});
    await ui.run();
    assert.equal(calls, 1);
    assert.match(ui.last.textContent, /Processing stopped/);
    assert.equal(ui.submit.disabled, false);
});
test('cancel is persisted and prevents another run', async () => {
    let calls = 0;
    const ui = setup({fetch: async (_, options) => {
        calls++;
        assert.equal(options.body.get('action'), 'cancel');
        return response({cancelled: true});
    }});
    await ui.cancel();
    await ui.run();
    assert.equal(calls, 1);
    assert.equal(ui.submit.disabled, true);
});
