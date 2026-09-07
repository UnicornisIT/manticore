(function () {
  'use strict';
  if (window.manticoreUpdaterInitialized) return;
  window.manticoreUpdaterInitialized = true;
  let api, state, timer, started = false, notified = '', ticks = 0;
  const root = document.querySelector('[data-desktop-settings]');
  const status = root?.querySelector('[data-update-status]');
  const badge = root?.querySelector('[data-update-badge]');
  const progress = root?.querySelector('[data-update-progress]');
  const channel = root?.querySelector('[data-update-channel]') || badge?.closest('section')?.querySelector('.settings-card-heading p');
  // Bundled styles also work when the remote server still uses an older template.
  if (document.head) {
    const style = document.createElement('style');
    style.textContent = `
      .release-notes { margin-top:16px; padding:16px; border:1px solid var(--border, #ccd3dc); border-radius:10px; background:var(--bg-subtle, Canvas); color:var(--text-primary, CanvasText); font-size:14px; line-height:1.65; max-height:360px; overflow:auto; overflow-wrap:anywhere; }
      .release-notes[hidden] { display:none !important; }
      .release-notes h3,.release-notes h4,.release-notes h5,.release-notes h6 { margin:18px 0 8px; line-height:1.35; font-weight:650; }
      .release-notes h3 { font-size:18px; } .release-notes h4 { font-size:16px; }
      .release-notes h5,.release-notes h6 { font-size:14px; }
      .release-notes p { margin:8px 0; } .release-notes > :first-child { margin-top:0; } .release-notes > :last-child { margin-bottom:0; }
      .release-notes ul,.release-notes ol { display:block; padding:0 0 0 24px; margin:8px 0; text-align:left; }
      .release-notes ul { list-style:disc outside; } .release-notes ol { list-style:decimal outside; }
      .release-notes li { display:list-item; float:none; padding:0; margin:5px 0; text-align:left; } .release-notes li > p { margin:0; }
      .release-notes a { color:var(--accent, #315de6); text-decoration:underline; text-underline-offset:3px; }
      .release-notes code { padding:2px 5px; border-radius:4px; background:var(--bg-canvas, Canvas); font-size:.9em; }
      .release-notes pre { padding:12px; overflow:auto; border-radius:6px; background:var(--bg-canvas, Canvas); white-space:pre; }
      .release-notes pre code { padding:0; } .release-notes blockquote { margin:12px 0; padding-left:12px; border-left:3px solid var(--accent, #315de6); color:var(--text-secondary, CanvasText); }
      .release-notes hr { border:0; border-top:1px solid var(--border, #ccd3dc); margin:16px 0; }
    `;
    document.head.append(style);
  }
  // Generate only our own HTML tags; release text and attributes are always escaped.
  function markdown(text) {
    const escape = value => value.replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
    function inline(value, depth = 0) {
      if (depth > 8) return escape(value);
      const tokens = /`([^`\n]+)`|\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*([^\n]+?)\*\*|__([^\n]+?)__|~~([^\n]+?)~~|\*([^*\n]+)\*|_([^_\n]+)_/g;
      let result = '', offset = 0;
      for (const match of value.matchAll(tokens)) {
        result += escape(value.slice(offset, match.index));
        if (match[1] !== undefined) result += '<code>' + escape(match[1]) + '</code>';
        else if (match[2] !== undefined) {
          // Never navigate the privileged desktop renderer to an untrusted URL.
          const url = match[3];
          result += /^https?:\/\//i.test(url)
            ? '<a href="' + escape(url) + '" target="_blank" rel="noopener noreferrer">' + inline(match[2], depth + 1) + '</a>'
            : escape(match[0]);
        } else {
          const tag = match[4] !== undefined || match[5] !== undefined ? 'strong' : match[6] !== undefined ? 'del' : 'em';
          result += '<' + tag + '>' + inline(match[4] ?? match[5] ?? match[6] ?? match[7] ?? match[8], depth + 1) + '</' + tag + '>';
        }
        offset = match.index + match[0].length;
      }
      return result + escape(value.slice(offset));
    }
    function blocks(lines, depth = 0) {
      if (depth > 8) return '<p>' + inline(lines.join('\n')) + '</p>';
      let result = '';
      const list = line => /^(\s*)([-+*]|\d+[.)])\s+(.+)$/.exec(line);
      const special = line => /^\s*$|^ {0,3}(#{1,6}\s|```|~~~|>|(?:---+|\*\*\*+)\s*$)/.test(line) || list(line);
      for (let i = 0; i < lines.length;) {
        const line = lines[i];
        if (!line.trim()) { i++; continue; }
        const fence = /^ {0,3}(`{3,}|~{3,})/.exec(line);
        if (fence) {
          const content = []; i++;
          while (i < lines.length && !lines[i].trim().startsWith(fence[1])) content.push(lines[i++]);
          if (i < lines.length) i++;
          result += '<pre><code>' + escape(content.join('\n')) + '</code></pre>'; continue;
        }
        const heading = /^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
        if (heading) { const level = Math.min(heading[1].length + 2, 6); result += `<h${level}>` + inline(heading[2]) + `</h${level}>`; i++; continue; }
        if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { result += '<hr>'; i++; continue; }
        if (/^ {0,3}>/.test(line)) {
          const quoted = [];
          while (i < lines.length && /^ {0,3}>/.test(lines[i])) quoted.push(lines[i++].replace(/^ {0,3}> ?/, ''));
          result += '<blockquote>' + blocks(quoted, depth + 1) + '</blockquote>'; continue;
        }
        const item = list(line);
        if (item) {
          const ordered = /^\d/.test(item[2]), tag = ordered ? 'ol' : 'ul', indent = item[1].length;
          result += '<' + tag + (ordered ? ' start="' + parseInt(item[2], 10) + '"' : '') + '>';
          while (i < lines.length) {
            const next = list(lines[i]);
            if (!next || next[1].length !== indent || /^\d/.test(next[2]) !== ordered) break;
            const content = [next[3]]; i++;
            while (i < lines.length && lines[i].trim() && /^\s+/.test(lines[i]) && lines[i].search(/\S/) > indent) content.push(lines[i++].slice(indent + 2));
            result += '<li>' + blocks(content, depth + 1) + '</li>';
          }
          result += '</' + tag + '>'; continue;
        }
        const paragraph = [line]; i++;
        while (i < lines.length && !special(lines[i])) paragraph.push(lines[i++]);
        result += '<p>' + inline(paragraph.join('\n')).replace(/ {2}\n/g, '<br>') + '</p>';
      }
      return result;
    }
    return blocks(String(text).slice(0, 4000).replace(/\r\n?/g, '\n').split('\n'));
  }
  function releaseNotes(anchor, value) {
    if (!anchor) return;
    let panel = anchor.parentElement?.querySelector('[data-release-notes]');
    if (!panel && anchor.after) {
      panel = document.createElement('section');
      panel.className = 'release-notes'; panel.setAttribute('data-release-notes', '');
      panel.setAttribute('aria-label', 'Описание релиза');
      panel.tabIndex = 0;
      anchor.after(panel);
    }
    if (!panel) return;
    const text = value.version ? String(value.notes || '').trim() : '';
    panel.hidden = !text;
    if (panel.dataset.markdown !== text) {
      panel.innerHTML = markdown(text);
      panel.dataset.markdown = text;
    }
  }
  // Remove handlers from older server templates: the bundled desktop UI owns these actions.
  function button(selector) {
    const old = root?.querySelector(selector);
    if (!old) return null;
    const next = old.cloneNode(true); old.replaceWith(next); return next;
  }
  let check, action;
  const labels = {idle:'Не проверено', disabled:'Desktop-сборка', checking:'Проверяем…', current:'Актуальная версия', available:'Доступно обновление', downloading:'Скачивание…', downloaded:'Готово к установке', installing:'Установка…', error:'Ошибка'};
  function render(value) {
    state = value;
    if (channel) channel.textContent = value.channel === 'preview'
      ? 'Предварительный канал: alpha, beta, rc и стабильные версии из официального GitHub Releases.'
      : 'Стабильный канал: стабильные версии из официального GitHub Releases.';
    const busy = ['checking', 'downloading', 'installing'].includes(value.state);
    if (check) check.disabled = busy || ['disabled', 'downloaded'].includes(value.state);
    if (action) {
      action.hidden = !['available', 'downloading', 'downloaded', 'installing'].includes(value.state);
      action.style?.setProperty('display', action.hidden ? 'none' : '');
      action.disabled = busy;
      action.textContent = value.state === 'downloaded' ? 'Перезапустить и установить' : value.state === 'downloading' ? 'Скачивание…' : value.state === 'installing' ? 'Установка…' : 'Скачать обновление';
    }
    if (badge) { badge.textContent = labels[value.state] || value.state; badge.className = 'status-pill ' + (value.state === 'error' ? 'status-danger' : 'status-info'); }
    const messages = {
      idle: `Текущая версия: ${value.current_version}.`,
      disabled: 'Обновления доступны в установленной Desktop-версии приложения.',
      checking: `Текущая версия: ${value.current_version}. Проверяем GitHub Releases…`,
      current: `Установлена актуальная версия: ${value.current_version}.`,
      available: `Текущая версия: ${value.current_version}. Доступна версия ${value.version}.`,
      downloading: `Скачивание обновления: ${value.percent}% — ${(value.downloaded / 1048576).toFixed(1)} МБ из ${(value.total / 1048576).toFixed(1)} МБ.`,
      downloaded: `Версия ${value.version} загружена и готова к установке.`,
      installing: 'Подтвердите установку в окне Manticore. После подтверждения приложение закроется и перезапустится.',
      error: `Не удалось выполнить обновление. ${value.error || 'Повторите проверку.'}`
    };
    if (status) status.textContent = messages[value.state] || '';
    releaseNotes(status, value);
    if (progress) { progress.hidden = value.state !== 'downloading'; progress.value = value.percent; }
    if (value.state === 'available' && notified !== value.version && !root) {
      notified = value.version;
      const notice = document.createElement('aside');
      notice.className = 'settings-card desktop-update-notice'; notice.setAttribute('role', 'status');
      Object.assign(notice.style, {position:'fixed', bottom:'20px', right:'20px', zIndex:'10000', maxWidth:'420px', padding:'18px', background:'Canvas', color:'CanvasText', border:'1px solid ButtonBorder', borderRadius:'12px'});
      const text = document.createElement('p'); text.textContent = `Доступно обновление Manticore ${value.version}`;
      const details = document.createElement('button'); details.className = 'btn btn-secondary'; details.textContent = 'Скачать обновление';
      const close = document.createElement('button'); close.className = 'btn btn-secondary'; close.textContent = 'Позже'; close.addEventListener('click', () => notice.remove());
      notice.append(text, details, close); document.body.append(notice);
    }
    // A bundled notification works even with an older remote Manticore server.
    const notice = document.querySelector('.desktop-update-notice');
    if (notice && !root) {
      notice.querySelector('p').textContent = messages[value.state];
      releaseNotes(notice.querySelector('p'), value);
      const next = notice.querySelector('button');
      next.disabled = busy;
      next.textContent = value.state === 'downloaded' ? 'Перезапустить и установить' : value.state === 'error' ? 'Повторить проверку' : 'Скачать обновление';
      next.onclick = () => invoke(value.state === 'downloaded' ? 'install_approved_update' : value.state === 'error' ? 'check_for_update' : 'download_update');
    }
    return busy;
  }
  function schedule(delay = 750) { clearTimeout(timer); timer = setTimeout(poll, delay); }
  async function poll() {
    try {
      const busy = render(await api.get_update_status());
      ticks++;
      // Polls only local IPC, never GitHub. Idle polling stops after startup check.
      if (busy || ticks < 20) schedule();
    } catch (_) { if (status) status.textContent = 'Связь с Desktop-клиентом прервана. Перезапустите приложение.'; if (check) check.disabled = false; }
  }
  async function invoke(method) {
    if (!api) return;
    if (check) check.disabled = true;
    if (action) action.disabled = true;
    try { render(await api[method]()); schedule(); }
    catch (_) { if (status) status.textContent = 'Не удалось выполнить действие. Повторите попытку.'; if (check) check.disabled = false; if (action) action.disabled = false; }
  }
  function startChannels() {
    if (!root || !api.get_update_channels) return false;
    let stable = root.querySelector('[data-update-card="stable"]');
    let preview = root.querySelector('[data-update-card="preview"]');
    // Upgrade an older server's single card using the UI bundled with this client.
    if (!stable) {
      stable = badge?.closest('section');
      if (!stable) return false;
      stable.setAttribute('data-update-card', 'stable');
    }
    if (!preview) {
      preview = stable.cloneNode(true);
      preview.setAttribute('data-update-card', 'preview');
      stable.after(preview);
    }
    const cards = {};
    const titles = {stable:'Стабильные обновления', preview:'Тестовые обновления'};
    const descriptions = {stable:'Стабильные версии из официального GitHub Releases.', preview:'Предварительные версии из GitHub Releases. Могут содержать ошибки.'};
    let values = {}, pending = false, pollingTimer, attempts = 0;
    for (const [name, card] of [['stable', stable], ['preview', preview]]) {
      card.hidden = false;
      card.querySelector('h3').textContent = titles[name];
      card.querySelector('.settings-card-heading p').textContent = descriptions[name];
      const nodes = {};
      for (const key of ['check-update', 'install-update', 'update-status', 'update-badge', 'update-progress']) {
        let node = card.querySelector(`[data-${key}]`);
        if (key === 'check-update' || key === 'install-update') {
          const clean = node.cloneNode(true); node.replaceWith(clean); node = clean;
        }
        nodes[key] = node;
      }
      cards[name] = nodes;
      nodes['check-update'].addEventListener('click', event => {
        event.stopImmediatePropagation(); act(name, 'check_for_update');
      }, true);
      nodes['install-update'].addEventListener('click', event => {
        event.stopImmediatePropagation();
        act(name, values[name]?.state === 'downloaded' ? 'install_approved_update' : 'download_update');
      }, true);
    }
    function draw() {
      const installing = Object.values(values).some(value => value.state === 'installing');
      let busy = false;
      for (const [name, nodes] of Object.entries(cards)) {
        const value = values[name];
        if (!value) continue;
        const active = ['checking', 'downloading', 'installing'].includes(value.state);
        busy ||= active;
        nodes['check-update'].disabled = pending || installing || active || ['disabled', 'downloaded'].includes(value.state);
        const action = nodes['install-update'];
        action.hidden = !['available', 'downloading', 'downloaded', 'installing'].includes(value.state);
        action.style?.setProperty('display', action.hidden ? 'none' : '');
        action.disabled = pending || installing || active;
        action.textContent = value.state === 'downloaded' ? 'Перезапустить и установить' : value.state === 'downloading' ? 'Скачивание…' : value.state === 'installing' ? 'Установка…' : 'Скачать обновление';
        const badge = nodes['update-badge'];
        badge.textContent = value.state === 'current' ? 'Нет обновлений' : labels[value.state] || value.state;
        badge.className = 'status-pill ' + (value.state === 'error' ? 'status-danger' : 'status-info');
        const messages = {
          idle: `Текущая версия: ${value.current_version}.`,
          disabled: 'Обновления доступны в установленной Desktop-версии приложения.',
          checking: 'Проверяем GitHub Releases…',
          current: `Новых ${name === 'stable' ? 'стабильных' : 'тестовых'} обновлений нет. Текущая версия: ${value.current_version}.`,
          available: `Текущая версия: ${value.current_version}. Доступна версия ${value.version}.`,
          downloading: `Скачивание обновления: ${value.percent}% — ${(value.downloaded / 1048576).toFixed(1)} МБ из ${(value.total / 1048576).toFixed(1)} МБ.`,
          downloaded: `Версия ${value.version} загружена и готова к установке.`,
          installing: 'Подтвердите установку в окне Manticore. После подтверждения приложение перезапустится.',
          error: `Не удалось выполнить обновление. ${value.error || 'Повторите проверку.'}`
        };
        nodes['update-status'].textContent = messages[value.state] || '';
        releaseNotes(nodes['update-status'], value);
        nodes['update-progress'].hidden = value.state !== 'downloading';
        nodes['update-progress'].value = value.percent || 0;
      }
      return busy;
    }
    function scheduleChannels() { clearTimeout(pollingTimer); pollingTimer = setTimeout(pollChannels, 750); }
    async function pollChannels() {
      try {
        values = await api.get_update_channels();
        const busy = draw();
        if (busy || ++attempts < 20) scheduleChannels();
      } catch (_) {
        for (const nodes of Object.values(cards)) nodes['update-status'].textContent = 'Связь с Desktop-клиентом прервана. Повторите проверку.';
      }
    }
    async function act(name, method) {
      if (pending) return;
      pending = true; draw();
      let failed = false;
      try { values[name] = await api[method](name); }
      catch (_) { failed = true; }
      finally { pending = false; draw(); }
      if (failed) cards[name]['update-status'].textContent = 'Не удалось выполнить действие. Повторите попытку.';
      else scheduleChannels();
    }
    pollChannels();
    return true;
  }
  function start() {
    if (started || !window.pywebview?.api?.get_update_status) return;
    started = true; api = window.pywebview.api;
    if (startChannels()) return;
    // Older clients only support the automatic channel through the first card.
    const unsupported = root?.querySelector('[data-update-card="preview"]');
    if (unsupported) {
      root.querySelector('[data-update-card="stable"] h3').textContent = 'Обновления';
      unsupported.querySelector('[data-check-update]').disabled = true;
      unsupported.querySelector('[data-update-status]').textContent = 'Установите клиент 0.0.5 или новее для раздельной проверки каналов.';
    }
    check = button('[data-check-update]'); action = button('[data-install-update]');
    check?.addEventListener('click', event => { event.stopImmediatePropagation(); invoke('check_for_update'); }, true);
    action?.addEventListener('click', event => { event.stopImmediatePropagation(); invoke(state?.state === 'downloaded' ? 'install_approved_update' : 'download_update'); }, true);
    poll();
  }
  window.addEventListener('pywebviewready', start, {once:true});
  setTimeout(start, 150);
})();
