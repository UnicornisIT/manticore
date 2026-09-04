(function() {
        const downloadButtons = Array.from(document.querySelectorAll('.btn-download'));
        downloadButtons.forEach(function(button) {
            button.addEventListener('click', function() {
                if (button.matches('[disabled], [aria-disabled="true"]')) {
                    return;
                }
                button.classList.add('is-downloading');
                window.setTimeout(function() {
                    button.classList.remove('is-downloading');
                }, 1600);
            });
        });
    })();
    (function() {
        const fileInputs = Array.from(document.querySelectorAll('.file-picker input[type="file"]'));
        if (!fileInputs.length) {
            return;
        }

        function ensureUploadIcon(picker) {
            if (picker.querySelector('.file-picker-icon')) {
                picker.classList.add('has-upload-icon');
                return;
            }
            const icon = document.createElement('span');
            icon.className = 'file-picker-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = [
                '<svg viewBox="0 0 24 24" focusable="false">',
                    '<path class="file-picker-arrow file-picker-arrow-head" d="M12 5 6.5 10.5"></path>',
                    '<path class="file-picker-arrow file-picker-arrow-head" d="M12 5 17.5 10.5"></path>',
                    '<path class="file-picker-arrow file-picker-arrow-line" d="M12 5v14"></path>',
                    '<path class="file-picker-check" d="M5 13.2 9.1 17.3 19 7.4"></path>',
                '</svg>'
            ].join('');
            picker.insertBefore(icon, picker.firstChild);
            picker.classList.add('has-upload-icon');
        }

        function fileMatchesAccept(input, file) {
            const accept = (input.getAttribute('accept') || '').trim();
            if (!accept || !file || !file.name) {
                return true;
            }
            const fileName = file.name.toLowerCase();
            const fileType = (file.type || '').toLowerCase();
            return accept.split(',').map(function(part) {
                return part.trim().toLowerCase();
            }).filter(Boolean).some(function(rule) {
                if (rule.startsWith('.')) {
                    return fileName.endsWith(rule);
                }
                if (rule.endsWith('/*')) {
                    return fileType.startsWith(rule.slice(0, -1));
                }
                return fileType === rule;
            });
        }

        function updateFileName(input, customMessage) {
            const picker = input.closest('.file-picker');
            const target = document.getElementById(input.dataset.fileNameTarget);
            const fileName = customMessage || (input.files && input.files.length ? input.files[0].name : 'Файл не выбран');
            if (target) {
                target.textContent = fileName;
            }
            if (picker) {
                picker.classList.toggle('is-invalid', Boolean(customMessage));
                if (input.files && input.files.length && !customMessage) {
                    picker.classList.remove('has-file');
                    void picker.offsetWidth;
                    picker.classList.add('has-file');
                } else {
                    picker.classList.remove('has-file');
                }
            }
        }

        function assignDroppedFile(input, files) {
            if (!files || !files.length) {
                return;
            }
            const file = files[0];
            if (!fileMatchesAccept(input, file)) {
                input.value = '';
                updateFileName(input, 'Неподходящий формат: ' + file.name);
                return;
            }
            try {
                const transfer = new DataTransfer();
                transfer.items.add(file);
                input.files = transfer.files;
            } catch (error) {
                try {
                    input.files = files;
                } catch (fallbackError) {
                    input.click();
                    return;
                }
            }
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }

        fileInputs.forEach(function(input) {
            const picker = input.closest('.file-picker');
            if (!picker) {
                return;
            }
            ensureUploadIcon(picker);

            input.addEventListener('change', function() {
                updateFileName(input);
            });

            picker.addEventListener('click', function(event) {
                if (event.target.closest('label') || event.target === input) {
                    return;
                }
                input.click();
            });

            picker.addEventListener('keydown', function(event) {
                if (event.key !== 'Enter' && event.key !== ' ') {
                    return;
                }
                event.preventDefault();
                input.click();
            });

            ['dragenter', 'dragover'].forEach(function(eventName) {
                picker.addEventListener(eventName, function(event) {
                    event.preventDefault();
                    picker.classList.add('is-dragover');
                });
            });

            ['dragleave', 'dragend'].forEach(function(eventName) {
                picker.addEventListener(eventName, function(event) {
                    if (event.relatedTarget && picker.contains(event.relatedTarget)) {
                        return;
                    }
                    picker.classList.remove('is-dragover');
                });
            });

            picker.addEventListener('drop', function(event) {
                event.preventDefault();
                picker.classList.remove('is-dragover');
                assignDroppedFile(input, event.dataTransfer ? event.dataTransfer.files : null);
            });
        });
    })();
    (function() {
        const dropdowns = Array.from(document.querySelectorAll('.nav-dropdown'));
        if (!dropdowns.length) {
            return;
        }
        dropdowns.forEach(function(dropdown) {
            dropdown.addEventListener('toggle', function() {
                if (!dropdown.open) {
                    return;
                }
                dropdowns.forEach(function(otherDropdown) {
                    if (otherDropdown !== dropdown) {
                        otherDropdown.open = false;
                    }
                });
            });
        });
        document.addEventListener('click', function(event) {
            if (event.target.closest('.nav-dropdown')) {
                return;
            }
            dropdowns.forEach(function(dropdown) {
                dropdown.open = false;
            });
        });
        document.addEventListener('keydown', function(event) {
            if (event.key !== 'Escape') {
                return;
            }
            dropdowns.forEach(function(dropdown) {
                dropdown.open = false;
            });
        });
    })();
    (function() {
        const searchForm = document.querySelector('.nav-search');
        if (!searchForm) {
            return;
        }
        const toggle = searchForm.querySelector('.nav-search-toggle');
        const sourceInput = searchForm.querySelector('.nav-search-input');
        const input = document.getElementById('global-search-palette-input') || sourceInput;
        const modal = document.getElementById('global-search-modal');
        const meta = document.getElementById('global-search-meta');
        const resultsBox = document.getElementById('global-search-results');
        const closeControls = Array.from(document.querySelectorAll('[data-global-search-close]'));
        const endpoint = searchForm.dataset.searchOverlayUrl;
        const personUrlTemplate = searchForm.dataset.personUrlTemplate;
        let lastController = null;

        function personUrl(item) {
            const kind = encodeURIComponent(String(item.kind ?? ''));
            const recordId = encodeURIComponent(String(item.id ?? ''));
            return personUrlTemplate
                .replace('__kind__', kind)
                .replace('__record__', recordId);
        }

        function openSearch() {
            searchForm.classList.add('is-open');
            toggle.setAttribute('aria-expanded', 'true');
            openModal();
            if (sourceInput.value && !input.value) {
                input.value = sourceInput.value;
            }
            window.setTimeout(function() {
                input.focus();
            }, 0);
        }

        function collapseSearch() {
            if (input.value.trim()) {
                return;
            }
            searchForm.classList.remove('is-open');
            toggle.setAttribute('aria-expanded', 'false');
        }

        function openModal() {
            modal.hidden = false;
            document.body.classList.add('global-search-lock');
        }

        function closeModal() {
            modal.hidden = true;
            document.body.classList.remove('global-search-lock');
            (document.querySelector('.nav-search-toggle-proxy') || toggle).focus();
        }

        function renderResults(payload) {
            const query = payload.query || input.value.trim();
            const results = Array.isArray(payload.results) ? payload.results : [];
            if (!results.length) {
                meta.textContent = 'По запросу «' + query + '» ничего не найдено';
                const empty = document.createElement('p');
                empty.className = 'global-search-empty';
                empty.textContent = 'Попробуйте ФИО, номер договора, логин, email или группу.';
                resultsBox.replaceChildren(empty);
                return;
            }
            meta.textContent = 'Найдено: ' + results.length + ' по запросу «' + query + '»';
            const fragment = document.createDocumentFragment();
            results.forEach(function(item) {
                const article = document.createElement('article'); article.className = 'global-search-result';
                const details = document.createElement('div');
                const status = document.createElement('span'); status.className = 'global-search-result-status'; status.textContent = item.status || 'Запись';
                const title = document.createElement('span'); title.className = 'global-search-result-title'; title.textContent = item.title || 'Без названия';
                const subtitle = document.createElement('span'); subtitle.className = 'global-search-result-subtitle'; subtitle.textContent = item.subtitle || '';
                const link = document.createElement('a'); link.className = 'btn global-search-open'; link.href = personUrl(item); link.textContent = 'Открыть';
                details.append(status, title, subtitle); article.append(details, link); fragment.append(article);
            });
            resultsBox.replaceChildren(fragment);
        }

        async function runSearch() {
            const query = input.value.trim();
            sourceInput.value = query;
            openSearch();
            if (!query) {
                input.focus();
                return;
            }
            openModal();
            meta.textContent = 'Ищу...';
            resultsBox.replaceChildren();

            if (lastController) {
                lastController.abort();
            }
            lastController = window.AbortController ? new AbortController() : null;
            const searchUrl = new URL(endpoint, window.location.origin);
            searchUrl.searchParams.set('q', query);

            try {
                const options = { headers: { Accept: 'application/json' } };
                if (lastController) {
                    options.signal = lastController.signal;
                }
                const response = await fetch(searchUrl.toString(), options);
                if (!response.ok) {
                    throw new Error('Search request failed');
                }
                renderResults(await response.json());
            } catch (error) {
                if (error.name === 'AbortError') {
                    return;
                }
                meta.textContent = '';
                const failure = document.createElement('p');
                failure.className = 'global-search-error';
                failure.textContent = 'Не удалось выполнить поиск. Попробуйте еще раз.';
                resultsBox.replaceChildren(failure);
            }
        }

        toggle.addEventListener('click', function(event) {
            event.preventDefault();
            if (!searchForm.classList.contains('is-open')) {
                openSearch();
                return;
            }
            if (input.value.trim()) {
                runSearch();
                return;
            }
            input.focus();
        });

        searchForm.addEventListener('submit', function(event) {
            event.preventDefault();
            runSearch();
        });

        input.addEventListener('keydown', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                runSearch();
                return;
            }
            if (event.key !== 'Escape') {
                return;
            }
            if (!modal.hidden) {
                closeModal();
                return;
            }
            input.value = '';
            collapseSearch();
            toggle.focus();
        });

        input.addEventListener('blur', function() {
            window.setTimeout(collapseSearch, 120);
        });

        closeControls.forEach(function(control) {
            control.addEventListener('click', closeModal);
        });

        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && !modal.hidden) {
                closeModal();
            }
        });
    })();
