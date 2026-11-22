const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  const elements = new Map();
  const get = selector => {
    if (!elements.has(selector)) elements.set(selector, {value:'',checked:false,indeterminate:false,disabled:false,hidden:false,textContent:'',classList:{toggle(){}},setAttribute(){},addEventListener(){},replaceChildren(){},append(){}});
    return elements.get(selector);
  };
  const rows = [{id:'a',title:'Alpha',url:'https://example.org/a',source:'Example',origin:'inoreader'},{id:'b',title:'Beta',url:'https://x.com/b',source:'X',origin:'x'},{id:'c',title:'Gamma',url:'https://example.org/c',source:'Example',origin:'inoreader'}];
  const context = vm.createContext({URL,Set,Map,structuredClone,console,document:{querySelector:get,querySelectorAll:()=>[],createElement:()=>({}),body:{},activeElement:{}},initial:{articles:rows,state:{version:0,decisions:{}},savedIds:rows.map(x=>x.id)},navigator:{}});
  const app = fs.readFileSync(`${__dirname}/app.js`,'utf8');
  vm.runInContext(app.slice(0,app.indexOf("$('#all-topics').addEventListener")),context);
  vm.runInContext(`data=initial;remoteIds=new Set(data.savedIds);ui.selected=new Set(['a','c']);ui.search='';ui.status='saved';ui.collection='all';ui.pageSize=1;ui.page=0;`,context);
  return {context,get,value:exp=>JSON.parse(vm.runInContext(`JSON.stringify(${exp})`,context))};
}

test('select filtered includes matches across pages',()=>{const h=harness();vm.runInContext(`ui.search='Example';filteredArticles().filter(a=>!isRemoved(a)).forEach(a=>ui.selected.add(a.id));ui.selectionScope='filtered';`,h.context);assert.deepEqual(h.value('[...ui.selected]'),['a','c']);assert.equal(h.value('visibleArticles().length'),1);});
test('Hermes handoff includes optional note, provenance and selected URLs only',async()=>{const h=harness();h.get('#hermes-note').value='Bitte priorisieren';assert.equal(await vm.runInContext('buildHermesHandoff()',h.context),['Leseliste — ausgewählte Artikel zur Prüfung (manuell geteilt; nicht automatisch gesendet)','','Notiz: Bitte priorisieren','','- Alpha','  Quelle: Example · Herkunft: Inoreader','  URL: https://example.org/a','- Gamma','  Quelle: Example · Herkunft: Inoreader','  URL: https://example.org/c'].join('\n'));});
test('share success never claims delivery; cancellation is not success',async()=>{const h=harness();const messages=[];h.context.notice=message=>messages.push(message);h.context.navigator={share:async()=>{}};await vm.runInContext('shareSelectedWithHermes()',h.context);assert.match(messages.at(-1),/nicht bestätigt/);h.context.navigator={share:async()=>{throw Object.assign(new Error('cancelled'),{name:'AbortError'});}};await vm.runInContext('shareSelectedWithHermes()',h.context);assert.match(messages.at(-1),/abgebrochen/);});
