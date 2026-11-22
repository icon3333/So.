const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const root=__dirname;
function harness() {
 const fields={};
 const element=()=>({textContent:'',hidden:false,disabled:false,children:[],dataset:{},classList:{toggle(){}},setAttribute(){},append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},addEventListener(){},close(){this.open=false}});
 const ctx=vm.createContext({URL,Set,document:{querySelector:k=>fields[k]??=element(),createElement:element}});
 const src=fs.readFileSync(root+'/app.js','utf8');
 vm.runInContext(src.slice(0,src.indexOf("$('#all-topics').addEventListener")),ctx);
 vm.runInContext(`data={articles:[{id:'ir',url:'https://x.com/a/status/1'},{id:'x',origin:'x',postId:'2',originAccountId:'account'}],state:{decisions:{ir:{choice:'remove'},x:{choice:'remove'}}},connection:{connected:false},xConnection:{connected:false}};ui.collection='x';`,ctx);
 return {fields,run:s=>vm.runInContext(s,ctx)};
}
test('both disconnected sources warn above the list even when filtered to X',()=>{
 const h=harness();h.run('renderConnectionWarnings()');
 const text=h.fields['#connection-warnings'].children.map(e=>e.textContent).join(' ');
 assert.match(text,/Inoreader.*nicht verbunden/);assert.match(text,/X.*nicht verbunden/);assert.match(text,/1.*vorgemerkt/);
 h.run('data.connection.connected=true;data.xConnection.connected=true;renderConnectionWarnings()');
 assert.equal(h.fields['#connection-warnings'].hidden,true);
});
test('connection popup only shows missing or explicitly requested sources; settings show both',()=>{
 const h=harness();
 h.run("$('#connection-dialog').open=true; data.connection.connected=true; renderConnectionCards()");
 assert.equal(h.fields['#inoreader-connection-card'].hidden,true);
 assert.equal(h.fields['#x-connection-card'].hidden,false);
 h.run("$('#connection-dialog').dataset.source='inoreader'; data.connection.connected=false; renderConnectionCards()");
 assert.equal(h.fields['#inoreader-connection-card'].hidden,false);
 assert.equal(h.fields['#x-connection-card'].hidden,true);
 h.run("data.connection.connected=true; renderConnectionCards()");
 assert.equal(h.fields['#connection-dialog'].open,false);
 h.run('renderConnectionCards()');
 assert.equal(h.fields['#inoreader-connection-card'].hidden,false);
 assert.equal(h.fields['#x-connection-card'].hidden,false);
});
test('already removed items are excluded and an expired connection warns without pending removals',()=>{
 const h=harness();h.run("data.savedIds=[];remoteIds=new Set([]);renderConnectionWarnings()");
 const text=h.fields['#connection-warnings'].children.map(e=>e.textContent).join(' ');
 assert.match(text,/nicht verbunden/);assert.doesNotMatch(text,/1.*vorgemerkt/);
});
test('unavailable-only previews cannot be enabled by a cost-control change',()=>{
 const h=harness();h.run("ui.preview={items:[{id:'x'}],groups:{inoreader:{count:0,available:true},x:{count:1,available:false}}};updateSyncQuote()");
 assert.equal(h.fields['#confirm-sync'].disabled,true);
 assert.match(h.fields['#confirm-sync'].textContent,/verbinden/i);
});
test('mixed preview labels only actionable removals and names the skipped source',()=>{
 const h=harness();h.run("ui.preview={items:[{id:'ir'},{id:'x'}],groups:{inoreader:{count:1,available:true},x:{count:1,available:false}}};updateSyncQuote()");
 assert.equal(h.fields['#confirm-sync'].disabled,false);
 assert.match(h.fields['#confirm-sync'].textContent,/1.*entfernen.*1.*offen/);
 assert.match(h.fields['#sync-connection-warning'].textContent,/X.*1/);
});
