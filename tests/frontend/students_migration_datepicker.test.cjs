const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  CalendarDataCache,
  buildMonthDays,
  dateKey,
  dayState,
  migrationText,
  monthKey,
  parseDisplayDate,
} = require(path.join(__dirname, '../../static/js/students-migration-datepicker.js'));

function octoberDay(day) {
  return new Date(2026, 9, day, 12);
}

test('a day without migrations has no event indicator', () => {
  const state = dayState(octoberDay(3), { events: {}, selected: '', rangeStart: '', rangeEnd: '' });
  assert.equal(state.hasEvents, false);
  assert.equal(state.title, '');
});

test('one or fifty migrations still produce one boolean event marker', () => {
  const one = dayState(octoberDay(2), {
    events: { '2026-10-02': 1 }, selected: '', rangeStart: '', rangeEnd: ''
  });
  const fifty = dayState(octoberDay(15), {
    events: { '2026-10-15': 50 }, selected: '', rangeStart: '', rangeEnd: ''
  });
  assert.equal(one.hasEvents, true);
  assert.equal(one.count, 1);
  assert.equal(one.title, 'Переведён 1 студент');
  assert.equal(fifty.hasEvents, true);
  assert.equal(fifty.count, 50);
  assert.equal(fifty.title, 'Переведено студентов: 50');
});

test('selected and event states coexist', () => {
  const state = dayState(octoberDay(2), {
    events: { '2026-10-02': 3 }, selected: '2026-10-02', rangeStart: '', rangeEnd: ''
  });
  assert.equal(state.selected, true);
  assert.equal(state.hasEvents, true);
});

test('calendar grid includes adjacent month days without timezone parsing', () => {
  const days = buildMonthDays(2026, 9);
  assert.equal(days.length, 42);
  assert.equal(dateKey(days[0]), '2026-09-28');
  assert.equal(dateKey(days[41]), '2026-11-08');
  assert.equal(dateKey(parseDisplayDate('02.10.2026')), '2026-10-02');
});

test('cache reuses a loaded month and separates month navigation', async () => {
  const calls = [];
  const cache = new CalendarDataCache(async (year, monthIndex) => {
    calls.push(monthKey(year, monthIndex));
    return { [`${monthKey(year, monthIndex)}-02`]: 1 };
  });
  const first = await cache.get(2026, 9);
  const repeated = await cache.get(2026, 9);
  const next = await cache.get(2026, 10);
  const back = await cache.get(2026, 9);
  assert.deepEqual(calls, ['2026-10', '2026-11']);
  assert.strictEqual(first, repeated);
  assert.strictEqual(first, back);
  assert.equal(next['2026-11-02'], 1);
});

test('network failure degrades to no dots and remains retryable', async () => {
  let calls = 0;
  const cache = new CalendarDataCache(async () => {
    calls += 1;
    if (calls === 1) throw new Error('offline');
    return { '2026-10-02': 1 };
  });
  assert.deepEqual(await cache.get(2026, 9), {});
  assert.deepEqual(await cache.get(2026, 9), { '2026-10-02': 1 });
  assert.equal(calls, 2);
  assert.equal(migrationText(5), 'Переведено студентов: 5');
});
