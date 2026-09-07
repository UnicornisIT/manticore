const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/desktop-updater.js', 'utf8');

function fixture(initial) {
  const notes = {hidden:true, dataset:{}, innerHTML:''};
  const nodes = {};
  function element(selector) {
    return nodes[selector] = {hidden:false, disabled:false, textContent:'', events:{}, parentElement:{querySelector:()=>notes},
      addEventListener(name, handler) {this.events[name] = handler;},
      cloneNode() {return element(selector);}, replaceWith() {}};
  }
  ['[data-check-update]', '[data-install-update]', '[data-update-status]', '[data-update-badge]', '[data-update-progress]', '[data-update-channel]'].forEach(element);
  const root = {querySelector: selector => nodes[selector]};
  const calls = [];
  let state = initial;
  const api = {get_update_status: async () => state};
  for (const method of ['check_for_update', 'download_update', 'install_approved_update']) {
    api[method] = async () => {calls.push(method); return state;};
  }
  const callbacks = [];
  const window = {pywebview:{api}, addEventListener() {}};
  const context = {window, document:{querySelector:s => s === '[data-desktop-settings]' ? root : null}, setTimeout: fn => {callbacks.push(fn); return callbacks.length;}, clearTimeout() {}};
  vm.runInNewContext(source, context);
  return {notes, nodes, calls, callbacks, setState: value => {state = value;}, api};
}

test('updater renders availability, progress and a separate install action', async () => {
  const f = fixture({state:'available', current_version:'1.0.0', version:'1.5.0', notes:'<img onerror=bad>'});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.nodes['[data-install-update]'].hidden, false);
  assert.equal(f.nodes['[data-install-update]'].textContent, 'Скачать обновление');
  assert.doesNotMatch(f.nodes['[data-update-status]'].textContent, /<img/);
  assert.match(f.notes.innerHTML, /&lt;img onerror=bad&gt;/);
  f.setState({state:'downloading', percent:37, downloaded:37, total:100});
  await f.nodes['[data-install-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  assert.deepEqual(f.calls, ['download_update']);
  assert.equal(f.nodes['[data-check-update]'].disabled, true);
  assert.equal(f.nodes['[data-update-progress]'].value, 37);
  f.setState({state:'downloaded', version:'1.5.0', percent:100});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.nodes['[data-install-update]'].textContent, 'Перезапустить и установить');
  await f.nodes['[data-install-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  assert.deepEqual(f.calls, ['download_update', 'install_approved_update']);
});

test('development disables updates and IPC rejection restores manual check', async () => {
  const f = fixture({state:'disabled'});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.nodes['[data-check-update]'].disabled, true);
  assert.equal(f.nodes['[data-install-update]'].hidden, true);
  f.api.check_for_update = async () => {throw Error('IPC gone');};
  await f.nodes['[data-check-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  assert.equal(f.nodes['[data-check-update]'].disabled, false);
  assert.match(f.nodes['[data-update-status]'].textContent, /Не удалось/);
});

test('updater shows the installed client channel', async () => {
  for (const [channel, label] of [['preview', /Предварительный канал/], ['stable', /Стабильный канал/]]) {
    const f = fixture({state:'idle', current_version:'0.0.3-alpha', channel});
    await f.callbacks.shift()(); await new Promise(setImmediate);
    assert.match(f.nodes['[data-update-channel]'].textContent, label);
  }
});

function channelFixture(initial) {
  const cards = {};
  for (const channel of ['stable', 'preview']) {
    const notes = {hidden:true, dataset:{}, innerHTML:''};
  const nodes = {};
    function element(selector) {
      return nodes[selector] = {hidden:false, disabled:false, textContent:'', events:{}, parentElement:{querySelector:()=>notes},
        addEventListener(name, handler) {this.events[name] = handler;},
        cloneNode() {return element(selector);}, replaceWith() {}};
    }
    for (const selector of ['h3', '.settings-card-heading p', '[data-update-channel]', '[data-check-update]', '[data-install-update]', '[data-update-status]', '[data-update-badge]', '[data-update-progress]']) element(selector);
    cards[channel] = {notes, nodes, querySelector: selector => nodes[selector]};
  }
  const root = {querySelector: selector => selector === '[data-update-card="stable"]' ? cards.stable : selector === '[data-update-card="preview"]' ? cards.preview : cards.stable.nodes[selector]};
  let states = initial;
  const calls = [], callbacks = [];
  const api = {get_update_status:async()=>states.preview, get_update_channels:async()=>states};
  for (const method of ['check_for_update', 'download_update', 'install_approved_update']) {
    api[method] = async channel => {calls.push([method, channel]); return states[channel];};
  }
  const context = {window:{pywebview:{api},addEventListener(){}}, document:{querySelector:()=>root}, setTimeout:fn=>{callbacks.push(fn);return callbacks.length;}, clearTimeout(){}};
  vm.runInNewContext(source, context);
  return {cards, calls, callbacks, api, setState:value=>{states=value;}};
}

test('separate cards route check, download and install to their own channel', async () => {
  const states = {stable:{state:'current',current_version:'0.0.5-alpha'}, preview:{state:'available',current_version:'0.0.5-alpha',version:'0.0.6-alpha'}};
  const f = channelFixture(states);
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.cards.stable.nodes['[data-install-update]'].hidden, true);
  assert.match(f.cards.stable.nodes['[data-update-status]'].textContent, /Новых стабильных обновлений нет/);
  assert.equal(f.cards.preview.nodes['[data-install-update]'].hidden, false);
  await f.cards.stable.nodes['[data-check-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  await f.cards.preview.nodes['[data-install-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  states.preview = {...states.preview,state:'downloaded'};
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.cards.preview.nodes['[data-install-update]'].textContent, 'Перезапустить и установить');
  await f.cards.preview.nodes['[data-install-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  assert.deepEqual(f.calls, [['check_for_update','stable'],['download_update','preview'],['install_approved_update','preview']]);
});

test('installation disables actions in both cards and IPC errors allow retry', async () => {
  const f = channelFixture({stable:{state:'available'},preview:{state:'installing'}});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.cards.stable.nodes['[data-install-update]'].disabled, true);
  assert.equal(f.cards.stable.nodes['[data-check-update]'].disabled, true);
  f.setState({stable:{state:'current'},preview:{state:'available'}});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  f.api.check_for_update=async()=>{throw Error('IPC gone');};
  await f.cards.stable.nodes['[data-check-update]'].events.click({stopImmediatePropagation(){}}); await new Promise(setImmediate);
  assert.equal(f.cards.stable.nodes['[data-check-update]'].disabled, false);
  assert.match(f.cards.stable.nodes['[data-update-status]'].textContent, /Повторите попытку/);
});


test('release notes render Markdown blocks and inline formatting separately from status', async () => {
  const notes = '# Release\n\n## Changes\n\n- **Bold** and *italic*\n- `code` and ~~removed~~\n\n1. First\n2. Second\n\n> Quote\n\n```js\nconst value = "<test>";\n```\n\n[Details](https://example.test/release)';
  const f = fixture({state:'available',version:'1.5.0',notes});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  for (const html of ['<h3>Release</h3>', '<h4>Changes</h4>', '<ul>', '<ol start="1">', '<strong>Bold</strong>', '<em>italic</em>', '<code>code</code>', '<del>removed</del>', '<blockquote>', '<pre><code>', 'rel="noopener noreferrer"']) assert.ok(f.notes.innerHTML.includes(html), html);
  assert.equal(f.notes.hidden, false);
  assert.doesNotMatch(f.nodes['[data-update-status]'].textContent, /Changes/);
  f.setState({state:'checking',version:'',notes:''});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.notes.hidden, true);
});

test('release notes escape HTML, reject executable links and isolate both channels', async () => {
  const malicious = '<script>alert(1)</script>\n\n[x](javascript:alert(1)) [x](data:text/html,bad) [safe](https://example.test/"onclick="bad)';
  const f = channelFixture({stable:{state:'available',version:'1.0.0',notes:'## Stable'},preview:{state:'available',version:'1.1.0-alpha',notes:malicious}});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.match(f.cards.stable.notes.innerHTML, /<h4>Stable<\/h4>/);
  const html = f.cards.preview.notes.innerHTML;
  assert.doesNotMatch(html, /<script|href="javascript:|href="data:|"onclick="/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&quot;onclick=&quot;/);
});
