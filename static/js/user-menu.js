(function () {
  'use strict';
  const menu = document.querySelector('.user-menu');
  if (!menu) return;
  const trigger = menu.querySelector('summary');
  document.addEventListener('click', event => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      trigger.focus();
      event.preventDefault();
    }
  });
  menu.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    menu.open = false;
    const destination = new URL(link.href, window.location.href);
    if (destination.pathname === window.location.pathname && destination.hash) {
      document.getElementById(destination.hash.slice(1))?.focus({preventScroll: true});
    }
  }));
})();
