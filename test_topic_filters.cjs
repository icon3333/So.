const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  const context = vm.createContext({ URL, Set, document: { querySelector: () => ({ value: '' }) } });
  const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("$('#all-topics').addEventListener")), context);
  vm.runInContext(`
    data = { articles: [
      { id: 'x-ai', origin: 'x', url: 'https://x.com/u/status/1', topic: 'KI & Agenten' },
      { id: 'x-health', origin: 'x', url: 'https://x.com/u/status/2', topic: 'Gesundheit & Training' },
      { id: 'x-money', origin: 'x', url: 'https://x.com/u/status/3', topic: 'Finanzen & Wirtschaft' },
      { id: 'ir-ai', origin: 'inoreader', url: 'https://x.com/u/status/4', topic: 'KI & Agenten' },
      { id: 'video', origin: 'inoreader', url: 'https://youtube.com/watch?v=1', topic: 'KI & Agenten' }
    ], state: { decisions: {} } };
    render = () => {}; restoreFocus = () => {};
  `, context);
  return expression => {
    const value = vm.runInContext(expression, context);
    return value === undefined ? null : JSON.parse(JSON.stringify(value));
  };
}

test('native X combines several topics as a union within its origin and platform', () => {
  const run = harness();
  run("setCollection('x'); setPlatforms(['x']); setTopics(['KI & Agenten', 'Gesundheit & Training']);");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['x-ai', 'x-health']);
});

test('each topic can be turned off and on without deselecting other topics', () => {
  const run = harness();
  run("setCollection('x'); toggleTopic('Finanzen & Wirtschaft', false);");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['x-ai', 'x-health']);
  run("toggleTopic('Gesundheit & Training', false); toggleTopic('Finanzen & Wirtschaft', true);");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['x-ai', 'x-money']);
});

test('all off is empty and all on includes newly imported topics without changing decisions', () => {
  const run = harness();
  run('setTopics([]);');
  assert.deepEqual(run('filteredArticles()'), []);
  run("setTopics(null); data.articles.push({id:'new', origin:'x', url:'https://x.com/u/status/5', topic:'Musik'});");
  assert.equal(run('filteredArticles().length'), 6);
  assert.deepEqual(run('data.state.decisions'), {});
});

test('topic selections survive origin/platform changes and clear stale row selection', () => {
  const run = harness();
  run("ui.selected.add('x-money'); ui.page=3; setTopics(['KI & Agenten']); setCollection('inoreader'); setPlatforms(['youtube']);");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['video']);
  assert.equal(run('ui.page'), 0);
  assert.equal(run('ui.selected.size'), 0);
  run("setCollection('x'); setPlatforms(['x']);");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['x-ai']);
});

test('switching off one topic preserves topics hidden by the current storage', () => {
  const run = harness();
  run("data.articles.push({id:'ir-only', origin:'inoreader', url:'https://example.org', topic:'Homelab'}); setCollection('x'); toggleTopic('Finanzen & Wirtschaft', false); setCollection('inoreader');");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['ir-ai', 'video', 'ir-only']);
});

for (const setter of ["setCollection('x')", "setPlatforms(['x'])"]) {
  test(`${setter} clears stale article selection and resets pagination`, () => {
    const run = harness();
    run(`ui.selected.add('video'); ui.page=3; ${setter};`);
    assert.equal(run('ui.page'), 0);
    assert.equal(run('ui.selected.size'), 0);
  });
}


test('completion uses decisions, keeps skipped items open, and distinguishes storage from platform', () => {
  const run = harness();
  run("data.state.decisions={'x-ai':{choice:'remove'},'x-health':{choice:'keep'},'x-money':{choice:'skip'}};");
  assert.equal(run("filterProgress('collection','x').open"), 1);
  assert.equal(run("filterProgress('platform','x').total"), 4);
  assert.equal(run("filterProgress('platform','youtube').total"), 1);
  run("ui.hideCompleted=true;data.state.decisions['x-money']={choice:'remove'};");
  assert.equal(run("showFilterGroup(filterProgress('collection','x'))"), false);
  assert.equal(run("showFilterGroup(filterProgress('platform','x'))"), true);
});

test('hiding completed filters restores a usable scope without changing decisions', () => {
  const run = harness();
  run("data.state.decisions={'x-ai':{choice:'remove'},'x-health':{choice:'keep'},'x-money':{choice:'remove'}};setCollection('x');setTopics(['KI & Agenten']);ui.hideCompleted=true;");
  const before=run('data.state.decisions');
  run('normalizeCompletedFilters();');
  assert.equal(run('ui.collection'), 'all');
  assert.deepEqual(run('data.state.decisions'),before);
  run("ui.hideCompleted=false;");
  assert.equal(run("showFilterGroup(filterProgress('collection','x'))"), true);
});

test('platform counts follow storage, ignoring their own switches and topic choices', () => {
  const run = harness();
  run("setCollection('inoreader');setPlatforms(['youtube']);setTopics([]);");
  assert.equal(run("filterProgress('platform','x').total"),1);
  assert.equal(run("filterProgress('platform','youtube').total"),1);
});
