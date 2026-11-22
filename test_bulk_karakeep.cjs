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

test('legacy payload keeps all entries and source provenance is not URL domain', () => {
  const h = harness();
  vm.runInContext(`
    initial.articles[0].url = 'https://x.com/author/status/123';
    applyState(initial);
    ui.collection = 'all';
    ui.status = 'all';
  `, h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['a', 'b', 'c']);
  vm.runInContext("ui.collection = 'x'", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), []);
  vm.runInContext("ui.collection = 'all'; ui.status = 'saved'; remoteIds = new Set(['b', 'c'])", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a => a.id)'), ['b', 'c']);
  vm.runInContext("initial.articles[1].origin='x'; initial.articles[1].url='https://example.org/non-x'; applyState(initial); ui.collection='x'; ui.status='all'", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['b']);
  vm.runInContext("ui.collection='inoreader'", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['a','c']);
});

function fakeDom(h) {
  const elements = new Map();
  const get = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', checked: false, disabled: false, hidden: false, textContent: '', open: false,
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; },
      showModal() { this.open = true; }, close() { this.open = false; }
    });
    return elements.get(selector);
  };
  h.context.document = { querySelector: get, querySelectorAll: () => [], activeElement: 1, body: 2 };
  get('#x-import-posts').value = '20';
  get('#x-import-users').value = '20';
  get('#x-import-pages').value = '1';
  return get;
}

test('native records remain visible with Inoreader membership and combined savedIds', () => {
  const h = harness();
  vm.runInContext("initial.articles[1].origin='x'; initial.articles[1].originAccountId='123'; initial.articles[1].postId='2'; initial.state.remote={starredIds:['a','c']}; applyState(initial); ui.status='saved'", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a=>a.id)'), ['a','b','c']);
  vm.runInContext("initial.savedIds=['a','b']; applyState(initial)", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a=>a.id)'), ['a','b']);
});

test('changing provenance resets hidden selection and stale publisher search', () => {
  const h = harness();
  const get = fakeDom(h);
  vm.runInContext("ui.source='old'; ui.search='old'; ui.page=2; setCollection('inoreader')", h.context);
  assert.deepEqual(h.value('[...ui.selected]'), []);
  assert.deepEqual(h.value('[ui.page,ui.source,ui.search,ui.collection]'), [0,'','','inoreader']);
  assert.equal(get('#search').value, '');
});

test('import has a live quote, resets approval and sends flat bounded parameters', async () => {
  const h = harness();
  const get = fakeDom(h);
  const requests = [];
  h.context.importTransport = async (path, body) => { requests.push({path,body}); return {xImport:{complete:false}}; };
  vm.runInContext("data.xConnection={connected:true}; request=importTransport; perform=async action=>action(); openXImport()", h.context);
  assert.match(get('#x-import-quote').textContent, /0,30 USD/);
  await vm.runInContext('confirmXImport()', h.context);
  assert.equal(requests.length, 0);
  get('#x-import-posts').value = '200';
  get('#x-import-users').value = '100';
  get('#x-import-pages').value = '2';
  get('#x-import-approval').checked = true;
  vm.runInContext('updateXImportQuote()', h.context);
  assert.equal(get('#x-import-approval').checked, false);
  assert.match(get('#x-import-quote').textContent, /2,00 USD/);
  get('#x-import-approval').checked = true;
  await vm.runInContext('confirmXImport()', h.context);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/x/import');
  assert.equal(requests[0].body.usd, 2);
  assert.equal(requests[0].body.maxResults, 100);
  assert.equal(requests[0].body.pages, 2);
  assert.equal(requests[0].body.approval, undefined);
});

test('invalid cursor needs explicit restart and native detail keeps full raw text', () => {
  const h = harness();
  const get = fakeDom(h);
  vm.runInContext("data.xConnection={connected:true}; data.xImport={restartRequired:true}; openXImport()", h.context);
  assert.throws(() => vm.runInContext('xImportParameters()', h.context), /Neustart/);
  get('#x-import-restart').checked = true;
  assert.equal(h.value('xImportParameters().restart'), true);
  vm.runInContext("data.articles[1]={id:'b',origin:'x',originAccountId:'123',postId:'2',url:'https://x.com/i/status/2',title:'Raw',text:'x'.repeat(2000),excerpt:'truncated',author:'',visibility:'protected',limitations:['Video nicht geprüft']}; ui.detailId='b'; renderDetails()", h.context);
  assert.equal(get('#detail-excerpt').textContent.length, 2000);
  assert.equal(get('#detail-fetch-x').hidden, true);
  assert.match(get('#detail-enrichment').textContent, /Video nicht geprüft/);
  assert.match(get('#detail-enrichment').textContent, /geschützt/);
  assert.equal(get('#detail-link').href, 'https://x.com/i/status/2');
});
