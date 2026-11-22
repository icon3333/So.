'use strict';
const q = (selector) => document.querySelector(selector);
const node = (tag, text) => Object.assign(document.createElement(tag), {textContent: text});
const labels = {to:'Desktop / To',projects:'Desktop / Projects',keep:'Google Keep',reading:'Inoreader & X',posteo:'Tasks.org / Posteo'};
const notes = {missing:'Ordner fehlt',unconfigured:'Auf diesem Rechner nicht eingerichtet',error:'Abruf fehlgeschlagen',loading:'Wird geladen …'};
const sources = {};
const selected = new Set();
const lifeAreas = {};
const undoStack = [];
const hiddenIds = new Set();
let kind = 'all';
let limit = 40;
function areaOf(item) { return lifeAreas[item.id] || item.lifeArea || ''; }
function safeLink(value) {
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; }
  catch { return null; }
}
function allItems() { return Object.values(sources).flatMap(s=>s.items || []).filter(i=>!hiddenIds.has(i.id)); }
function fillFilter(id, values) {
  const select=q(id), current=select.value;
  const options=[...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b));
  select.replaceChildren(node('option','Alle'));
  select.children[0].value='';
  for(const value of options){const option=node('option',value);option.value=value;select.append(option);}
  select.value=options.includes(current)?current:'';
}
function filteredItems() {
      const search=q('#search').value.trim().toLocaleLowerCase();
  return allItems().filter(i=>(kind==='all'||i.kind===kind) &&
    (!q('#sourceFilter').value || (i.sourceLabel || i.group || labels[i.source])===q('#sourceFilter').value) &&
    (!q('#topicFilter').value || (areaOf(i) || '\0')===q('#topicFilter').value) &&
    (!q('#formatFilter').value || i.format===q('#formatFilter').value) &&
    `${i.title} ${i.group||''} ${areaOf(i)||i.topic||''} ${i.topic||''} ${i.format||''} ${i.author||''} ${excerptOf(i)}`.toLocaleLowerCase().includes(search));
}
function refreshFilters() {
  fillFilter('#sourceFilter',allItems().map(i=>i.sourceLabel || i.group || labels[i.source]));
  const currentArea=q('#topicFilter').value;
  fillFilter('#topicFilter',allItems().map(areaOf));
  const unassigned=node('option','Ohne Zuordnung'); unassigned.value='\0'; q('#topicFilter').append(unassigned);
  if(currentArea==='\0') q('#topicFilter').value=currentArea;
  fillFilter('#formatFilter',allItems().map(i=>i.format));
  q('#areaSuggestions').replaceChildren(...[...new Set(allItems().flatMap(i=>[areaOf(i),i.topic]).filter(Boolean))].sort((a,b)=>a.localeCompare(b)).map(value=>node('option',value)));
}
function render(focusId) {
  q('#sources').replaceChildren(...Object.entries(labels).map(([key,label]) => {
    const s = sources[key];
    const status = !s ? 'Noch nicht geladen' : notes[s.state] || `${s.items.length} Einträge${s.skipped ? ` · ${s.skipped} übersprungen` : ''}`;
    return node('li', `${label}: ${status}`);
  }));
  const items=filteredItems();
  items.sort((a,b)=> Number(Boolean(a.closed || a.archived))-Number(Boolean(b.closed || b.archived)) ||
    (a.due || '~').localeCompare(b.due || '~') || a.title.localeCompare(b.title));

  q('#count').textContent = `${items.length} ${items.length===1?'Eintrag':'Einträge'} · ${selected.size} ausgewählt`;
  if(selected.size) q('#batchTools').open=true;
  q('#more').hidden = limit >= items.length;
  q('#selectionCount').textContent=`${selected.size} ausgewählt`;
  q('#selectVisible').checked=items.length>0 && items.every(i=>selected.has(i.id));
  q('#selectVisible').indeterminate=items.some(i=>selected.has(i.id)) && !q('#selectVisible').checked;
  q('#copyPrompts').disabled=selected.size===0;
  q('#reviewSelected').disabled=selected.size===0;
  q('#assignArea').disabled=selected.size===0;
  q('#deleteSelected').disabled=selected.size===0;
  q('#undoDelete').disabled=undoStack.length===0;
  q('#items').replaceChildren(...items.slice(0,limit).map(i=>{
    const row=node('li',''); row.dataset.id=i.id;
    const pick=document.createElement('input'); pick.type='checkbox'; pick.checked=selected.has(i.id); pick.setAttribute('aria-label',`Auswählen: ${i.title}`);
    pick.addEventListener('change',()=>{pick.checked?selected.add(i.id):selected.delete(i.id);render(i.id);});
    const meta=node('small',[i.sourceLabel || i.group || labels[i.source],i.topic,i.format,i.author,i.due && `Fällig ${i.due}`,i.recurring && 'Wiederkehrend',i.closed && 'Abgeschlossen',i.archived && 'Archiviert',i.attachments && `${i.attachments} Anhänge im Original`].filter(Boolean).join(' · '));
    const title=node('strong',i.title);
    row.append(pick,meta,title);
    const area=document.createElement('select'); area.className='row-area'; area.setAttribute('aria-label',`Lebensbereich: ${i.title}`);
    const unset=node('option','Ohne Zuordnung'); unset.value=''; area.append(unset);
    for(const value of [...new Set([...allItems().map(x=>x.lifeArea||x.topic).filter(Boolean),areaOf(i),lifeAreas[i.id]].filter(Boolean))].sort()) { const opt=node('option',value);opt.value=value;area.append(opt); }
    area.value=areaOf(i); area.addEventListener('change',()=>saveAreas([i.id],area.value)); row.append(area);
    if(i.xContent){
      const x=i.xContent, detail=document.createElement('details');
      detail.append(node('summary','X-Inhalt anzeigen'));
      const body=document.createElement('div');
      for(const [label,value] of [['Zusammenfassung',x.summary],['Thema',x.topic],['Kernaussage',x.takeaway],['Einschätzung',x.assessment],['Posttext',x.text]]) if(value) body.append(node('p',`${label}: ${value}`));
      if(x.limitations?.length) body.append(node('p',`Grenzen: ${x.limitations.join(' ')}`));
      detail.append(body); row.append(detail);
    } else if(i.detail){
      const info=document.createElement('details');
      info.append(node('summary', i.source==='desktop' ? 'Pfad anzeigen' : 'Notiz anzeigen'), node('p',i.detail));
      row.append(info);
    }
    const url=safeLink(i.url);
    if(url){const a=node('a',i.source==='reading'?'Im Original / Leserunde öffnen ↗':'Original öffnen ↗'); a.href=url; a.target='_blank'; a.rel='noopener noreferrer'; row.append(a);}
    return row;
  }));
  if(focusId) [...q('#items').children].find(row=>row.dataset.id===focusId)?.querySelector('input')?.focus();
}
async function load(button, path, keys, headers={}) {
  button.disabled=true;
  for(const k of keys) sources[k]={state:'loading',items:sources[k]?.items || []};
  render();
  try {
    const res=await fetch(path,{headers,cache:'no-store'});
    if(!res.ok) throw new Error();
    const data=await res.json();
    if(path.endsWith('/posteo')) sources.posteo=data;
    else Object.assign(sources,data);
    if(data.areas && typeof data.areas==='object') Object.assign(lifeAreas,data.areas);
  } catch { for(const k of keys) sources[k]={state:'error',items:[]}; }
  finally {button.disabled=false;refreshFilters();render();}
}
const local = () => load(q('#reload'),'/api/cockpit',['to','projects','keep','reading']);
q('#reload').addEventListener('click',local);
q('#posteo').addEventListener('click',()=>load(q('#posteo'),'/api/cockpit/posteo',['posteo'],{'X-Triage-Request':'1'}));
q('#search').addEventListener('input',()=>{limit=40;render();});
for(const id of ['#sourceFilter','#topicFilter','#formatFilter']) q(id).addEventListener('change',()=>{limit=40;render();});
q('#selectVisible').addEventListener('change',()=>{for(const i of filteredItems()) q('#selectVisible').checked?selected.add(i.id):selected.delete(i.id);render();});
function excerptOf(i) { return i.excerpt || i.summary || i.xContent?.summary || i.xContent?.text || 'Kein gespeicherter Auszug'; }
function handoffText(list) {
  return `${list.some(i=>i.kind==='reading')?'Leseliste — ausgewählte Inhalte zur Prüfung':'Arbeitsrunde — ausgewählte Aufgaben und Projekte'} (manuell geteilt; nicht automatisch gesendet)\n\nEigene Nachricht: ${q('#handoffNote').value.trim()||'Keine eigene Nachricht'}\n\n${list.map(i=>`- ${i.title}\n  Quelle: ${i.sourceLabel||i.group||i.source} · Lebensbereich: ${areaOf(i)||'ohne Zuordnung'} · Thema: ${i.topic||'nicht klassifiziert'}\n  Auszug: ${excerptOf(i)}\n  Original: ${safeLink(i.url)||'kein Link'}`).join('\n')}`;
}
q('#reviewSelected').addEventListener('click',()=>{
  const list=allItems().filter(i=>selected.has(i.id));
  const links=list.map(i=>safeLink(i.url)).filter(Boolean);
  q('#reviewLinks').replaceChildren(...links.map(url=>{const a=node('a',url);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}));
  q('#reviewCount').textContent=`${links.length} von ${list.length} Einträgen haben einen öffnungsfähigen Link.`; q('#reviewDialog').showModal();
});
q('#openBatch').addEventListener('click',()=>{
  for(const link of q('#reviewLinks').children) window.open(link.href,'_blank','noopener,noreferrer');
  q('#reviewCount').textContent += ' Falls Tabs blockiert wurden, die Links einzeln öffnen.';
});
async function saveAreas(ids,area) {
  const affected=allItems().filter(i=>ids.includes(i.id));
  try {
    const response=await fetch('/api/cockpit/areas',{method:'POST',headers:{'Content-Type':'application/json','X-Triage-Request':'1'},body:JSON.stringify({ids,lifeArea:area})});
    if(!response.ok) throw Error();
    for(const item of affected) item.lifeArea=area; refreshFilters(); render();
  } catch { q('#count').textContent='Zuordnung konnte nicht lokal gespeichert werden.'; }
}
q('#assignArea').addEventListener('click',()=>{const area=q('#bulkArea').value.trim();if(area)saveAreas([...selected],area);});
q('#deleteSelected').addEventListener('click',()=>{const ids=[...selected];if(!ids.length)return;undoStack.push({type:'hide',ids});ids.forEach(id=>{hiddenIds.add(id);selected.delete(id);});render();});
q('#undoDelete').addEventListener('click',()=>{const entry=undoStack.pop();if(!entry)return;entry.ids.forEach(id=>hiddenIds.delete(id));refreshFilters();render(entry.ids[0]);});
q('#copyPrompts').addEventListener('click',()=>{q('#previewText').value=handoffText(allItems().filter(i=>selected.has(i.id)));q('#previewDialog').showModal();});
q('#copyPreview').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(q('#previewText').value);q('#previewStatus').textContent='In Zwischenablage kopiert.';}catch{q('#previewText').focus();q('#previewText').select();q('#previewStatus').textContent='Kopieren nicht möglich – Text markieren und manuell kopieren.';}});
q('#more').addEventListener('click',()=>{const index=limit;limit+=40;render();const row=q('#items').children[index];if(row){row.tabIndex=-1;row.focus();}});
document.addEventListener('keydown',e=>{if(e.key!=='Delete'||e.defaultPrevented)return;const el=e.target;if(el?.isContentEditable||(['INPUT','TEXTAREA','SELECT'].includes(el?.tagName)&&el.type!=='checkbox')||!selected.size)return;e.preventDefault();q('#deleteSelected').click();});
for(const b of document.querySelectorAll('[data-kind]')) b.addEventListener('click',()=>{
  kind=b.dataset.kind;
  for(const other of document.querySelectorAll('[data-kind]')) other.setAttribute('aria-pressed',String(other===b));
  limit=40;render();
});
local();
