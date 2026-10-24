const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../../templates/partials/moderation_bulk_script.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function setup({confirm = () => true, fetch, checked = true} = {}) {
    let submit, stopClick;
    const button = {disabled: false};
    const stop = {hidden: true, disabled: false, addEventListener: (_, fn) => { stopClick = fn; }};
    const progress = {textContent: 'Nothing has been deleted.'};
    const rows = ['one', 'two'].map(ticket => {
        const result = {textContent: 'Ready'};
        return {dataset: {ticket}, result, querySelector: () => result};
    });
    const form = {action: '/delete/', addEventListener: (_, fn) => { submit = fn; }, querySelector: sel => sel.startsWith('button') ? button : {value: 'csrf'}};
    vm.runInNewContext(script, {
        URLSearchParams, fetch,
        document: {getElementById: id => ({'bulk-confirm': form, 'bulk-stop': stop, 'bulk-progress': progress, 'bulk-acknowledge': {checked}}[id]), querySelectorAll: () => rows},
        window: {confirm, addEventListener: () => {}},
    });
    return {rows, button, progress, stop: () => stopClick(), submit: () => submit({preventDefault() {}})};
}

test('confirmation and acknowledgment are both required', async () => {
    for (const options of [{checked: false}, {confirm: () => false}]) {
        const ui = setup({...options, fetch: () => assert.fail('must not delete')});
        await ui.submit();
        assert.equal(ui.rows[0].result.textContent, 'Ready');
    }
});

test('processes only listed signed selections sequentially and reports protection skips', async () => {
    const calls = [];
    const ui = setup({fetch: async (url, options) => {
        calls.push(options.body.get('ticket'));
        assert.equal(options.method, 'POST');
        assert.equal(options.headers['X-CSRFToken'], 'csrf');
        if (calls.length === 1) return {ok: false, status: 409, json: async () => ({ok: false, error: 'Protected account: Premium'})};
        return {ok: true, json: async () => ({ok: true})};
    }});
    await ui.submit();
    assert.deepEqual(calls, ['one', 'two']);
    assert.match(ui.rows[0].result.textContent, /Protected/);
    assert.equal(ui.rows[1].result.textContent, 'Deleted');
    assert.match(ui.progress.textContent, /1 deleted; 1 skipped/);
});

test('network uncertainty stops the batch without deleting subsequent accounts or retrying', async () => {
    let calls = 0;
    const ui = setup({fetch: async () => { calls++; throw Error('timeout'); }});
    await ui.submit();
    await ui.submit();
    assert.equal(calls, 1);
    assert.match(ui.rows[0].result.textContent, /unknown/);
    assert.equal(ui.rows[1].result.textContent, 'Ready');
});

test('stop waits for the current account and blocks duplicate submits', async () => {
    let complete;
    let calls = 0;
    const ui = setup({fetch: () => { calls++; return new Promise(resolve => { complete = resolve; }); }});
    const pending = ui.submit();
    await ui.submit();
    ui.stop();
    complete({ok: true, json: async () => ({ok: true})});
    await pending;
    assert.equal(calls, 1);
    assert.equal(ui.rows[1].result.textContent, 'Ready');
    assert.match(ui.progress.textContent, /Stopped/);
});
