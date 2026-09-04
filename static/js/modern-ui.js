(function () {
  'use strict';
  const body = document.body;
  const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
  const mobile = () => window.matchMedia('(max-width: 900px)').matches;

  if (sidebarToggle) {
    const syncSidebarToggle = () => {
      const expanded = mobile()
        ? body.classList.contains('sidebar-open')
        : !body.classList.contains('sidebar-collapsed');
      const label = expanded ? 'Свернуть боковую панель' : 'Развернуть боковую панель';
      sidebarToggle.setAttribute('aria-expanded', String(expanded));
      sidebarToggle.setAttribute('aria-label', label);
      sidebarToggle.title = label;
    };
    const collapsed = sessionStorage.getItem('manticore-sidebar') === 'collapsed';
    if (collapsed && !mobile()) body.classList.add('sidebar-collapsed');
    syncSidebarToggle();
    sidebarToggle.addEventListener('click', () => {
      if (mobile()) body.classList.toggle('sidebar-open');
      else body.classList.toggle('sidebar-collapsed');
      syncSidebarToggle();
      sessionStorage.setItem('manticore-sidebar', body.classList.contains('sidebar-collapsed') ? 'collapsed' : 'expanded');
    });
    window.addEventListener('resize', syncSidebarToggle, { passive: true });
  }

  document.querySelectorAll('.row-action-popover[popover]').forEach((menu) => {
    menu.addEventListener('toggle', () => {
      if (!menu.matches(':popover-open')) return;
      const trigger = document.querySelector(`[popovertarget="${CSS.escape(menu.id)}"]`);
      if (!trigger) return;
      const triggerRect = trigger.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const gap = 6;
      const left = Math.max(10, Math.min(window.innerWidth - menuRect.width - 10, triggerRect.right - menuRect.width));
      const below = triggerRect.bottom + gap;
      const top = below + menuRect.height <= window.innerHeight - 10
        ? below
        : Math.max(10, triggerRect.top - menuRect.height - gap);
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    });
  });

  const searchToggle = document.querySelector('.nav-search-toggle');
  document.querySelector('.nav-search-toggle-proxy')?.addEventListener('click', () => searchToggle?.click());
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      searchToggle?.click();
    }
  });

  const themeToggle = document.querySelector('[data-theme-toggle]');
  const themeLabels = { system: 'системная', light: 'светлая', dark: 'тёмная' };
  const syncThemeTitle = () => {
    const theme = window.ManticoreTheme?.get() || 'system';
    if (themeToggle) themeToggle.title = `Тема: ${themeLabels[theme]}`;
  };
  themeToggle?.addEventListener('click', () => {
    const current = window.ManticoreTheme?.get() || 'system';
    const next = { system: 'light', light: 'dark', dark: 'system' }[current];
    window.ManticoreTheme?.set(next);
    syncThemeTitle();
  });
  syncThemeTitle();

  document.querySelectorAll('.flash-message').forEach((message) => {
    message.setAttribute('role', message.classList.contains('error') ? 'alert' : 'status');
    window.setTimeout(() => {
      message.style.opacity = '0';
      message.style.transform = 'translateY(-5px)';
      window.setTimeout(() => message.remove(), 180);
    }, 6000);
  });

  const confirmModal = document.getElementById('confirm-modal');
  if (confirmModal) {
    const message = confirmModal.querySelector('#confirm-message');
    const accept = confirmModal.querySelector('[data-confirm-accept]');
    const cancelControls = confirmModal.querySelectorAll('[data-confirm-cancel]');
    let pending = null;
    let restoreFocus = null;
    const closeConfirm = () => { confirmModal.hidden = true; document.body.classList.remove('modal-lock'); pending = null; restoreFocus?.focus(); };
    const openConfirm = (control) => {
      pending = control; restoreFocus = document.activeElement;
      message.textContent = control.dataset.confirm || 'Продолжить выполнение действия?';
      accept.textContent = control.dataset.confirmAction || (control.matches('.btn-danger,[data-danger]') ? 'Удалить' : 'Продолжить');
      confirmModal.hidden = false; document.body.classList.add('modal-lock'); accept.focus();
    };
    document.addEventListener('click', event => {
      const control = event.target.closest('[data-confirm]');
      if (!control || control.dataset.confirmBypass === 'true') return;
      event.preventDefault(); event.stopImmediatePropagation(); openConfirm(control);
    }, true);
    document.addEventListener('submit', event => {
      const form = event.target;
      if (!form.dataset.confirm || form.dataset.confirmBypass === 'true') return;
      event.preventDefault(); openConfirm(form);
    }, true);
    accept.addEventListener('click', () => {
      if (!pending) return;
      const target = pending; target.dataset.confirmBypass = 'true';
      if (target.tagName === 'FORM') target.requestSubmit();
      else if (target.tagName === 'BUTTON' || target.tagName === 'INPUT') target.click();
      else if (target.href) window.location.assign(target.href);
      window.setTimeout(() => { delete target.dataset.confirmBypass; }, 0);
      closeConfirm();
    });
    cancelControls.forEach(control => control.addEventListener('click', closeConfirm));
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !confirmModal.hidden) closeConfirm(); });
  }

  document.querySelectorAll('input[type="file"]').forEach((input) => {
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      if (!file) return;
      input.title = `${file.name} · ${(file.size / 1024).toFixed(file.size > 1048576 ? 0 : 1)} КБ`;
    });
    const form = input.closest('form');
    if (form) {
      ['dragenter','dragover'].forEach(type => form.addEventListener(type, event => { event.preventDefault(); form.classList.add('is-dragover'); }));
      ['dragleave','drop'].forEach(type => form.addEventListener(type, event => { event.preventDefault(); form.classList.remove('is-dragover'); }));
      form.addEventListener('drop', event => {
        const files = event.dataTransfer?.files;
        if (!files?.length) return;
        const transfer = new DataTransfer(); transfer.items.add(files[0]); input.files = transfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }
  });

  document.querySelectorAll('tbody input[type="checkbox"]').forEach(checkbox => {
    const sync = () => checkbox.closest('tr')?.classList.toggle('is-selected', checkbox.checked);
    checkbox.addEventListener('change', sync); sync();
  });

  document.querySelectorAll('form').forEach((form) => {
    form.addEventListener('submit', () => {
      form.classList.add('was-validated');
      const button = form.querySelector('button[type="submit"], input[type="submit"]');
      if (!button || button.dataset.keepEnabled !== undefined) return;
      window.setTimeout(() => { button.setAttribute('aria-busy', 'true'); button.classList.add('is-busy'); }, 0);
    });
  });

  const syncFooterHeight = () => {
    const footer = document.getElementById('main-footer');
    if (footer) document.documentElement.style.setProperty('--main-footer-height', `${footer.offsetHeight}px`);
  };
  window.addEventListener('resize', syncFooterHeight, { passive: true });
  syncFooterHeight();
})();
