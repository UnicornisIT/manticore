(() => {
  const root = document.getElementById('roster-groups');
  if (!root) return;

  const search = document.getElementById('roster-search');
  const email = document.getElementById('roster-email-filter');
  const group = document.getElementById('roster-group-filter');
  const status = document.getElementById('roster-status-filter');
  const counter = document.getElementById('roster-result-count');
  const chips = document.getElementById('active-filter-chips');
  const exportLink = document.getElementById('roster-filtered-export');
  const baseExportUrl = exportLink.href;
  const state = { sort: '', direction: 'asc' };

  const normalize = value => String(value || '').trim().toLocaleLowerCase('ru');
  const selectedGroups = () => Array.from(group.selectedOptions, option => option.value);
  const params = () => {
    const value = new URLSearchParams();
    if (search.value.trim()) value.set('search', search.value.trim());
    if (email.value) value.set('email', email.value);
    selectedGroups().forEach(item => value.append('groups', item));
    if (status.value.trim()) value.set('status', status.value.trim());
    if (state.sort) { value.set('sort', state.sort); value.set('direction', state.direction); }
    return value;
  };

  function apply() {
    const needle = normalize(search.value);
    const statusNeedle = normalize(status.value);
    const groups = selectedGroups().map(normalize);
    let visible = 0;
    root.querySelectorAll('.roster-group').forEach(section => {
      let sectionCount = 0;
      section.querySelectorAll('[data-roster-row]').forEach(row => {
        const matchesSearch = !needle || normalize(Object.values(row.dataset).join(' ')).includes(needle);
        const matchesEmail = !email.value || (email.value === '__empty__' ? !row.dataset.email.trim() : !!row.dataset.email.trim());
        const matchesGroup = !groups.length || groups.includes(normalize(row.dataset.group));
        const matchesStatus = !statusNeedle || normalize(row.dataset.status).includes(statusNeedle);
        row.hidden = !(matchesSearch && matchesEmail && matchesGroup && matchesStatus);
        if (!row.hidden) { visible += 1; sectionCount += 1; }
      });
      section.hidden = sectionCount === 0;
      const label = section.querySelector('.roster-group-counts');
      if (label) label.textContent = `${sectionCount} чел.`;
    });
    counter.textContent = `Показано ${visible} из ${root.dataset.total}`;
    exportLink.href = `${baseExportUrl}?${params().toString()}`;
    exportLink.setAttribute('aria-disabled', String(visible === 0));
    exportLink.classList.toggle('is-disabled', visible === 0);
    const values = [];
    if (search.value.trim()) values.push(`Поиск: ${search.value.trim()}`);
    if (email.value) values.push(`Email: ${email.options[email.selectedIndex].text}`);
    if (groups.length) values.push(`Группы: ${selectedGroups().join(', ')}`);
    if (status.value.trim()) values.push(`Статус: ${status.value.trim()}`);
    chips.replaceChildren(...values.map(text => { const item = document.createElement('span'); item.className = 'status-badge status-info'; item.textContent = text; return item; }));
  }

  [search, email, group, status].forEach(control => control.addEventListener(control.tagName === 'SELECT' ? 'change' : 'input', apply));
  document.getElementById('roster-reset').addEventListener('click', () => {
    search.value = ''; email.value = ''; status.value = '';
    Array.from(group.options).forEach(option => { option.selected = false; });
    apply(); search.focus();
  });
  exportLink.addEventListener('click', event => { if (exportLink.classList.contains('is-disabled')) event.preventDefault(); });

  const headerKey = label => ({ 'Логин': 'login', 'Email': 'email', 'Фамилия': 'fio', 'Имя': 'fio', 'Группа': 'group', 'Глобальная группа курса': 'cohort2', 'Специальность': 'specialty', 'Договор': 'dogovor', 'Состояние': 'status' })[label];
  const firstHeaders = Array.from(root.querySelector('[data-roster-table]').tHead.rows[0].cells);
  firstHeaders.forEach((firstHeader, index) => {
    const key = headerKey(firstHeader.textContent.trim());
    if (!key) return;
    const matchingHeaders = Array.from(root.querySelectorAll('[data-roster-table]')).map(table => table.tHead.rows[0].cells[index]).filter(Boolean);
    matchingHeaders.forEach(header => { header.tabIndex = 0; header.classList.add('sortable'); header.setAttribute('role', 'button'); });
    const sort = () => {
      state.direction = state.sort === key && state.direction === 'asc' ? 'desc' : 'asc'; state.sort = key;
      root.querySelectorAll('[data-roster-table] tbody').forEach(body => {
        Array.from(body.rows).sort((a, b) => normalize(a.dataset[key]).localeCompare(normalize(b.dataset[key]), 'ru', { numeric: true }) * (state.direction === 'asc' ? 1 : -1)).forEach(row => body.appendChild(row));
      });
      root.querySelectorAll('th[aria-sort]').forEach(item => item.removeAttribute('aria-sort'));
      root.querySelectorAll('[data-roster-table] thead').forEach(head => head.children[0].children[index]?.setAttribute('aria-sort', state.direction === 'asc' ? 'ascending' : 'descending'));
      apply();
    };
    matchingHeaders.forEach(header => {
      header.addEventListener('click', sort);
      header.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sort(); } });
    });
  });

  const chooser = document.getElementById('column-chooser');
  firstHeaders.forEach((header, index) => {
    const label = document.createElement('label'); const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = true; checkbox.dataset.column = index;
    label.append(checkbox, document.createTextNode(header.textContent.trim())); chooser.appendChild(label);
    checkbox.addEventListener('change', () => root.querySelectorAll('[data-roster-table]').forEach(table => Array.from(table.rows).forEach(row => { if (row.cells[index]) row.cells[index].hidden = !checkbox.checked; })));
  });

  const modal = document.getElementById('issue-modal');
  const modalBody = document.getElementById('issue-table-body');
  const issueSearch = document.getElementById('issue-search');
  let issueRows = []; let opener = null;
  function renderIssueRows() {
    const needle = normalize(issueSearch.value);
    modalBody.replaceChildren(...issueRows.filter(row => !needle || normalize(Object.values(row).join(' ')).includes(needle)).map(row => {
      const tr = document.createElement('tr');
      ['fio', 'dogovor', 'login', 'email', 'group_name', 'specialty', 'status', 'issues_text'].forEach(key => {
        const td = document.createElement('td');
        td.textContent = key === 'specialty' && row.suggested_abiturient_specialty
          ? `В приказе: ${row.specialty || '—'}; в Manticore: ${row.suggested_abiturient_specialty}`
          : row[key] || '—';
        tr.appendChild(td);
      });
      return tr;
    }));
  }
  function closeModal() { modal.hidden = true; document.body.classList.remove('modal-lock'); opener?.focus(); }
  document.querySelectorAll('[data-issue-close]').forEach(button => button.addEventListener('click', closeModal));
  issueSearch.addEventListener('input', renderIssueRows);
  document.addEventListener('keydown', event => {
    if (modal.hidden) return;
    if (event.key === 'Escape') { closeModal(); return; }
    if (event.key === 'Tab') {
      const focusable = Array.from(modal.querySelectorAll('a[href],button:not(:disabled),input:not(:disabled)'));
      if (!focusable.length) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  document.querySelectorAll('[data-issue-url]').forEach(button => button.addEventListener('click', async () => {
    opener = button; modal.hidden = false; document.body.classList.add('modal-lock'); issueSearch.value = '';
    document.getElementById('issue-title').textContent = 'Загрузка…'; modalBody.replaceChildren();
    try {
      const response = await fetch(button.dataset.issueUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Не удалось загрузить выборку');
      const payload = await response.json(); issueRows = payload.rows;
      document.getElementById('issue-title').textContent = payload.label;
      document.getElementById('issue-meta').textContent = `${payload.count} абитуриентов`;
      document.getElementById('issue-description').textContent = payload.description;
      document.getElementById('issue-download').href = payload.download_url;
      renderIssueRows(); issueSearch.focus();
    } catch (error) { document.getElementById('issue-title').textContent = 'Ошибка'; document.getElementById('issue-description').textContent = error.message; }
  }));
  apply();
})();
