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

test('a pending X membership check prevents a concurrent disconnect request', async () => {
  const h = harness();
  const get = fakeDom(h);
  get('#x-check-posts').value = '100';
  get('#x-check-pages').value = '1';
  get('#x-check-approval').checked = true;
  const paths = [];
  let completeCheck;
  h.context.transport = path => {
    paths.push(path);
    return new Promise(resolve => { completeCheck = resolve; });
  };
  vm.runInContext('data.xConnection = {connected:true}; request = transport;', h.context);
  const pending = vm.runInContext('runXCheck()', h.context);
  assert.equal(h.value('ui.busy'), true);
  await vm.runInContext("perform(() => request('/api/x/disconnect', {}))", h.context);
  assert.deepEqual(paths, ['/api/x/check']);
  assert.equal(h.value('data.xConnection.connected'), true);
  completeCheck({...h.value('data'), xMembership: {complete:true}});
  await pending;
  assert.equal(h.value('ui.busy'), false);
  assert.equal(h.value('data.xConnection.connected'), true);
});

test('row action copy names the article origin for Inoreader and native X', () => {
  const h = harness();
  const elements = [];
  h.context.node = (tag, cls, text = '') => {
    const el = { tag, className: cls, textContent: text, children: [], dataset: {}, setAttribute(name, value) { this[name] = value; }, append(...items) { this.children.push(...items); }, addEventListener() {} };
    elements.push(el); return el;
  };
  vm.runInContext(`renderArticle({id:'ir',origin:'inoreader',url:'https://example.org/ir',title:'IR'}); renderArticle({id:'native',origin:'x',url:'https://x.com/i/status/1',title:'X'})`, h.context);
  assert.deepEqual(elements.filter(e => e.dataset.action === 'karakeep').map(e => e.title), [
    'In Karakeep speichern und danach Entfernung aus Inoreader vormerken',
    'In Karakeep speichern und danach Entfernung aus X vormerken'
  ]);
  assert.deepEqual(elements.filter(e => e.dataset.action === 'remove').map(e => e.title), [
    'Erst nach „Entfernungen prüfen“ und deiner Bestätigung aus Inoreader entfernen',
    'Erst nach „Entfernungen prüfen“ und deiner Bestätigung aus X entfernen'
  ]);
});

test('legacy payload keeps all entries and source provenance is not URL domain', () => {
  const h = harness();
  vm.runInContext(`
    initial.articles[0].url = 'https://x.com/author/status/123';
    applyState(initial);
    ui.facets.collection.clear();
    ui.status = 'all';
  `, h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['a', 'b', 'c']);
  vm.runInContext("setFacet('collection','x')", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), []);
  vm.runInContext("setFacet('collection',''); ui.status = 'saved'; remoteIds = new Set(['b', 'c'])", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a => a.id)'), ['b', 'c']);
  vm.runInContext("initial.articles[1].origin='x'; initial.articles[1].url='https://example.org/non-x'; applyState(initial); setFacet('collection','x'); ui.status='all'", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a => a.id)'), ['b']);
  vm.runInContext("setFacet('collection',''); setFacet('collection','inoreader')", h.context);
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

test('platform classification is independent of bookmark origin and rejects lookalike hosts', () => {
  const h = harness();
  vm.runInContext(`data.articles=[
    {id:'ir-x',origin:'inoreader',url:'https://x.com/u/status/101'},
    {id:'native-x',origin:'x',url:'https://x.com/u/status/101'},
    {id:'video',origin:'inoreader',url:'https://youtu.be/video'},
    {id:'newsletter',origin:'inoreader',url:'https://writer.substack.com/p/story'},
    {id:'html',origin:'inoreader',url:'https://example.org/article'},
    {id:'spoof',origin:'inoreader',url:'https://youtube.com.evil.example/video'}];
    ui.topic=''; ui.source=''; ui.search=''; ui.status='all'`, h.context);
  assert.deepEqual(h.value('data.articles.map(platform)'), ['x','x','youtube','substack','web','web']);
  vm.runInContext("setFacet('platform','x')", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a=>a.id)'), ['ir-x','native-x']);
  vm.runInContext("setFacet('collection','inoreader')", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a=>a.id)'), ['ir-x']);
  vm.runInContext("setFacet('platform',''); setFacet('platform','youtube')", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a=>a.id)'), ['video']);
});

test('reading facets sit above the worklist, not in the topic sidebar', () => {
  const html = fs.readFileSync(`${__dirname}/index.html`, 'utf8');
  const sidebar = html.slice(html.indexOf('<aside'), html.indexOf('</aside>'));
  assert.match(sidebar, /id="topics"/);
  assert.doesNotMatch(sidebar, /data-facet/);
  assert.match(html, /id="reader-facets"[\s\S]+id="original-platform"[\s\S]+id="article-list"/);
});

test('platform changes clear stale selection without changing removal origin', () => {
  const h = harness();
  fakeDom(h);
  vm.runInContext("initial.articles[0].url='https://x.com/u/status/101'; applyState(initial); setFacet('platform','x')", h.context);
  assert.equal(h.value('ui.facets.collection.size'), 0);
  assert.deepEqual(h.value('[...ui.selected]'), []);
  assert.deepEqual(h.value('scopedArticles().map(a=>origin(a))'), ['inoreader']);
  vm.runInContext("setFacet('collection','inoreader')", h.context);
  assert.deepEqual(h.value('[...ui.facets.platform]'), ['x']);
});

test('native records remain visible with Inoreader membership and combined savedIds', () => {
  const h = harness();
  vm.runInContext("initial.articles[1].origin='x'; initial.articles[1].originAccountId='123'; initial.articles[1].postId='2'; initial.state.remote={starredIds:['a','c']}; applyState(initial); ui.status='saved'", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a=>a.id)'), ['a','b','c']);
  vm.runInContext("initial.savedIds=['a','b']; applyState(initial)", h.context);
  assert.deepEqual(h.value('filteredArticles().map(a=>a.id)'), ['a','b']);
});

test('changing provenance clears hidden selection but preserves topic and search', () => {
  const h = harness();
  const get = fakeDom(h);
  vm.runInContext("ui.source='old'; ui.search='old'; ui.topic='Finanzen'; ui.page=2; setFacet('collection','inoreader')", h.context);
  assert.deepEqual(h.value('[...ui.selected]'), []);
  assert.deepEqual(h.value('[ui.page,ui.source,ui.search,ui.topic,...ui.facets.collection]'), [0,'old','old','Finanzen','inoreader']);
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

test('native removal requires fresh preview, explicit cost approval and passes exact bound token', async () => {
  const h=harness(),get=fakeDom(h),sent=[];
  const preview={version:7,catalogRevision:'rev-2',accountId:'acct',confirmationToken:'once',items:[{id:'x1',origin:'x',title:'native'}],groups:{inoreader:{count:0,available:false},x:{count:1,available:true}},xCost:{deleteUsdEach:.010,verificationPostUsdEach:.005,verificationPosts:10,pages:2}};
  h.context.initialPreview=preview;
  h.context.syncTransport=async(path,body)=>{sent.push({path,body});return {result:{confirmed:[],unconfirmed:['x1'],skipped:[],verificationComplete:false,message:'uncertain'}};};
  vm.runInContext("ui.preview=initialPreview; request=syncTransport; perform=async op=>{try{return await op()}catch(e){return null}}",h.context);
  get('#sync-x-posts').value='10';get('#sync-x-pages').value='2';get('#confirm-sync').disabled=false;
  await vm.runInContext('sync()',h.context); assert.equal(sent.length,0);
  get('#sync-x-approval').checked=true; await vm.runInContext('sync()',h.context);
  assert.equal(sent.length,1);assert.equal(sent[0].path,'/api/sync');
  assert.deepEqual(JSON.parse(JSON.stringify(sent[0].body)),{version:7,ids:['x1'],confirmationToken:'once',catalogRevision:'rev-2',posts:0,users:0,mutations:1,verification_posts:10,pages:2,usd:.06});
  assert.equal(h.value('ui.preview'),null);
});

test('read-only X check requires approval and sends zero mutations', async()=>{
  const h=harness(),get=fakeDom(h),sent=[];
  h.context.checkTransport=async(path,body)=>{sent.push({path,body});return {xMembership:{complete:false}};};
  vm.runInContext("data.xConnection={connected:true}; data.xCost={verificationPosts:20,pages:3}; request=checkTransport; perform=async op=>op()",h.context);
  get('#x-check-posts').value='4';get('#x-check-pages').value='2';
  await vm.runInContext('runXCheck()',h.context);assert.equal(sent.length,0);
  get('#x-check-approval').checked=true;await vm.runInContext('runXCheck()',h.context);
  assert.equal(sent[0].path,'/api/x/check');assert.deepEqual(JSON.parse(JSON.stringify(sent[0].body)),{posts:0,users:0,mutations:0,verification_posts:4,pages:2,usd:.02});
  assert.match(h.messages.at(-1),/unbekannt/);
});
test('reading facets compose without changing the bookmark origin or active topic', () => {
  const h = harness();
  fakeDom(h);
  vm.runInContext(`data.articles = [
    {id:'a', topic:'Finanzen', origin:'inoreader', url:'https://youtu.be/vid'},
    {id:'b', topic:'Finanzen', origin:'inoreader', url:'https://news.substack.com/p/ep', format:'Podcast'},
    {id:'c', topic:'Tech', origin:'inoreader', url:'https://youtu.be/vid'},
    {id:'d', topic:'Finanzen', origin:'x', url:'https://x.com/u/status/12'}];
    ui.topic='Finanzen'; setFacet('collection','inoreader'); setFacet('format','video'); setFacet('format','podcast');`, h.context);
  assert.deepEqual(h.value('scopedArticles().map(a=>a.id)'), ['a','b']);
  vm.runInContext("setFacet('platform','substack')", h.context);
  assert.deepEqual(h.value('scopedArticles().map(a=>a.id)'), ['b']);
  assert.deepEqual(h.value("scopedArticles('platform').map(a=>a.id)"), ['a','b']);
  assert.equal(h.value('destinationLabel(data.articles[1])'), 'Inoreader');
  assert.equal(h.value("readerFormat({url:'https://example.org/issue',format:'Newsletter'})"), 'other');
  assert.equal(h.value("readerFormat({url:'https://youtu.be/vid',format:'Article'})"), 'video');
});
