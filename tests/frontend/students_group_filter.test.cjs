'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('search, batch selection and clearing groups emit one change per batch', () => {
  const document = new EventTarget();
  const root = new EventTarget();
  root.open = true;
  const search = new EventTarget();
  const select = new EventTarget();
  const clear = new EventTarget();
  const summary = {};
  const empty = { hidden: true };
  const boxes = [false, false, true].map(checked => {
    const box = new EventTarget(); box.checked = checked;
    // Simulate bubbling in this small DOM fixture.
    box.addEventListener('change', () => root.dispatchEvent(new Event('change')));
    return box;
  });
  const options = ['26ЛД-11-1', '26ЛД-9-1', '26СД-11-1'].map((textContent, i) => ({
    textContent, hidden: false, querySelector: () => boxes[i],
  }));
  const elements = {
    '[data-group-search]': search, '[data-group-summary]': summary,
    '[data-group-select]': select, '[data-group-clear]': clear, '[data-group-empty]': empty,
  };
  root.querySelector = selector => elements[selector];
  root.querySelectorAll = () => options;
  document.querySelectorAll = () => [root];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/students-group-filter.js'), 'utf8'), { document, Event });
  document.dispatchEvent(new Event('DOMContentLoaded'));
  let changes = 0;
  root.addEventListener('change', () => { changes++; });
  search.value = ' лд ';
  search.dispatchEvent(new Event('input'));
  assert.deepEqual(options.map(option => option.hidden), [false, false, true]);
  assert.equal(empty.hidden, true);
  assert.equal(changes, 0);
  select.dispatchEvent(new Event('click'));
  assert.deepEqual(boxes.map(box => box.checked), [true, true, true]);
  assert.equal(summary.textContent, 'Группы: 3');
  assert.equal(changes, 1);
  clear.dispatchEvent(new Event('click'));
  assert.deepEqual(boxes.map(box => box.checked), [false, false, false]);
  assert.equal(summary.textContent, 'Все группы');
  assert.equal(changes, 2);
  search.value = 'нет такой группы';
  search.dispatchEvent(new Event('input'));
  assert.equal(empty.hidden, false);
});
