const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../static/js/student-transfer.js'), 'utf8');

function option(value = '', text = '') {
  return { value, textContent: text, dataset: {}, disabled: false };
}

function fixture(responses, confirmations) {
  const requests = [], dialogs = [];
  const button = {
    disabled: false,
    classList: { remove() {} },
    removeAttribute() {},
    focus() { this.focused = true; }
  };
  const error = { hidden: true, textContent: '' };
  const capacityPreview = { textContent: '' };
  const cohort2Preview = { textContent: '' };
  const groupStatus = {
    textContent: '',
    classList: { add() { groupStatus.hasError = true; }, remove() { groupStatus.hasError = false; } }
  };
  const campaignSelect = {
    value: '2026',
    addEventListener: (_, callback) => { campaignSelect.change = callback; }
  };
  const groupSelect = {
    value: 'B', disabled: false,
    children: [Object.assign(option('B', 'B — 2/25'), { dataset: { count: '2', capacity: '25', cohort2: 'СД-1' } })],
    get selectedOptions() { return this.children.filter(item => item.value === this.value); },
    addEventListener: (_, callback) => { groupSelect.change = callback; },
    replaceChildren(...children) { this.children = children; this.value = ''; },
    appendChild(child) { this.children.push(child); }
  };
  const form = {
    action: '/transfer',
    dataset: { source: 'A', sourceCampaign: '2026', groupsUrl: '/api/groups', student: 'moving' },
    querySelector: selector => ({
      '#target_campaign_year': campaignSelect,
      '#new_cohort1': groupSelect,
      '[type="submit"]': button,
      '[data-group-status]': groupStatus
    })[selector],
    addEventListener: (_, callback) => { form.submit = callback; }
  };
  const window = {
    ManticoreConfirm: async (message, action) => { dialogs.push(action); return confirmations.shift(); },
    location: { assign: url => { window.destination = url; } }
  };
  vm.runInNewContext(script, {
    document: {
      querySelector: () => form,
      getElementById: id => ({
        'transfer-error': error,
        'transfer-capacity-preview': capacityPreview,
        'transfer_cohort2_preview': cohort2Preview
      })[id],
      createElement: () => option()
    },
    FormData: class extends Map {
      constructor() { super([['new_cohort1', groupSelect.value], ['target_campaign_year', campaignSelect.value]]); }
    },
    URLSearchParams,
    window,
    fetch: async (url, options = {}) => {
      const body = options.body ? Object.fromEntries(options.body) : null;
      requests.push({ url: String(url), body });
      const response = responses.shift();
      assert.ok(response, 'Unexpected extra request');
      return {
        ok: response.ok !== undefined ? response.ok : !response.code,
        headers: { get: () => 'application/json' },
        json: async () => response
      };
    }
  });
  return {
    submit: () => form.submit({ preventDefault() {} }),
    changeCampaign: value => { campaignSelect.value = value; return campaignSelect.change(); },
    requests, dialogs, window, button, error, groupSelect, groupStatus
  };
}

test('cancel full target never submits an override; restores focus', async () => {
  const f = fixture([{ code: 'capacity_exceeded', message: '25/25' }], [false]);
  await f.submit();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].body.preview, 'true');
  assert.equal(f.requests[0].body.capacity_override, undefined);
  assert.equal(f.button.focused, true);
  assert.equal(f.button.disabled, false);
});

test('full target override follows explicit confirmation', async () => {
  const f = fixture([{ code: 'capacity_exceeded', message: '27/25' }, { redirect: '/student' }], [true]);
  await f.submit();
  assert.equal(f.requests[1].body.capacity_override, 'true');
  assert.equal(f.requests[1].body.expected_source, 'A');
  assert.equal(f.requests[1].body.expected_campaign, '2026');
  assert.equal(f.requests[1].body.preview, undefined);
  assert.equal(f.window.destination, '/student');
});

test('last seat race requires a second confirmation', async () => {
  const f = fixture([{ message: '24/25' }, { code: 'capacity_exceeded', message: '25/25' },
    { redirect: '/student' }], [true, true]);
  await f.submit();
  assert.deepEqual(f.dialogs, ['Перевести', 'Перевести всё равно']);
  assert.equal(f.requests[1].body.capacity_override, 'false');
  assert.equal(f.requests[2].body.capacity_override, 'true');
});

test('cross-campaign preview uses an explicit serious confirmation action', async () => {
  const f = fixture([{ message: '2026 → 2023', campaign_changed: true }, { redirect: '/student' }], [true]);
  await f.submit();
  assert.equal(f.dialogs[0], 'Перенести в другую кампанию');
});

test('campaign change clears a stale group and loads only returned campaign groups', async () => {
  const f = fixture([{ groups: [{
    name: '23СД-1-1', current_students: 4, capacity: 25, cohort2: 'СД-4',
    is_current_group: false, is_full: false, is_over_capacity: false
  }] }], []);
  await f.changeCampaign('2023');
  assert.match(f.requests[0].url, /campaign_year=2023/);
  assert.equal(f.groupSelect.value, '');
  assert.deepEqual(f.groupSelect.children.map(item => item.value), ['', '23СД-1-1']);
  assert.equal(f.button.disabled, false);
});

test('campaign group loading error keeps transfer unavailable', async () => {
  const f = fixture([{ ok: false, message: 'Кампания недоступна' }], []);
  await f.changeCampaign('2023');
  assert.equal(f.groupSelect.disabled, true);
  assert.equal(f.button.disabled, true);
  assert.equal(f.groupStatus.hasError, true);
  assert.equal(f.groupStatus.textContent, 'Кампания недоступна');
});

test('concurrent student change displays error without retrying', async () => {
  const f = fixture([{ code: 'concurrent_update', message: 'Обновите карточку.' }], []);
  await f.submit();
  assert.equal(f.requests.length, 1);
  assert.equal(f.error.hidden, false);
  assert.equal(f.error.textContent, 'Обновите карточку.');
});
