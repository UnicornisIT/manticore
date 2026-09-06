(function () {
  'use strict';

  class TableSelectionController {
    constructor(root) {
      this.root = root;
      this.selectedIds = new Set();
      this.header = root.querySelector('[data-select-all]');
      this.counts = Array.from(root.querySelectorAll('[data-selection-count]'));
      this.toolbars = Array.from(root.querySelectorAll('[data-selection-toolbar]'));
      this.actions = Array.from(root.querySelectorAll('[data-selection-action]'));
      this.refreshQueued = false;

      this.actions.forEach((action) => {
        action.dataset.selectionInitiallyDisabled = action.disabled ? 'true' : 'false';
      });
      this.rowCheckboxes().forEach((checkbox) => {
        if (checkbox.checked && checkbox.value && !checkbox.disabled && this.isVisible(checkbox)) {
          this.selectedIds.add(checkbox.value);
        }
      });

      root.addEventListener('change', (event) => this.handleChange(event));
      root.addEventListener('table-selection:refresh', () => this.refresh());
      this.observer = new MutationObserver((mutations) => {
        if (!mutations.some((mutation) => mutation.type === 'childList' || ['hidden', 'disabled', 'style'].includes(mutation.attributeName))) return;
        this.queueRefresh();
      });
      this.observer.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['hidden', 'disabled', 'style'],
      });
      this.refresh({ preserveVisibleSelection: true });
      root.tableSelectionController = this;
    }

    rowCheckboxes() {
      return Array.from(this.root.querySelectorAll('[data-row-select]'));
    }

    isVisible(checkbox) {
      const row = checkbox.closest('tr');
      if (!row || row.hidden || row.closest('[hidden]')) return false;
      return getComputedStyle(row).display !== 'none';
    }

    selectableVisibleCheckboxes() {
      return this.rowCheckboxes().filter((checkbox) => !checkbox.disabled && this.isVisible(checkbox));
    }

    handleChange(event) {
      if (event.target === this.header) {
        this.toggleVisible(Boolean(this.header.checked));
        return;
      }
      const checkbox = event.target.closest?.('[data-row-select]');
      if (!checkbox || !this.root.contains(checkbox) || checkbox.disabled) return;
      if (checkbox.checked && checkbox.value) this.selectedIds.add(checkbox.value);
      else this.selectedIds.delete(checkbox.value);
      this.render();
    }

    toggleVisible(checked) {
      this.selectableVisibleCheckboxes().forEach((checkbox) => {
        checkbox.checked = checked;
        if (checked) this.selectedIds.add(checkbox.value);
        else this.selectedIds.delete(checkbox.value);
      });
      this.render();
    }

    refresh(options = {}) {
      const preserveVisibleSelection = Boolean(options.preserveVisibleSelection);
      const currentIds = new Set(this.rowCheckboxes().map((checkbox) => checkbox.value));
      this.selectedIds.forEach((id) => {
        if (!currentIds.has(id)) this.selectedIds.delete(id);
      });

      this.rowCheckboxes().forEach((checkbox) => {
        if (!this.isVisible(checkbox) || checkbox.disabled) {
          checkbox.checked = false;
          this.selectedIds.delete(checkbox.value);
        } else if (preserveVisibleSelection && checkbox.checked && !checkbox.disabled && checkbox.value) {
          this.selectedIds.add(checkbox.value);
        } else {
          checkbox.checked = this.selectedIds.has(checkbox.value);
        }
      });
      this.render();
    }

    queueRefresh() {
      if (this.refreshQueued) return;
      this.refreshQueued = true;
      requestAnimationFrame(() => {
        this.refreshQueued = false;
        this.refresh();
      });
    }

    render() {
      const visible = this.selectableVisibleCheckboxes();
      const selectedVisible = visible.filter((checkbox) => this.selectedIds.has(checkbox.value));

      this.rowCheckboxes().forEach((checkbox) => {
        checkbox.checked = this.selectedIds.has(checkbox.value);
        const selected = checkbox.checked;
        const row = checkbox.closest('tr');
        row?.classList.toggle('is-selected', selected);
        if (row) row.setAttribute('aria-selected', String(selected));
      });

      if (this.header) {
        this.header.disabled = visible.length === 0;
        this.header.checked = visible.length > 0 && selectedVisible.length === visible.length;
        this.header.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visible.length;
      }

      const selectedCount = this.selectedIds.size;
      this.counts.forEach((count) => { count.textContent = String(selectedCount); });
      this.toolbars.forEach((toolbar) => { toolbar.hidden = selectedCount === 0; });
      this.actions.forEach((action) => {
        action.disabled = selectedCount === 0 || action.dataset.selectionInitiallyDisabled === 'true';
      });

      this.root.dispatchEvent(new CustomEvent('table-selection:change', {
        bubbles: true,
        detail: { selectedIds: Array.from(this.selectedIds), selectedCount, visibleCount: visible.length },
      }));
    }

    selectedValues() {
      return Array.from(this.selectedIds);
    }
  }

  function initialize(root = document) {
    root.querySelectorAll('[data-table-selection]').forEach((selectionRoot) => {
      if (!selectionRoot.tableSelectionController) new TableSelectionController(selectionRoot);
    });
  }

  window.TableSelectionController = TableSelectionController;
  window.initializeTableSelections = initialize;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initialize());
  else initialize();
})();
