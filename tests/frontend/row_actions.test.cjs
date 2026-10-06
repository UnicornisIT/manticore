'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { calculateDropdownPosition, init } = require(path.join(__dirname, '../../static/js/row-actions.js'));

test('aligns the menu right edge with a normal trigger', () => {
  const result = calculateDropdownPosition(
    { left: 900, right: 1000, top: 200, bottom: 230 },
    { width: 230, height: 90 },
    { width: 1280, height: 720 },
  );
  assert.deepEqual(result, { left: 770, top: 236, placement: 'bottom' });
});

test('flips above a trigger near the viewport bottom', () => {
  const result = calculateDropdownPosition(
    { left: 900, right: 1000, top: 650, bottom: 680 },
    { width: 230, height: 90 },
    { width: 1280, height: 720 },
  );
  assert.deepEqual(result, { left: 770, top: 554, placement: 'top' });
});

test('clamps both horizontal viewport edges', () => {
  assert.equal(calculateDropdownPosition(
    { left: 2, right: 32, top: 100, bottom: 130 },
    { width: 190, height: 90 },
    { width: 1280, height: 720 },
  ).left, 10);
  assert.equal(calculateDropdownPosition(
    { left: 1240, right: 1310, top: 100, bottom: 130 },
    { width: 230, height: 90 },
    { width: 1280, height: 720 },
  ).left, 1040);
});

test('keeps a menu inside a small viewport', () => {
  const result = calculateDropdownPosition(
    { left: 250, right: 310, top: 160, bottom: 190 },
    { width: 300, height: 180 },
    { width: 320, height: 240 },
  );
  assert.equal(result.left, 10);
  assert.equal(result.top, 10);
  assert.equal(result.placement, 'top');
});

test('regression: a right-side student action never falls back to the sidebar origin', () => {
  const result = calculateDropdownPosition(
    { left: 1560, right: 1650, top: 300, bottom: 330 },
    { width: 190, height: 90 },
    { width: 1920, height: 1080 },
  );
  assert.deepEqual(result, { left: 1460, top: 336, placement: 'bottom' });
  assert.notDeepEqual({ left: result.left, top: result.top }, { left: 0, top: 20 });
});

test('controller anchors a portaled menu to the trigger and Escape restores it and focus', () => {
  class Element {
    constructor(classes = []) {
      this.attributes = new Map();
      this.children = [];
      this.classNames = new Set(classes);
      this.classList = { contains: name => this.classNames.has(name) };
      this.style = {};
      this.isConnected = true;
      this.open = false;
      this.focused = false;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    appendChild(child) { child.parentNode?.removeChild?.(child); this.children.push(child); child.parentNode = this; return child; }
    insertBefore(child, reference) {
      child.parentNode?.removeChild?.(child);
      const index = this.children.indexOf(reference);
      this.children.splice(index < 0 ? this.children.length : index, 0, child);
      child.parentNode = this;
      return child;
    }
    removeChild(child) { const index = this.children.indexOf(child); if (index >= 0) this.children.splice(index, 1); child.parentNode = null; }
    remove() { this.parentNode?.removeChild(this); }
    matches(selector) { return selector === ':popover-open' && this.open; }
    showPopover() { this.open = true; }
    hidePopover() { this.open = false; }
    getBoundingClientRect() { return this.rect; }
    focus() { this.focused = true; }
  }

  const listeners = new Map();
  const document = {
    documentElement: { dataset: {} },
    body: new Element(),
    addEventListener(type, listener) { (listeners.get(type) || listeners.set(type, []).get(type)).push(listener); },
    createComment() { return new Element(); },
    getElementById(id) { return id === 'student-actions-1' ? menu : null; },
  };
  const windowListeners = new Map();
  const window = {
    innerWidth: 1920,
    innerHeight: 1080,
    addEventListener(type, listener) { (windowListeners.get(type) || windowListeners.set(type, []).get(type)).push(listener); },
  };
  const row = new Element();
  const trigger = new Element(['row-action-trigger']);
  trigger.rect = { left: 1560, right: 1650, top: 300, bottom: 330 };
  trigger.setAttribute('popovertarget', 'student-actions-1');
  const icon = new Element();
  icon.closest = selector => selector.includes('.row-action-trigger') ? trigger : null;
  const menu = new Element(['row-action-popover']);
  menu.rect = { width: 190, height: 90 };
  menu.offsetWidth = 190;
  menu.offsetHeight = 90;
  row.appendChild(trigger);
  row.appendChild(menu);

  init(document, window);
  const click = { target: icon, preventDefault() { this.defaultPrevented = true; } };
  listeners.get('click')[0](click);
  assert.equal(click.defaultPrevented, true);
  assert.equal(menu.parentNode, document.body);
  assert.equal(menu.style.left, '1460px');
  assert.equal(menu.style.top, '336px');
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');

  const escape = { key: 'Escape', preventDefault() { this.defaultPrevented = true; } };
  listeners.get('keydown')[0](escape);
  assert.equal(escape.defaultPrevented, true);
  assert.equal(menu.parentNode, row);
  assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  assert.equal(trigger.focused, true);
});
