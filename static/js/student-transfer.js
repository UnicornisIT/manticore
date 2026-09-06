(() => {
  const form = document.querySelector('.student-transfer-form');
  if (!form) return;
  const select = form.querySelector('select');
  const button = form.querySelector('[type="submit"]');
  const error = document.getElementById('transfer-error');
  const preview = document.getElementById('transfer-capacity-preview');
  let busy = false;
  select.addEventListener('change', () => {
    const option = select.selectedOptions[0];
    const count = Number(option.dataset.count), capacity = Number(option.dataset.capacity);
    preview.textContent = select.value ? `${option.textContent.trim()}. После перевода: ${count + 1}/${capacity}.` : '';
    if (count >= capacity) preview.textContent += ' Требуется явное подтверждение переполнения.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    button.disabled = true;
    error.hidden = true;
    const data = new FormData(form);
    data.set('expected_source', form.dataset.source);
    data.set('preview', 'true');
    try {
      if (!window.ManticoreConfirm) throw new Error('Диалог подтверждения недоступен. Обновите страницу.');
      while (true) {
        const response = await fetch(form.action, {
          method: 'POST', body: data, headers: { Accept: 'application/json' }
        });
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('Не удалось выполнить перевод. Обновите страницу и проверьте вход в систему.');
        }
        const result = await response.json();
        if (result.redirect && response.ok) { window.location.assign(result.redirect); return; }
        if (!response.ok && result.code !== 'capacity_exceeded') throw new Error(result.message);
        // Both initial preview and a newly occupied last seat require a fresh dialog.
        const override = result.code === 'capacity_exceeded';
        const confirmed = await window.ManticoreConfirm(
          `${result.message} Продолжить перевод?`, override ? 'Перевести всё равно' : 'Перевести'
        );
        if (!confirmed) return;
        data.delete('preview');
        data.set('capacity_override', override ? 'true' : 'false');
      }
    } catch (exception) {
      error.textContent = exception.message || 'Не удалось выполнить перевод.';
      error.hidden = false;
    } finally {
      busy = false;
      button.disabled = false;
      button.focus();
      button.removeAttribute('aria-busy');
      button.classList.remove('is-busy');
    }
  });
})();
