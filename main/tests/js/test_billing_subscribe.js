// Run with: node --test main/tests/js/test_billing_subscribe.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(
    path.join(__dirname, '../../templates/main/billing_subscribe.html'), 'utf8',
);
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function checkout(confirmPayment, retrievePaymentIntent = async () => ({})) {
    let onSubmit;
    const attributes = new Set();
    const nodes = Object.fromEntries(
        ['payment-form', 'loading-message', 'error-message', 'checkout-reload', 'submit']
            .map(id => [id, {textContent: '', hidden: true, disabled: false}]),
    );
    Object.assign(nodes['payment-form'], {
        hasAttribute: key => attributes.has(key),
        setAttribute: key => attributes.add(key),
        removeAttribute: key => attributes.delete(key),
        addEventListener: (_, callback) => { onSubmit = callback; },
    });
    const stripe = {
        elements: () => ({create: () => ({mount() {}})}),
        confirmPayment,
        retrievePaymentIntent,
    };
    vm.runInNewContext(script, {
        Stripe: () => stripe,
        document: {
            getElementById: id => nodes[id],
            querySelector: selector => nodes[selector.slice(1)],
        },
    });
    return {
        nodes,
        attributes,
        submit: () => onSubmit({preventDefault() {}}),
    };
}

test('expired payment offers reload and prevents another submission', async () => {
    const form = checkout(async () => ({
        error: {payment_intent: {status: 'canceled'}},
    }));
    await form.submit();
    assert.match(form.nodes['error-message'].textContent, /checkout expired/);
    assert.equal(form.nodes['checkout-reload'].hidden, false);
    assert.equal(form.nodes.submit.disabled, true);
    assert.equal(form.nodes['loading-message'].textContent, '');
    assert.equal(form.attributes.has('data-submitting'), false);
});

test('retrieves payment status when the confirmation error omits it', async () => {
    let lookedUp = false;
    const form = checkout(
        async () => ({error: {message: 'Processing error'}}),
        async () => {
            lookedUp = true;
            return {paymentIntent: {status: 'canceled'}};
        },
    );
    await form.submit();
    assert.equal(lookedUp, true);
    assert.equal(form.nodes['checkout-reload'].hidden, false);
    assert.match(form.nodes['error-message'].textContent, /checkout expired/);
});

test('card declines preserve the error and allow retrying', async () => {
    const form = checkout(async () => ({
        error: {message: 'Card declined', payment_intent: {status: 'requires_payment_method'}},
    }));
    await form.submit();
    assert.equal(form.nodes['error-message'].textContent, 'Card declined');
    assert.equal(form.nodes['checkout-reload'].hidden, true);
    assert.equal(form.nodes.submit.disabled, false);
    assert.equal(form.attributes.has('data-submitting'), false);
});

test('a failed status lookup preserves the original payment error', async () => {
    const form = checkout(
        async () => ({error: {message: 'Processing error'}}),
        async () => { throw new Error('Network unavailable'); },
    );
    await form.submit();
    assert.equal(form.nodes['error-message'].textContent, 'Processing error');
    assert.equal(form.nodes['checkout-reload'].hidden, true);
    assert.equal(form.attributes.has('data-submitting'), false);
});

test('a network exception releases the form for retrying', async () => {
    const form = checkout(async () => { throw new Error('Network unavailable'); });
    await form.submit();
    assert.match(form.nodes['error-message'].textContent, /Please try again/);
    assert.equal(form.nodes['loading-message'].textContent, '');
    assert.equal(form.attributes.has('data-submitting'), false);
});

test('a second submit cannot confirm payment while the first is pending', async () => {
    let finish;
    let confirmations = 0;
    const form = checkout(() => {
        confirmations++;
        return new Promise(resolve => { finish = resolve; });
    });
    const first = form.submit();
    await form.submit();
    assert.equal(confirmations, 1);
    finish({});
    await first;
    assert.equal(form.attributes.has('data-submitting'), false);
    assert.equal(form.nodes['checkout-reload'].hidden, true);
});
