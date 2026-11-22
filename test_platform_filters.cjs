const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  const context = vm.createContext({ URL, Set });
  const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("$('#all-topics').addEventListener")), context);
  vm.runInContext(`
    data = { articles: [
      { id: 'video', url: 'https://youtu.be/example', topic: 'Tools' },
      { id: 'inoreader-x', url: 'https://x.com/user/status/1', topic: 'Tools' },
      { id: 'native-x', url: 'https://x.com/user/status/2', origin: 'x', topic: 'Tools' },
      { id: 'newsletter', url: 'https://example.substack.com/p/test', topic: 'Finance' },
      { id: 'web', url: 'https://example.org', topic: 'Tools' }
    ], state: { decisions: { 'inoreader-x': { choice: 'keep' } } } };
    render = () => {};
  `, context);
  return expression => {
    const value = vm.runInContext(expression, context);
    return value === undefined ? null : JSON.parse(JSON.stringify(value));
  };
}

test('multiple platforms include their union while storage origin remains independent', () => {
  const run = harness();
  assert.equal(run('filteredArticles().length'), 5);
  run("setPlatforms(['youtube', 'x']); ui.collection = 'inoreader'; null");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['video', 'inoreader-x']);
  run("ui.collection = 'x'; null");
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['native-x']);
});

test('all off shows no articles; all on restores the collection without changing decisions', () => {
  const run = harness();
  const state = run('data.state');
  run('setPlatforms([]); null');
  assert.deepEqual(run('filteredArticles()'), []);
  run('setPlatforms(PLATFORMS); null');
  assert.equal(run('filteredArticles().length'), 5);
  assert.deepEqual(run('data.state'), state);
});

test('platform changes preserve compatible topic and search and reset the visible selection', () => {
  const run = harness();
  run("setTopics(['Tools']); ui.search = 'status'; ui.page = 3; ui.selected.add('video'); setPlatforms(['youtube', 'x']); null");
  assert.deepEqual(run('[...ui.topics]'), ['Tools']);
  assert.deepEqual(run('filteredArticles().map(a => a.id)'), ['inoreader-x', 'native-x']);
  assert.equal(run('ui.page'), 0);
  assert.equal(run('ui.selected.size'), 0);
});
