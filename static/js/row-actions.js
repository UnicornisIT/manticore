(function (root, factory) {
  'use strict';

  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }

  root.ManticoreRowActions = api;
  api.init(root.document, root);
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const DEFAULT_GAP = 6;
  const DEFAULT_MARGIN = 10;

  function calculateDropdownPosition(triggerRect, menuSize, viewportSize, options = {}) {
    const gap = Number.isFinite(options.gap) ? options.gap : DEFAULT_GAP;
    const margin = Number.isFinite(options.margin) ? options.margin : DEFAULT_MARGIN;
    const viewportWidth = Math.max(0, viewportSize.width);
    const viewportHeight = Math.max(0, viewportSize.height);
    const menuWidth = Math.max(0, menuSize.width);
    const menuHeight = Math.max(0, menuSize.height);
    const maxLeft = Math.max(margin, viewportWidth - menuWidth - margin);
    const left = Math.max(margin, Math.min(triggerRect.right - menuWidth, maxLeft));
    const below = triggerRect.bottom + gap;
    const fitsBelow = below + menuHeight <= viewportHeight - margin;
    const top = fitsBelow
      ? below
      : Math.max(margin, triggerRect.top - gap - menuHeight);

    return { left, top, placement: fitsBelow ? 'bottom' : 'top' };
  }

  function init(document, window) {
    if (!document || !window || document.documentElement.dataset.rowActionsReady === 'true') return;
    document.documentElement.dataset.rowActionsReady = 'true';

    let active = null;

    function isOpen(menu) {
      return menu.matches(':popover-open');
    }

    function restoreMenu(state) {
      if (!state) return;
      const { menu, marker, trigger } = state;
      menu.style.visibility = '';
      menu.style.left = '';
      menu.style.top = '';
      menu.removeAttribute('data-row-action-placement');
      trigger.setAttribute('aria-expanded', 'false');
      if (marker.parentNode) marker.parentNode.insertBefore(menu, marker);
      marker.remove();
      if (active === state) active = null;
    }

    function closeActive({ restoreFocus = false } = {}) {
      const state = active;
      if (!state) return;
      if (isOpen(state.menu)) state.menu.hidePopover();
      restoreMenu(state);
      if (restoreFocus && state.trigger.isConnected) state.trigger.focus();
    }

    function openMenu(trigger, menu) {
      if (active && active.menu !== menu) closeActive();

      const marker = document.createComment('row-action-popover-origin');
      menu.parentNode.insertBefore(marker, menu);
      document.body.appendChild(menu);
      const state = { trigger, menu, marker };
      active = state;

      menu.style.visibility = 'hidden';
      menu.style.left = '0px';
      menu.style.top = '0px';
      menu.showPopover();

      const menuRect = menu.getBoundingClientRect();
      const triggerRect = trigger.getBoundingClientRect();
      const position = calculateDropdownPosition(
        triggerRect,
        { width: menuRect.width || menu.offsetWidth, height: menuRect.height || menu.offsetHeight },
        { width: window.innerWidth, height: window.innerHeight },
      );
      menu.style.left = `${position.left}px`;
      menu.style.top = `${position.top}px`;
      menu.style.visibility = 'visible';
      menu.setAttribute('data-row-action-placement', position.placement);
      trigger.setAttribute('aria-expanded', 'true');
    }

    document.addEventListener('click', event => {
      const trigger = event.target.closest?.('.row-action-trigger[popovertarget]');
      if (!trigger) {
        if (active && !active.menu.contains(event.target)) closeActive();
        return;
      }
      const menu = document.getElementById(trigger.getAttribute('popovertarget'));
      if (!menu?.classList.contains('row-action-popover')) return;

      event.preventDefault();
      if (active?.menu === menu) closeActive();
      else openMenu(trigger, menu);
    });

    // Native light-dismiss closes the popover. Restore it to its row afterwards so
    // live-search replacement removes the trigger and its menu as one DOM subtree.
    document.addEventListener('toggle', event => {
      if (active?.menu === event.target && !isOpen(active.menu)) restoreMenu(active);
    }, true);

    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !active) return;
      event.preventDefault();
      closeActive({ restoreFocus: true });
    }, true);

    window.addEventListener('resize', () => closeActive(), { passive: true });
    window.addEventListener('scroll', () => closeActive(), { passive: true, capture: true });

    if (typeof MutationObserver !== 'undefined') {
      new MutationObserver(() => {
        if (active && !active.trigger.isConnected) closeActive();
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  return { calculateDropdownPosition, init };
});
