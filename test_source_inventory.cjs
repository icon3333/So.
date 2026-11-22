const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeHarness(inventory, connected = true) {
  const elements = new Map();
const get = selector => { if (!elements.has(selector)) elements.set(selector, {value:'',checked:false,disabled:false,hidden:false,textContent:'',classList:{toggle(){}},setAttribute(name,value){this[name]=value},removeAttribute(name){delete this[name]},replaceChildren(...children){this.children=children},append(){},addEventListener(){},closest(){return {hidden:false}}}); return elements.get(selector); };
  const calls = [];
  const context = vm.createContext({
    URL, Set, Map, Date, Intl, console, Option: function(text, value) { return {text, value}; },
    document: { querySelector: get, querySelectorAll: () => [], activeElement: {closest() { return null; }, dataset:{}}, body: {}, createElement: () => ({style:{},classList:{toggle(){}},setAttribute(){},append(){},addEventListener(){}}) },
    window: {},
  });
  const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("$('#all-topics').addEventListener")), context);
  context.request = async (path, body) => { calls.push({path, body}); return { sourceInventory: inventory }; };
  context.notice = message => { get('#notice').textContent = message; };
  context.render = () => {};
  vm.runInContext(`data = { articles: Array.from({length:7},(_,i)=>({id:String(i),origin:'inoreader',url:'https://example.org/'+i})), state:{version:0,decisions:{}}, sourceInventory: ${JSON.stringify(inventory)}, connection:{connected:${connected}}, xConnection:{connected:true} };`, context);
  get('#open-count').textContent = 7;
  return {context, calls, get, value(expr) { return JSON.parse(vm.runInContext(`JSON.stringify(${expr})`, context)); }};
}

const unknown = {count:null, checkedAt:null, coverage:'unknown', state:'unknown'};
const complete = (count, checkedAt) => ({count, checkedAt, coverage:'complete', state:'complete'});

test('source inventory is independent of local open count and handles unknown, partial, stale', () => {
  const inventory = {x: {count:23,checkedAt:'2026-11-18T10:00:00Z',coverage:'partial',state:'partial'}, inoreader:{count:41,checkedAt:'2026-11-17T10:00:00Z',coverage:'complete',state:'stale'}};
  const h = makeHarness(inventory);
  vm.runInContext('renderSourceInventory()', h.context);
  assert.equal(h.get('#source-inventory-x-count').textContent, '23');
  assert.match(h.get('#source-inventory-x-status').textContent, /unvollständig/i);
  assert.equal(h.get('#source-inventory-inoreader-count').textContent, '41');
  assert.match(h.get('#source-inventory-inoreader-time').textContent, /17\.11\.2026/);
  h.get('#open-count').textContent = 7;
  assert.equal(h.get('#open-count').textContent, 7);
  const u = makeHarness({x:unknown,inoreader:unknown});
  vm.runInContext('renderSourceInventory()', u.context);
  assert.equal(u.get('#source-inventory-x-count').textContent, '–');
  assert.doesNotMatch(u.get('#source-inventory-x-count').textContent, /0/);
});

test('source checks are explicit, source-specific, and never run on initial render', async () => {
  const h = makeHarness({x:unknown,inoreader:complete(41,'2026-11-18T10:00:00Z')});
  vm.runInContext('renderSourceInventory()', h.context);
  assert.deepEqual(h.calls, []);
  await vm.runInContext("checkSourceInventory('inoreader')", h.context);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, '/api/source-inventory/check');
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].body)), {source:'inoreader'});
  h.calls.length = 0;
  await vm.runInContext("checkSourceInventory('x')", h.context);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, '/api/source-inventory/check');
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].body)), {source:'x'});
});

test('X is identified as last-scan snapshot and source cards link to original apps', () => {
  const html = fs.readFileSync(`${__dirname}/index.html`, 'utf8');
  assert.match(html, /source-inventory-x/);
  assert.match(html, /source-inventory-inoreader/);
  assert.ok(html.indexOf('id="main"') < html.indexOf('class="source-inventory"'), 'Overview must be visible in main, not hidden inside connection settings');
  assert.match(html, /https:\/\/x\.com\/i\/bookmarks/);
  assert.match(html, /https:\/\/www\.inoreader\.com\/read-later/);
  assert.match(html, /API-Scan|letzten API-Scan/i);
  assert.match(html, /historisch|Verlauf/i);
  assert.match(fs.readFileSync(`${__dirname}/styles.css`, 'utf8'), /source-inventory-grid/);
  assert.match(fs.readFileSync(`${__dirname}/styles.css`, 'utf8'), /@media\(max-width:640px\)/);
});
