const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/desktop-updater.js', 'utf8');

function fixture(initial) {
  const nodes = {};
  function element(selector) {
    return nodes[selector] = {hidden:false, disabled:false, textContent:'', events:{},
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
  return {nodes, calls, callbacks, setState: value => {state = value;}, api};
}

test('updater renders availability, progress and a separate install action', async () => {
  const f = fixture({state:'available', current_version:'1.0.0', version:'1.5.0', notes:'<img onerror=bad>'});
  await f.callbacks.shift()(); await new Promise(setImmediate);
  assert.equal(f.nodes['[data-install-update]'].hidden, false);
  assert.equal(f.nodes['[data-install-update]'].textContent, 'Скачать обновление');
  assert.match(f.nodes['[data-update-status]'].textContent, /<img onerror=bad>/); // textContent, never executable HTML
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
