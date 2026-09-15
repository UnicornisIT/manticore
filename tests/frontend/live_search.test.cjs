'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function fixture() {
  const timers = new Set();
  const requests = [];
  const document = new EventTarget();
  const input = new EventTarget(); input.value = '';
  const form = new EventTarget();
  form.dataset = { liveSearch: '#results' };
  form.action = 'http://localhost/students_list';
  const exportLink = { href: 'http://localhost/students/download' };
  const sortLink = { href: 'http://localhost/students_list?order_by=lastname&order_dir=desc' };
  const target = {
    children: ['original'],
    querySelectorAll: () => [sortLink],
    replaceChildren(...nodes) { this.children = nodes; },
    closest: () => null,
  };
  form.querySelectorAll = selector => selector === '[data-live-search-export]' ? [exportLink] : [input];
  form.after = () => {};
  document.querySelectorAll = () => [form];
  document.querySelector = () => target;
  document.createElement = () => ({ setAttribute() {}, textContent: '' });
  let currentURL;
  const window = {
    location: { href: form.action },
    history: { replaceState: (_state, _title, url) => { currentURL = String(url); } },
    setTimeout: callback => { timers.add(callback); return callback; },
    clearTimeout: callback => timers.delete(callback),
  };
  const context = vm.createContext({ window, document, URL, URLSearchParams, AbortController, Event,
    FormData: class { *[Symbol.iterator]() { yield ['lastname', input.value]; yield ['cohort', '26ЛД-9И-1']; yield ['order_by', 'username']; yield ['order_dir', 'asc']; } },
    DOMParser: class { parseFromString(html) { return { querySelector: () => ({ childNodes: [html] }) }; } },
    fetch: (url, options) => new Promise(resolve => requests.push({ url: String(url), options,
      respond: html => resolve({ ok: true, text: async () => html }) })),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/js/live-search.js'), 'utf8'), context);
  document.dispatchEvent(new Event('DOMContentLoaded'));
  return { input, form, target, exportLink, sortLink, requests, window,
    url: () => currentURL,
    flush: () => { for (const run of timers) { timers.delete(run); run(); } },
    type: value => { input.value = value; input.dispatchEvent(new Event('input')); },
  };
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test('list replaces only results, preserving input, filters, sort and export', async () => {
  const page = fixture();
  page.type('С');
  assert.equal(page.requests.length, 0);
  assert.equal(new URL(page.exportLink.href).searchParams.get('lastname'), 'С');
  assert.equal(new URL(page.sortLink.href).searchParams.get('order_by'), 'lastname');
  page.flush();
  assert.equal(page.requests.length, 1);
  const params = new URL(page.requests[0].url).searchParams;
  assert.equal(params.get('cohort'), '26ЛД-9И-1');
  assert.equal(params.get('order_by'), 'username');
  page.requests[0].respond('Смирнов'); await tick();
  assert.equal(page.target.children[0], 'Смирнов');
  assert.equal(page.input.value, 'С');
  assert.equal(page.url(), page.requests[0].url);
  page.type(''); page.flush();
  assert.equal(new URL(page.requests[1].url).searchParams.get('cohort'), '26ЛД-9И-1');
  page.requests[1].respond('all in group'); await tick();
  assert.equal(page.target.children[0], 'all in group');
});

test('list rejects late responses even if transport ignores abort', async () => {
  const page = fixture();
  page.type('С'); page.flush();
  page.type('См');
  assert.equal(page.requests[0].options.signal.aborted, true);
  page.requests[0].respond('stale during debounce'); await tick();
  assert.equal(page.target.children[0], 'original');
  page.flush();
  page.requests[1].respond('latest'); await tick();
  assert.equal(page.target.children[0], 'latest');
});

test('normalization matches Cyrillic, Latin, whitespace and Unicode casefold', () => {
  const normalize = fixture().window.LiveSearch.normalize;
  assert.equal(normalize('  ИвАнОв   ИВАН  '), 'иванов иван');
  assert.equal(normalize('26ЛД-9И-1'), '26лд-9и-1');
  assert.equal(normalize('Student.Test@Example.ru'), 'student.test@example.ru');
  assert.equal(normalize('Straße Σς'), 'strasse σσ');
});
