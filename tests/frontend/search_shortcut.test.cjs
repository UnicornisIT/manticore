'use strict';

// Execute the shipped scripts with a small DOM fixture; no frontend packages required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');

function fixture({ documentation = false, docsFirst = true } = {}) {
    const timers = [];
    const requests = [];
    const document = new EventTarget();

    class Element extends EventTarget {
        constructor(tagName = 'div') {
            super();
            this.tagName = tagName.toUpperCase();
            this.dataset = {};
            this.attributes = {};
            this.selectors = new Map();
            this.value = '';
            this.textContent = '';
            this.hidden = false;
            this.isConnected = true;
            this.focusCount = 0;
            this.children = [];
            this.style = { setProperty() {} };
            const classes = new Set();
            this.classList = {
                add: (...names) => names.forEach(name => classes.add(name)),
                remove: (...names) => names.forEach(name => classes.delete(name)),
                contains: name => classes.has(name),
                toggle(name, force = !classes.has(name)) {
                    if (force) classes.add(name);
                    else classes.delete(name);
                    return force;
                },
            };
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        querySelectorAll(selector) { return this.selectors.get(selector) || []; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        focus() {
            this.focusCount += 1;
            const previous = document.activeElement;
            document.activeElement = this;
            if (previous !== this) previous?.dispatchEvent(new Event('blur'));
        }
        append(...elements) { this.children.push(...elements); }
        replaceChildren(...elements) { this.children = elements; }
    }

    const selectors = new Map();
    const ids = new Map();
    document.querySelectorAll = selector => selectors.get(selector) || [];
    document.querySelector = selector => document.querySelectorAll(selector)[0] || null;
    document.getElementById = id => ids.get(id) || null;
    document.createElement = tag => new Element(tag);
    document.createDocumentFragment = () => new Element();
    document.body = new Element('body');
    document.documentElement = new Element('html');
    document.activeElement = document.body;

    const form = new Element('form');
    form.dataset.searchOverlayUrl = '/search/overlay';
    form.dataset.personUrlTemplate = '/people/__kind__/__record__';
    const toggle = new Element('button');
    const opener = new Element('button');
    const sourceInput = new Element('input');
    const input = new Element('input');
    const close = new Element('button');
    const modal = new Element();
    modal.hidden = true;
    form.selectors.set('.nav-search-toggle', [toggle]);
    form.selectors.set('.nav-search-input', [sourceInput]);
    selectors.set('.nav-search', [form]);
    selectors.set('form', [form]);
    selectors.set('[data-global-search-open]', [opener]);
    selectors.set('.nav-search-toggle-proxy', [opener]);
    selectors.set('[data-global-search-close]', [close]);
    ids.set('global-search-palette-input', input);
    ids.set('global-search-modal', modal);
    ids.set('global-search-meta', new Element());
    ids.set('global-search-results', new Element());

    const docs = new Element();
    const docsInput = new Element('input');
    const docsMenu = new Element('button');
    const docsSection = new Element('section');
    docsSection.textContent = 'Приёмные кампании';
    if (documentation) {
        selectors.set('[data-docs-app]', [docs]);
        docs.selectors.set('[data-docs-search]', [docsInput]);
        docs.selectors.set('[data-docs-menu]', [docsMenu]);
        docs.selectors.set('[data-docs-section]', [docsSection]);
        for (const selector of ['.docs-sidebar', '[data-docs-overlay]',
            '[data-docs-search-status]', '[data-docs-to-top]']) {
            docs.selectors.set(selector, [new Element()]);
        }
    }

    const window = {
        innerWidth: 1280, scrollY: 0, location: { origin: 'http://localhost' },
        setTimeout: callback => timers.push(callback), addEventListener() {},
        matchMedia: () => ({ matches: false }), AbortController,
    };
    const context = vm.createContext({
        window, document, URL, AbortController,
        IntersectionObserver: class { observe() {} },
        fetch: async (url, options) => {
            requests.push({ url, options });
            return { ok: true, json: async () => ({ query: input.value, results: [] }) };
        },
    });
    function load(relativePath) {
        vm.runInContext(fs.readFileSync(path.join(root, relativePath), 'utf8'), context,
            { filename: relativePath });
    }
    if (documentation && docsFirst) load('static/documentation.js');
    load('static/js/app.js');
    load('static/js/modern-ui.js');
    if (documentation && !docsFirst) load('static/documentation.js');

    function key(properties, target = document.activeElement) {
        const event = new Event('keydown', { cancelable: true });
        Object.assign(event, { key: '', code: '', ctrlKey: false, metaKey: false,
            repeat: false, ...properties });
        target.dispatchEvent(event);
        document.dispatchEvent(event);
        return event;
    }
    return { document, form, toggle, opener, input, close, modal, requests,
        docs, docsInput, docsMenu, docsSection, load, key,
        origin: tag => new Element(tag),
        click: target => target.dispatchEvent(new Event('click', { cancelable: true })),
        flushTimers: () => { while (timers.length) timers.shift()(); },
    };
}

for (const modifier of ['ctrlKey', 'metaKey']) {
    for (const key of ['k', 'л', 'K', 'ל']) {
        test(`${modifier} + physical KeyK opens for key=${key}`, () => {
            const page = fixture();
            const event = page.key({ [modifier]: true, code: 'KeyK', key });
            page.flushTimers();
            assert.equal(event.defaultPrevented, true);
            assert.equal(page.modal.hidden, false);
            assert.equal(page.document.activeElement, page.input);
            assert.equal(page.input.focusCount, 1);
            assert.equal(page.requests.length, 0);
        });
    }
}

test('plain K, another physical key, and unrelated shortcuts do not open search', () => {
    const page = fixture();
    for (const properties of [
        { code: 'KeyK', key: 'k' }, { code: 'KeyK', key: 'л' },
        { code: 'KeyL', key: 'k', ctrlKey: true },
        { code: 'KeyL', key: 'л', metaKey: true },
    ]) {
        assert.equal(page.key(properties).defaultPrevented, false);
        assert.equal(page.modal.hidden, true);
    }
    assert.equal(page.input.focusCount, 0);
});

for (const origin of ['body', 'td', 'select', 'input']) {
    test(`Escape restores ${origin} focus and reopen stays stable`, () => {
        const page = fixture();
        const previous = origin === 'body' ? page.document.body : page.origin(origin);
        previous.focus();
        for (let cycle = 0; cycle < 2; cycle += 1) {
            page.key({ code: 'KeyK', key: 'л', ctrlKey: true });
            assert.equal(page.document.activeElement, page.input);
            page.key({ code: 'Escape', key: 'Escape' });
            page.flushTimers();
            assert.equal(page.modal.hidden, true);
            assert.equal(page.document.activeElement, previous);
            assert.equal(page.document.body.classList.contains('global-search-lock'), false);
        }
        assert.equal(page.input.focusCount, 2);
        assert.equal(previous.focusCount, 3);
    });
}

test('repeated initialization and keydown cause no duplicate focus or fetch', async () => {
    const page = fixture();
    page.load('static/js/app.js');
    page.key({ ctrlKey: true, code: 'KeyK', key: 'л' });
    page.key({ ctrlKey: true, code: 'KeyK', key: 'л', repeat: true });
    page.key({ ctrlKey: true, code: 'KeyK', key: 'k' });
    page.flushTimers();
    assert.equal(page.input.focusCount, 1);
    assert.equal(page.requests.length, 0);
    page.input.value = 'Иванов';
    page.key({ code: 'Enter', key: 'Enter' });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(page.requests.length, 1);
    assert.equal(new URL(page.requests[0].url).searchParams.get('q'), 'Иванов');
    assert.equal(page.input.focusCount, 1);
});

test('topbar and sidebar clicks open, close button restores focus, disconnected origin falls back', () => {
    const page = fixture();
    for (const opener of [page.opener, page.toggle]) {
        opener.focus();
        page.click(opener);
        assert.equal(page.modal.hidden, false);
        page.click(page.close);
        page.flushTimers();
        assert.equal(page.modal.hidden, true);
        assert.equal(page.document.activeElement, opener);
    }
    const removedInput = page.origin('input');
    removedInput.focus();
    page.key({ ctrlKey: true, code: 'KeyK', key: 'k' });
    removedInput.isConnected = false;
    page.key({ key: 'Escape' });
    assert.equal(page.document.activeElement, page.opener);
});

for (const docsFirst of [true, false]) {
    test(`documentation shares the global shortcut (docs loaded ${docsFirst ? 'first' : 'last'})`, () => {
        const page = fixture({ documentation: true, docsFirst });
        page.docsInput.value = 'кампании';
        page.docsInput.focus();
        page.click(page.docsMenu);
        for (const key of ['k', 'л']) {
            page.key({ ctrlKey: true, code: 'KeyK', key });
            page.flushTimers();
            assert.equal(page.modal.hidden, false);
            assert.equal(page.document.activeElement, page.input);
            assert.equal(page.docsInput.focusCount, key === 'k' ? 1 : 2);
            page.key({ key: 'Escape', code: 'Escape' });
            assert.equal(page.document.activeElement, page.docsInput);
            assert.equal(page.docsInput.value, 'кампании');
            assert.equal(page.docs.classList.contains('is-menu-open'), true);
        }
        assert.equal(page.requests.length, 0);
        page.docsInput.value = 'не существующий раздел';
        page.docsInput.dispatchEvent(new Event('input'));
        assert.equal(page.docsSection.classList.contains('docs-search-hidden'), true);
        page.key({ key: 'Escape' });
        assert.equal(page.docsInput.value, '');
        assert.equal(page.docsSection.classList.contains('docs-search-hidden'), false);
    });
}
