(() => {
  const form = document.querySelector('.student-transfer-form');
  if (!form) return;

  const campaignSelect = form.querySelector('#target_campaign_year');
  const groupSelect = form.querySelector('#new_cohort1');
  const button = form.querySelector('[type="submit"]');
  const error = document.getElementById('transfer-error');
  const capacityPreview = document.getElementById('transfer-capacity-preview');
  const cohort2Preview = document.getElementById('transfer_cohort2_preview');
  const groupStatus = form.querySelector('[data-group-status]');
  if (!campaignSelect || !groupSelect || !button) return;

  let busy = false;
  let loadSequence = 0;

  function replaceGroupOptions(message) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = message;
    groupSelect.replaceChildren(option);
  }

  function groupLabel(group) {
    let label = `${group.name} — ${group.current_students}/${group.capacity}`;
    if (group.is_over_capacity) label += ' (переполнена)';
    else if (group.is_full) label += ' (мест нет)';
    if (group.is_current_group) label += ' (текущая)';
    return label;
  }

  function updatePreviews() {
    const option = groupSelect.selectedOptions[0];
    if (!groupSelect.value || !option) {
      capacityPreview.textContent = '';
      cohort2Preview.textContent = '';
      return;
    }
    const count = Number(option.dataset.count);
    const capacity = Number(option.dataset.capacity);
    capacityPreview.textContent = `${option.textContent.trim()}. После перевода: ${count + 1}/${capacity}.`;
    if (count >= capacity) {
      capacityPreview.textContent += ' Требуется явное подтверждение переполнения.';
    }
    cohort2Preview.textContent = option.dataset.cohort2
      ? `Глобальная группа: ${option.dataset.cohort2}`
      : '';
  }

  async function loadCampaignGroups() {
    const sequence = ++loadSequence;
    replaceGroupOptions('Загрузка групп...');
    groupSelect.disabled = true;
    button.disabled = true;
    capacityPreview.textContent = '';
    cohort2Preview.textContent = '';
    groupStatus.textContent = 'Загружаем группы выбранной кампании...';
    groupStatus.classList.remove('is-error');
    error.hidden = true;
    try {
      const query = new URLSearchParams({
        campaign_year: campaignSelect.value,
        username: form.dataset.student
      });
      const response = await fetch(`${form.dataset.groupsUrl}?${query}`, {
        headers: { Accept: 'application/json' }
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || result.error || 'Не удалось загрузить группы.');
      if (sequence !== loadSequence) return;

      replaceGroupOptions('Выберите группу');
      for (const group of result.groups) {
        const option = document.createElement('option');
        option.value = group.name;
        option.textContent = groupLabel(group);
        option.dataset.count = group.current_students;
        option.dataset.capacity = group.capacity;
        option.dataset.cohort2 = group.cohort2 || '';
        option.disabled = Boolean(group.is_current_group);
        groupSelect.appendChild(option);
      }
      const hasAvailableGroup = result.groups.some(group => !group.is_current_group);
      groupSelect.disabled = !hasAvailableGroup;
      button.disabled = !hasAvailableGroup;
      groupStatus.textContent = hasAvailableGroup
        ? 'Выберите группу назначения в этой приёмной кампании.'
        : 'В выбранной приёмной кампании нет доступных групп для перевода.';
    } catch (exception) {
      if (sequence !== loadSequence) return;
      replaceGroupOptions('Группы недоступны');
      groupSelect.disabled = true;
      button.disabled = true;
      groupStatus.textContent = exception.message || 'Не удалось загрузить группы.';
      groupStatus.classList.add('is-error');
    }
  }

  campaignSelect.addEventListener('change', loadCampaignGroups);
  groupSelect.addEventListener('change', updatePreviews);

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !groupSelect.value) return;
    busy = true;
    button.disabled = true;
    error.hidden = true;
    const data = new FormData(form);
    data.set('expected_source', form.dataset.source);
    data.set('expected_campaign', form.dataset.sourceCampaign);
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
        const actionLabel = override
          ? 'Перевести всё равно'
          : (result.campaign_changed ? 'Перенести в другую кампанию' : 'Перевести');
        const confirmed = await window.ManticoreConfirm(
          `${result.message} Продолжить перевод?`, actionLabel
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
      button.disabled = groupSelect.disabled;
      button.focus();
      button.removeAttribute('aria-busy');
      button.classList.remove('is-busy');
    }
  });
})();
