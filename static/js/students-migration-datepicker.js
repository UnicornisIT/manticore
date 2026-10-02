(function (root) {
    'use strict';

    const MONTHS_GENITIVE = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ];
    const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

    function pad(value) {
        return String(value).padStart(2, '0');
    }

    function makeDate(year, monthIndex, day) {
        return new Date(year, monthIndex, day, 12, 0, 0, 0);
    }

    function dateKey(value) {
        return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
    }

    function parseIso(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
        if (!match) return null;
        const result = makeDate(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
        return dateKey(result) === match[0] ? result : null;
    }

    function parseDisplayDate(value) {
        const text = String(value || '').trim();
        const displayMatch = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(text);
        if (displayMatch) {
            const result = makeDate(Number(displayMatch[3]), Number(displayMatch[2]) - 1, Number(displayMatch[1]));
            return result.getFullYear() === Number(displayMatch[3])
                && result.getMonth() === Number(displayMatch[2]) - 1
                && result.getDate() === Number(displayMatch[1]) ? result : null;
        }
        return parseIso(text);
    }

    function displayDate(value) {
        return `${pad(value.getDate())}.${pad(value.getMonth() + 1)}.${value.getFullYear()}`;
    }

    function monthKey(year, monthIndex) {
        return `${year}-${pad(monthIndex + 1)}`;
    }

    function buildMonthDays(year, monthIndex) {
        const first = makeDate(year, monthIndex, 1);
        const mondayOffset = (first.getDay() + 6) % 7;
        const start = makeDate(year, monthIndex, 1 - mondayOffset);
        return Array.from({ length: 42 }, (_, index) => (
            makeDate(start.getFullYear(), start.getMonth(), start.getDate() + index)
        ));
    }

    function migrationText(count) {
        return count === 1 ? 'Переведён 1 студент' : `Переведено студентов: ${count}`;
    }

    function dayAriaLabel(value, count) {
        const base = `${value.getDate()} ${MONTHS_GENITIVE[value.getMonth()]} ${value.getFullYear()}`;
        return count > 0 ? `${base}, ${migrationText(count).toLocaleLowerCase('ru-RU')}` : base;
    }

    function dayState(value, options) {
        const key = dateKey(value);
        const count = Number(options.events[key] || 0);
        return {
            key,
            count,
            hasEvents: count > 0,
            selected: key === options.selected,
            inRange: Boolean(options.rangeStart && options.rangeEnd
                && key >= options.rangeStart && key <= options.rangeEnd),
            title: count > 0 ? migrationText(count) : '',
            ariaLabel: dayAriaLabel(value, count),
        };
    }

    class CalendarDataCache {
        constructor(loader) {
            this.loader = loader;
            this.values = new Map();
        }

        get(year, monthIndex) {
            const key = monthKey(year, monthIndex);
            if (!this.values.has(key)) {
                const request = Promise.resolve()
                    .then(() => this.loader(year, monthIndex))
                    .then(days => days && typeof days === 'object' ? days : {})
                    .catch(() => {
                        this.values.delete(key);
                        return {};
                    });
                this.values.set(key, request);
            }
            return this.values.get(key);
        }

        peek(year, monthIndex) {
            return this.values.get(monthKey(year, monthIndex)) || null;
        }
    }

    function initDatepickers(doc) {
        const roots = Array.from(doc.querySelectorAll('[data-migration-datepicker]'));
        if (!roots.length) return;

        const endpoint = roots[0].dataset.calendarUrl;
        const cache = new CalendarDataCache(async (year, monthIndex) => {
            const url = new URL(endpoint, root.location.href);
            url.searchParams.set('year', String(year));
            url.searchParams.set('month', String(monthIndex + 1));
            const response = await root.fetch(url.toString(), {
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
            if (!response.ok) throw new Error(`Calendar request failed: ${response.status}`);
            const payload = await response.json();
            return payload.days || {};
        });
        const instances = [];
        let activeInstance = null;

        function selectedRange() {
            const values = roots.map(element => element.querySelector('[data-date-value]').value).filter(Boolean);
            return values.length === 2
                ? [values[0] < values[1] ? values[0] : values[1], values[0] < values[1] ? values[1] : values[0]]
                : ['', ''];
        }

        roots.forEach(element => {
            const hidden = element.querySelector('[data-date-value]');
            const input = element.querySelector('[data-date-display]');
            const toggle = element.querySelector('[data-date-toggle]');
            const popup = element.querySelector('[data-date-popup]');
            const selectedAtStart = parseIso(hidden.value);
            const initial = selectedAtStart || new Date();
            const state = {
                year: initial.getFullYear(),
                monthIndex: initial.getMonth(),
                events: {},
                open: false,
            };

            function close(restoreFocus) {
                if (!state.open) return;
                state.open = false;
                popup.hidden = true;
                input.setAttribute('aria-expanded', 'false');
                if (activeInstance === instance) activeInstance = null;
                if (restoreFocus) input.focus();
            }

            function setMonth(year, monthIndex, focusKey) {
                const normalized = makeDate(year, monthIndex, 1);
                state.year = normalized.getFullYear();
                state.monthIndex = normalized.getMonth();
                render(focusKey);
                loadEvents(focusKey);
            }

            function selectDate(value) {
                hidden.value = dateKey(value);
                input.value = displayDate(value);
                input.setCustomValidity('');
                hidden.dispatchEvent(new Event('change', { bubbles: true }));
                close(true);
                instances.forEach(item => item.render());
            }

            function render(focusKey) {
                const days = buildMonthDays(state.year, state.monthIndex);
                const today = dateKey(new Date());
                const selected = hidden.value;
                const range = selectedRange();
                popup.replaceChildren();

                const header = doc.createElement('div');
                header.className = 'migration-calendar-header';
                const previous = doc.createElement('button');
                previous.type = 'button';
                previous.className = 'migration-calendar-nav';
                previous.setAttribute('aria-label', 'Предыдущий месяц');
                previous.textContent = '‹';
                previous.addEventListener('click', () => setMonth(state.year, state.monthIndex - 1));
                const heading = doc.createElement('strong');
                heading.textContent = makeDate(state.year, state.monthIndex, 1)
                    .toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
                const next = doc.createElement('button');
                next.type = 'button';
                next.className = 'migration-calendar-nav';
                next.setAttribute('aria-label', 'Следующий месяц');
                next.textContent = '›';
                next.addEventListener('click', () => setMonth(state.year, state.monthIndex + 1));
                header.append(previous, heading, next);

                const grid = doc.createElement('div');
                grid.className = 'migration-calendar-grid';
                grid.setAttribute('role', 'grid');
                WEEKDAYS.forEach(label => {
                    const weekday = doc.createElement('span');
                    weekday.className = 'migration-calendar-weekday';
                    weekday.setAttribute('aria-hidden', 'true');
                    weekday.textContent = label;
                    grid.appendChild(weekday);
                });
                days.forEach(value => {
                    const model = dayState(value, {
                        events: state.events,
                        selected,
                        rangeStart: range[0],
                        rangeEnd: range[1],
                    });
                    const button = doc.createElement('button');
                    button.type = 'button';
                    button.className = 'migration-calendar-day';
                    button.dataset.date = model.key;
                    button.setAttribute('role', 'gridcell');
                    button.setAttribute('aria-label', model.ariaLabel);
                    if (value.getMonth() !== state.monthIndex) button.classList.add('migration-calendar-day--outside');
                    if (model.key === today) button.classList.add('migration-calendar-day--today');
                    if (model.inRange) button.classList.add('migration-calendar-day--in-range');
                    if (model.selected) {
                        button.classList.add('migration-calendar-day--selected');
                        button.setAttribute('aria-selected', 'true');
                    }
                    if (model.hasEvents) {
                        button.classList.add('calendar-day--has-events');
                        button.title = model.title;
                    }
                    const number = doc.createElement('span');
                    number.className = 'migration-calendar-day-number';
                    number.textContent = String(value.getDate());
                    button.appendChild(number);
                    if (model.hasEvents) {
                        const dot = doc.createElement('span');
                        dot.className = 'calendar-event-dot';
                        dot.setAttribute('aria-hidden', 'true');
                        button.appendChild(dot);
                    }
                    button.addEventListener('click', () => selectDate(value));
                    button.addEventListener('keydown', event => onDayKeydown(event, value));
                    grid.appendChild(button);
                });
                popup.append(header, grid);
                if (focusKey) {
                    const target = popup.querySelector(`[data-date="${focusKey}"]`);
                    if (target) target.focus();
                }
            }

            function loadEvents(focusKey) {
                const requestedKey = monthKey(state.year, state.monthIndex);
                popup.setAttribute('aria-busy', 'true');
                cache.get(state.year, state.monthIndex).then(events => {
                    if (requestedKey !== monthKey(state.year, state.monthIndex)) return;
                    state.events = events;
                    popup.removeAttribute('aria-busy');
                    if (state.open) render(focusKey);
                });
            }

            function focusDate(value) {
                const key = dateKey(value);
                const target = popup.querySelector(`[data-date="${key}"]`);
                if (target) {
                    target.focus();
                    return;
                }
                setMonth(value.getFullYear(), value.getMonth(), key);
            }

            function onDayKeydown(event, value) {
                const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
                if (Object.prototype.hasOwnProperty.call(offsets, event.key)) {
                    event.preventDefault();
                    focusDate(makeDate(value.getFullYear(), value.getMonth(), value.getDate() + offsets[event.key]));
                } else if (event.key === 'PageUp' || event.key === 'PageDown') {
                    event.preventDefault();
                    const offset = event.key === 'PageUp' ? -1 : 1;
                    const target = makeDate(value.getFullYear(), value.getMonth() + offset, value.getDate());
                    setMonth(target.getFullYear(), target.getMonth(), dateKey(target));
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    close(true);
                }
            }

            function open() {
                if (activeInstance && activeInstance !== instance) activeInstance.close(false);
                activeInstance = instance;
                state.open = true;
                popup.hidden = false;
                input.setAttribute('aria-expanded', 'true');
                render();
                loadEvents();
            }

            function commitInput() {
                const text = input.value.trim();
                if (!text) {
                    hidden.value = '';
                    input.setCustomValidity('');
                    instances.forEach(item => item.render());
                    return true;
                }
                const parsed = parseDisplayDate(text);
                if (!parsed) {
                    input.setCustomValidity('Введите корректную дату в формате ДД.ММ.ГГГГ.');
                    return false;
                }
                hidden.value = dateKey(parsed);
                input.value = displayDate(parsed);
                input.setCustomValidity('');
                state.year = parsed.getFullYear();
                state.monthIndex = parsed.getMonth();
                instances.forEach(item => item.render());
                return true;
            }

            const instance = { close, render, commitInput };
            instances.push(instance);
            input.addEventListener('click', open);
            input.addEventListener('keydown', event => {
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    open();
                    const selectedButton = popup.querySelector('.migration-calendar-day--selected')
                        || popup.querySelector('.migration-calendar-day:not(.migration-calendar-day--outside)');
                    if (selectedButton) selectedButton.focus();
                } else if (event.key === 'Escape') {
                    close(false);
                }
            });
            input.addEventListener('input', () => {
                input.setCustomValidity('');
                if (!input.value.trim()) hidden.value = '';
            });
            input.addEventListener('change', commitInput);
            toggle.addEventListener('click', open);
        });

        const form = roots[0].closest('form');
        if (form) {
            form.addEventListener('submit', event => {
                const invalid = instances.find(instance => !instance.commitInput());
                if (invalid) {
                    event.preventDefault();
                    roots.find(element => !element.querySelector('[data-date-display]').checkValidity())
                        ?.querySelector('[data-date-display]').reportValidity();
                }
            });
        }
        doc.addEventListener('pointerdown', event => {
            if (activeInstance && !event.target.closest('[data-migration-datepicker]')) activeInstance.close(false);
        });
        doc.addEventListener('keydown', event => {
            if (event.key === 'Escape' && activeInstance) activeInstance.close(true);
        });
    }

    const api = {
        CalendarDataCache,
        buildMonthDays,
        dateKey,
        dayState,
        migrationText,
        monthKey,
        parseDisplayDate,
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root.document) {
        if (root.document.readyState === 'loading') {
            root.document.addEventListener('DOMContentLoaded', () => initDatepickers(root.document));
        } else {
            initDatepickers(root.document);
        }
    }
}(typeof window !== 'undefined' ? window : globalThis));
