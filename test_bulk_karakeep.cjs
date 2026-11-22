const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(failAt, connected = true) {
  const calls = [];
  const messages = [];
  const articles = ['a', 'b', 'c'].map(id => ({ id, url: `https://example.org/${id}` }));
  let state = { version: 0, decisions: {}, karakeepSaved: {} };
  const payload = () => structuredClone({ articles, state, karakeep: { connected } });
  const context = vm.createContext({ URL, Set, console, initial: payload() });
  const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("$('#all-topics').addEventListener")), context);
  context.transport = async (path, body) => {
    if (path === '/api/state') return payload();
    assert.equal(path, '/api/karakeep/save');
    assert.equal(body.version, state.version);
    calls.push(body.id);
    if (body.id === failAt) throw Object.assign(new Error('Speichern fehlgeschlagen'), { status: 502 });
    state.version++;
    state.decisions[body.id] = { choice: 'remove' };
    state.karakeepSaved[body.id] = { bookmarkId: body.id };
    return payload();
  };
  context.recordNotice = message => messages.push(message);
  vm.runInContext(`
    data = initial;
    ui.selected = new Set(['a', 'b', 'c']);
    render = () => {};
    captureFocus = () => null;
    restoreFocus = () => {};
    document = { querySelector: () => null, activeElement: 1, body: 2 };
    request = transport;
    notice = recordNotice;
    openKarakeepConnection = () => { connectionOpened = true; };
    var connectionOpened = false;
  `, context);
  return { calls, messages, context, run: () => vm.runInContext('saveSelectedToKarakeep()', context),
    value: expression => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context)) };
}

test('bulk save uses current versions and only stages confirmed selected articles', async () => {
  const h = harness();
  vm.runInContext("ui.selected.delete('c')", h.context);
  await h.run();
  assert.deepEqual(h.calls, ['a', 'b']);
  assert.equal(h.value('data.state.version'), 2);
  assert.deepEqual(h.value('Object.keys(data.state.decisions)'), ['a', 'b']);
  assert.deepEqual(h.value('[...ui.selected]'), []);
  assert.equal(h.value('ui.busy'), false);
  assert.match(h.messages.at(-1), /2 von 2/);
});

test('a partial failure keeps the failed and unattempted items selected without retry', async () => {
  const h = harness('b');
  await h.run();
  assert.deepEqual(h.calls, ['a', 'b']);
  assert.deepEqual(h.value('Object.keys(data.state.decisions)'), ['a']);
  assert.deepEqual(h.value('[...ui.selected]'), ['b', 'c']);
  assert.match(h.messages.at(-1), /1 von 3/);
  assert.match(h.messages.at(-1), /Speichern fehlgeschlagen/);
});

test('connection and busy guards prevent bulk writes', async () => {
  const h = harness(null, false);
  await h.run();
  assert.equal(h.value('connectionOpened'), true);
  assert.deepEqual(h.calls, []);
  vm.runInContext('ui.busy = true; connectionOpened = false', h.context);
  await h.run();
  assert.equal(h.value('connectionOpened'), false);
  assert.deepEqual(h.calls, []);
});

test('legacy payload without eligibleIds keeps every source visible and X overview separate', () => {
  const h = harness();
  vm.runInContext(`
    initial.articles[0].url = 'https://x.com/author/status/123';
    applyState(initial);
    ui.collection = 'all';
    ui.status = 'all';
  `, h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['a', 'b', 'c']);
  vm.runInContext("ui.collection = 'x'", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['a']);
  vm.runInContext("ui.collection = 'all'; ui.status = 'saved'; remoteIds = new Set(['b', 'c'])", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a => a.id)'), ['b', 'c']);
});
