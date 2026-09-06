const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../static/js/student-transfer.js'), 'utf8');

function fixture(responses, confirmations) {
  const requests = [], dialogs = [];
  const button = { classList: { remove() {} }, removeAttribute() {}, focus() { this.focused = true; } };
  const error = {};
  const select = { addEventListener() {} };
  const form = {
    action: '/transfer', dataset: { source: 'A' },
    querySelector: selector => selector === 'select' ? select : button,
    addEventListener: (_, callback) => { form.submit = callback; }
  };
  const window = {
    ManticoreConfirm: async (message, action) => { dialogs.push(action); return confirmations.shift(); },
    location: { assign: url => { window.destination = url; } }
  };
  vm.runInNewContext(script, {
    document: { querySelector: () => form, getElementById: id => id === 'transfer-error' ? error : {} },
    FormData: class extends Map { constructor() { super([['new_cohort1', 'B']]); } },
    window,
    fetch: async (_, options) => {
      requests.push(Object.fromEntries(options.body));
      const response = responses.shift();
      assert.ok(response, 'Unexpected extra request');
      return { ok: !response.code, headers: { get: () => 'application/json' }, json: async () => response };
    }
  });
  return { submit: () => form.submit({ preventDefault() {} }), requests, dialogs, window, button, error };
}

test('cancel full target never submits an override; restores focus', async () => {
  const f = fixture([{ code: 'capacity_exceeded', message: '25/25' }], [false]);
  await f.submit();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].preview, 'true');
  assert.equal(f.requests[0].capacity_override, undefined);
  assert.equal(f.button.focused, true);
  assert.equal(f.button.disabled, false);
});

test('full target override follows explicit confirmation', async () => {
  const f = fixture([{ code: 'capacity_exceeded', message: '27/25' }, { redirect: '/student' }], [true]);
  await f.submit();
  assert.equal(f.requests[1].capacity_override, 'true');
  assert.equal(f.requests[1].expected_source, 'A');
  assert.equal(f.requests[1].preview, undefined);
  assert.equal(f.window.destination, '/student');
});

test('last seat race requires a second confirmation', async () => {
  const f = fixture([{ message: '24/25' }, { code: 'capacity_exceeded', message: '25/25' },
    { redirect: '/student' }], [true, true]);
  await f.submit();
  assert.deepEqual(f.dialogs, ['Перевести', 'Перевести всё равно']);
  assert.equal(f.requests[1].capacity_override, 'false');
  assert.equal(f.requests[2].capacity_override, 'true');
});

test('concurrent student change displays error without retrying', async () => {
  const f = fixture([{ code: 'concurrent_update', message: 'Обновите карточку.' }], []);
  await f.submit();
  assert.equal(f.requests.length, 1);
  assert.equal(f.error.hidden, false);
  assert.equal(f.error.textContent, 'Обновите карточку.');
});
