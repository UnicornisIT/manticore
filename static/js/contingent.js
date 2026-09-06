(() => {
  const root = document.querySelector('[data-contingent-results]');
  if (!root) return;
  const rows = Array.from(root.querySelectorAll('[data-result-row]'));
  const search = root.querySelector('[data-result-search]');
  const discrepancies = root.querySelector('[data-discrepancies]');
  const filters = new Map();
  const normalize = value => String(value || '').trim().toLocaleLowerCase('ru');
  function apply() {
    rows.forEach(row => {
      row.hidden = !(normalize(row.textContent).includes(normalize(search.value)) &&
        (!discrepancies.checked || row.dataset.status !== 'MATCHED') &&
        Array.from(filters).every(([key, select]) => !select.value || row.dataset[key] === select.value));
    });
    root.querySelector('[data-visible-count]').textContent = `Показано ${rows.filter(row => !row.hidden).length} из ${rows.length}`;
    root.querySelector('[data-table-selection]').dispatchEvent(new Event('table-selection:refresh'));
  }
  root.querySelectorAll('[data-filter-key]').forEach(header => {
    const key = header.dataset.filterKey;
    const label = document.createElement('label');
    label.append(document.createTextNode(header.textContent + ' '));
    const select = document.createElement('select');
    select.add(new Option('Все', ''));
    [...new Set(rows.map(row => row.dataset[key]))].filter(Boolean).sort().forEach(value => {
      const text = key === 'status' ? rows.find(row => row.dataset.status === value).querySelector('.status-badge').textContent : value;
      select.add(new Option(text, value));
    });
    select.addEventListener('change', apply);
    label.append(select); root.querySelector('[data-result-filters]').append(label); filters.set(key, select);
  });
  search.addEventListener('input', apply);
  discrepancies.addEventListener('change', apply);
  root.querySelectorAll('[data-status-filter]').forEach(button => button.addEventListener('click', () => {
    filters.get('status').value = button.dataset.statusFilter; apply();
    root.querySelector('[data-result-table]').scrollIntoView({block: 'nearest'});
  }));
  root.querySelector('[data-reset-filters]').addEventListener('click', () => {
    filters.forEach(select => { select.value = ''; }); search.value = ''; discrepancies.checked = false; apply();
  });
  apply();
})();
