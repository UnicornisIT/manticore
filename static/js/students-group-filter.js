(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-group-filter]').forEach(root => {
      const search = root.querySelector('[data-group-search]');
      const options = Array.from(root.querySelectorAll('[data-group-option]'));
      const boxes = options.map(option => option.querySelector('input'));
      const summary = root.querySelector('[data-group-summary]');
      const updateSummary = () => {
        const count = boxes.filter(box => box.checked).length;
        summary.textContent = count ? `Группы: ${count}` : 'Все группы';
      };
      search.addEventListener('input', () => {
        const query = search.value.trim().toLocaleLowerCase('ru');
        options.forEach(option => { option.hidden = !option.textContent.toLocaleLowerCase('ru').includes(query); });
        root.querySelector('[data-group-empty]').hidden = options.some(option => !option.hidden);
      });
      root.addEventListener('change', updateSummary);
      const notify = () => {
        updateSummary();
        // Send one change after the batch so list and export update together.
        boxes[0]?.dispatchEvent(new Event('change', { bubbles: true }));
      };
      root.querySelector('[data-group-select]').addEventListener('click', () => {
        options.forEach((option, index) => { if (!option.hidden) boxes[index].checked = true; });
        notify();
      });
      root.querySelector('[data-group-clear]').addEventListener('click', () => {
        boxes.forEach(box => { box.checked = false; });
        notify();
      });
      root.addEventListener('keydown', event => {
        if (event.key === 'Escape') { root.open = false; root.querySelector('summary').focus(); }
        if (event.key === 'Enter' && event.target === search) event.preventDefault();
      });
      document.addEventListener('click', event => { if (!root.contains(event.target)) root.open = false; });
    });
  });
})();
