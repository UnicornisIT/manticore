/* Browser regression probe, loaded only by the isolated UI audit server.
   Exercises shipped handlers; never submits a form or uploads to the backend. */
(() => {
  const run = document.createElement('button');
  run.textContent = 'Run upload layout audit';
  run.type = 'button';
  const output = document.createElement('pre');
  output.id = 'layout-audit-result';
  document.querySelector('main').append(run, output);
  run.addEventListener('click', () => {
    const failures = [];
    let checks = 0;
    const check = (ok, message) => { checks++; if (!ok) failures.push(message); };
    document.querySelectorAll('.file-picker').forEach(picker => {
      const input = picker.querySelector('input[type="file"]');
      const form = input.form;
      const id = input.id;
      const border = getComputedStyle(picker).borderLeftWidth;
      const geometry = state => {
        const r = picker.getBoundingClientRect();
        for (let parent = picker.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
          const p = parent.getBoundingClientRect();
          check(r.left >= p.left - 1 && r.right <= p.right + 1, `${id}/${state}: outside ${parent.className}`);
          check(r.top >= p.top - 1 && r.bottom <= p.bottom + 1, `${id}/${state}: vertical overflow in ${parent.className}`);
        }
        check(picker.scrollWidth <= picker.clientWidth + 1, `${id}/${state}: inner overflow`);
        check(getComputedStyle(picker).borderLeftWidth === border, `${id}/${state}: border shift`);
        check(!form.classList.contains('is-dragover'), `${id}/${state}: whole form highlighted`);
        for (const button of form.querySelectorAll('button[type="submit"]')) {
          const b = button.getBoundingClientRect(), f = form.getBoundingClientRect();
          check(b.left >= f.left - 1 && b.right <= f.right + 1, `${id}/${state}: action overflow`);
        }
      };
      const drag = (type, name) => {
        const before = picker.getBoundingClientRect();
        const dataTransfer = new DataTransfer();
        if (name) dataTransfer.items.add(new File(['layout fixture'], name));
        picker.dispatchEvent(new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer}));
        const after = picker.getBoundingClientRect();
        if (type !== 'drop') check(before.width === after.width && before.height === after.height, `${id}/${type}: layout shift`);
        geometry(type);
      };
      input.value = '';
      input.dispatchEvent(new Event('change', {bubbles: true}));
      geometry('empty');
      picker.focus();
      geometry('focus');
      check(getComputedStyle(picker).outlineStyle !== 'none', `${id}: missing focus indicator`);
      check(parseFloat(getComputedStyle(picker).outlineOffset) < 0, `${id}: external focus outline`);
      drag('dragenter');
      check(picker.classList.contains('is-dragover'), `${id}: dragenter ignored`);
      drag('dragover');
      drag('dragleave');
      check(!picker.classList.contains('is-dragover'), `${id}: dragleave not cleared`);
      const ext = input.accept.includes('.xlsx') ? '.xlsx' : input.accept.includes('.csv') ? '.csv' : '.pdf';
      drag('drop', 'short' + ext);
      check(input.files[0]?.name === 'short' + ext && picker.classList.contains('has-file'), `${id}: valid drop`);
      drag('drop', 'invalid.exe');
      check(input.files.length === 0 && picker.classList.contains('is-invalid'), `${id}: invalid drop overwritten`);
      const long = 'ПРИКАЗЫ_О_ЗАЧИСЛЕНИИ_У+ПП_ОТ_04_09_2026_ФИНАЛЬНАЯ_ВЕРСИЯ' + ext;
      drag('drop', long);
      check(input.files[0]?.name === long && !picker.classList.contains('is-invalid'), `${id}: replacement`);
      for (const other of form.querySelectorAll('input[type="file"]')) {
        if (other !== input) check(other.files[0]?.name !== long, `${id}: drop assigned to another input`);
      }
      const submit = form.querySelector('button[type="submit"]');
      submit?.setAttribute('aria-busy', 'true');
      geometry('uploading');
      submit?.removeAttribute('aria-busy');
      input.value = '';
      input.dispatchEvent(new Event('change', {bubbles: true}));
      check(!picker.classList.contains('has-file'), `${id}: clear selection`);
      geometry('cleared');
      drag('drop', long);
      picker.blur();
    });
    output.textContent = JSON.stringify({checks, failures});
  });
})();
