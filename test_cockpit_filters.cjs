const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('life-area filter spans sources and falls back to existing topic metadata', () => {
  const elements = new Map();
  const q = selector => {
    if (!elements.has(selector)) elements.set(selector, {value:'', addEventListener(){}, replaceChildren(...children){this.children=children}, append(child){this.children.push(child)}, firstElementChild:{value:''}});
    return elements.get(selector);
  };
  const context = vm.createContext({document:{querySelector:q, createElement:()=>({append(){}})}, URL, Set, Object, console});
  const source = fs.readFileSync(`${__dirname}/cockpit.js`, 'utf8');
  vm.runInContext(source.slice(0, source.indexOf('const local =')), context);
  vm.runInContext(`sources.desktop={items:[{id:'desk',kind:'task',title:'Plan',source:'desktop',lifeArea:'Home'}]};
    sources.reading={items:[{id:'feed',kind:'reading',title:'Essay',source:'reading',topic:'Home'},{id:'x',kind:'reading',title:'Post',source:'reading',lifeArea:'Work'}]};`, context);
  q('#topicFilter').value = 'Home';
  const ids = vm.runInContext('filteredItems().map(i=>i.id)', context);
  assert.deepEqual(JSON.parse(JSON.stringify(ids)), ['desk', 'feed']);
  vm.runInContext("fillFilter('#topicFilter',allItems().map(i=>i.lifeArea || i.topic))", context);
  assert.equal(q('#topicFilter').children.length, 3);
});

test('default route uses the So overview and preserves explicit reader access', () => {
  const html = fs.readFileSync(`${__dirname}/cockpit.html`, 'utf8');
  const server = fs.readFileSync(`${__dirname}/server.py`, 'utf8');
  assert.match(html, /<title>So\. · Übersicht<\/title>/);
  assert.match(html, /href="\/reader"/);
  assert.match(html, /Lebensbereich/);
  assert.match(server, /'\/': 'cockpit\.html'.*'\/reader': 'index\.html'/);
});
