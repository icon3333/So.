const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(overrides = {}, expression = 'xImportParameters()', setup = 'data = { xImport: {} };') {
  const fields = {
    '#x-import-posts': { value: '1000' }, '#x-import-users': { value: '1000' },
    '#x-import-pages': { value: '1000' }, '#x-import-usd': { value: '0.30' },
    '#x-import-restart': { checked: false }, ...overrides,
  };
  const ctx = vm.createContext({ URL, Set, document: { querySelector: key => fields[key] } });
  const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("$('#all-topics').addEventListener")), ctx);
  vm.runInContext(setup, ctx);
  return () => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, ctx));
}

test('full import uses an independent explicit USD cap and up to 100 posts per page', () => {
  const budget = harness()();
  assert.equal(budget.usd, .30);
  assert.equal(budget.maxPages, 1000);
  assert.equal(budget.maxResults, 100);
  assert.equal(budget.posts, 1000);
  assert.equal(budget.mutations, 0);
});

test('a completed X live check replaces a cached partial scan, including a verified zero', () => {
  const snapshot = harness({}, "sourceInventorySnapshot('x')", `
    data = {
      sourceInventory: {x: {count: 20, coverage: 'partial', checkedAt: null, stale: true}},
      state: {xRemote: {starredIds: [], checkedAt: '2026-11-18T12:00:00.123+00:00'}},
      articles: []
    };
  `)();
  assert.deepEqual(snapshot, {count: 0, coverage: 'complete', checkedAt: '2026-11-18T12:00:00.123+00:00', notImported: 0});
});

test('unimported count compares IDs from the same source and full check', () => {
  const counts = harness({}, "[sourceInventorySnapshot('inoreader'), sourceInventorySnapshot('x')]", `
    data = {
      state: {
        remote: {starredIds: ['ir1', 'ir2'], checkedAt: '2026-11-18T12:00:00Z'},
        xRemote: {accountId: 'account', starredIds: ['1', '2'], checkedAt: '2026-11-18T12:00:00Z'}
      },
      articles: [{id: 'ir1'}, {id: 'x:account:1', origin: 'x', originAccountId: 'account', postId: '1'}]
    };
  `)();
  assert.deepEqual(counts.map(item => item.notImported), [1, 1]);
});

test('unknown X membership falls back to the recorded scan without implying zero', () => {
  const snapshot = harness({}, "sourceInventorySnapshot('x')", `
    data = {
      sourceInventory: {x: {count: 20, coverage: 'partial', checkedAt: null}},
      state: {xRemote: {starredIds: null, checkedAt: '2026-11-18T12:00:00Z'}}
    };
  `)();
  assert.deepEqual(snapshot, {count: 20, coverage: 'partial', checkedAt: null});
});

test('large libraries are allowed inside separately approved resource and money caps', () => {
  const budget = harness({ '#x-import-posts': { value: '5000' },
    '#x-import-users': { value: '0' }, '#x-import-usd': { value: '2' } })();
  assert.equal(budget.posts, 5000);
  assert.equal(budget.usd, 2);
});

test('invalid or empty money caps cannot reach the import endpoint', () => {
  for (const value of ['', '0', '-1', 'NaN', 'Infinity']) {
    assert.throws(harness({ '#x-import-usd': { value } }));
  }
});
