(function () {
  const key = 'manticore-theme';
  const stored = localStorage.getItem(key);
  document.documentElement.dataset.theme = ['light', 'dark', 'system'].includes(stored) ? stored : 'system';
  window.ManticoreTheme = {
    get: () => document.documentElement.dataset.theme || 'system',
    set: (theme) => {
      if (!['light', 'dark', 'system'].includes(theme)) return;
      document.documentElement.dataset.theme = theme;
      localStorage.setItem(key, theme);
      document.dispatchEvent(new CustomEvent('manticore:theme', { detail: theme }));
    }
  };
})();
