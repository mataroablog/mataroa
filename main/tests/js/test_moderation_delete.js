// Run with: node --test main/tests/js/test_moderation_delete.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../../templates/main/moderation_inbox.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function setup(confirm, fetch) {
    let click;
    const status = {textContent: ''};
    const error = {hidden: true, textContent: ''};
    const row = {removed: false, remove() { this.removed = true; }, querySelector: () => error};
    const button = {dataset: {username: 'spam-blog', deleteUrl: '/moderation/users/7/delete/'}, disabled: false, textContent: 'Delete blog', closest: () => row};
    vm.runInNewContext(script, {
        window: {confirm}, fetch,
        document: {addEventListener: (_, handler) => { click = handler; }, getElementById: () => status},
    });
    return {button, row, error, status, click: () => click({target: {closest: () => button}})};
}

test('canceling confirmation never sends a deletion request', async () => {
    let called = false;
    const ui = setup(() => false, async () => { called = true; });
    await ui.click();
    assert.equal(called, false);
    assert.equal(ui.row.removed, false);
    assert.equal(ui.button.disabled, false);
});

test('confirmed deletion sends POST with CSRF and removes the row after success', async () => {
    const ui = setup(message => {
        assert.match(message, /spam-blog/);
        assert.match(message, /cannot be undone/);
        return true;
    }, async (url, options) => {
        assert.equal(url, '/moderation/users/7/delete/');
        assert.equal(options.method, 'POST');
        assert.ok(options.headers['X-CSRFToken']);
        assert.equal(ui.row.removed, false);
        assert.equal(ui.button.disabled, true);
        return {ok: true, json: async () => ({ok: true})};
    });
    await ui.click();
    assert.equal(ui.row.removed, true);
    assert.equal(ui.status.textContent, 'Deleted blog spam-blog.');
});

test('server and network failures keep the row and display an error', async () => {
    for (const fetch of [
        async () => ({ok: false, json: async () => ({ok: false})}),
        async () => { throw new Error('Network failed'); },
        async () => ({ok: true, json: async () => ({ok: false})}),
    ]) {
        const ui = setup(() => true, fetch);
        await ui.click();
        assert.equal(ui.row.removed, false);
        assert.equal(ui.error.hidden, false);
        assert.match(ui.error.textContent, /Reload to check/);
        assert.equal(ui.button.disabled, false);
    }
});

test('in-flight deletion cannot submit twice', async () => {
    let finish;
    let calls = 0;
    const ui = setup(() => true, async () => {
        calls++;
        return new Promise(resolve => { finish = resolve; });
    });
    const pending = ui.click();
    await ui.click();
    assert.equal(calls, 1);
    finish({ok: true, json: async () => ({ok: true})});
    await pending;
});
