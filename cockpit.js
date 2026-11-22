'use strict';
const q = (selector) => document.querySelector(selector);
const node = (tag, text) => Object.assign(document.createElement(tag), {textContent: text});
const labels = {to:'Desktop / To',projects:'Desktop / Projects',keep:'Google Keep',reading:'Inoreader & X',posteo:'Tasks.org / Posteo'};
const notes = {missing:'Ordner fehlt',unconfigured:'Auf diesem Rechner nicht eingerichtet',error:'Abruf fehlgeschlagen',loading:'Wird geladen …'};
const sources = {};
const selected = new Set();
let kind = 'all';
let limit = 40;
function safeLink(value) {
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; }
  catch { return null; }
}
function allItems() { return Object.values(sources).flatMap(s=>s.items || []); }
function fillFilter(id, values) {
  const select=q(id), current=select.value;
  const options=[...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b));
  select.replaceChildren(node('option','Alle'));
  select.firstElementChild.value='';
  for(const value of options){const option=node('option',value);option.value=value;select.append(option);}
  select.value=options.includes(current)?current:'';
}
function filteredItems() {
  const search=q('#search').value.trim().toLocaleLowerCase();
  return allItems().filter(i=>(kind==='all'||i.kind===kind) &&
    (!q('#sourceFilter').value || (i.sourceLabel || i.group || labels[i.source])===q('#sourceFilter').value) &&
    (!q('#topicFilter').value || (i.lifeArea || i.topic)===q('#topicFilter').value) &&
    (!q('#formatFilter').value || i.format===q('#formatFilter').value) &&
    `${i.title} ${i.group||''} ${i.lifeArea||i.topic||''} ${i.format||''} ${i.author||''} ${i.detail||''}`.toLocaleLowerCase().includes(search));
}
function refreshFilters() {
  fillFilter('#sourceFilter',allItems().map(i=>i.sourceLabel || i.group || labels[i.source]));
  fillFilter('#topicFilter',allItems().map(i=>i.lifeArea || i.topic));
  fillFilter('#formatFilter',allItems().map(i=>i.format));
}
function render() {
  q('#sources').replaceChildren(...Object.entries(labels).map(([key,label]) => {
    const s = sources[key];
    const status = !s ? 'Noch nicht geladen' : notes[s.state] || `${s.items.length} Einträge${s.skipped ? ` · ${s.skipped} übersprungen` : ''}`;
    return node('li', `${label}: ${status}`);
  }));
  const items=filteredItems();
  items.sort((a,b)=> Number(Boolean(a.closed || a.archived))-Number(Boolean(b.closed || b.archived)) ||
    (a.due || '~').localeCompare(b.due || '~') || a.title.localeCompare(b.title));
  const ids=new Set(items.map(i=>i.id));
  for(const id of selected) if(!ids.has(id)) selected.delete(id);
  q('#count').textContent = items.length ? `${items.length} ${items.length===1?'Eintrag':'Einträge'}` :
    (Object.values(sources).some(s=>s.state==='loading') ? 'Lade Einträge …' : 'Keine Einträge in dieser Ansicht.');
  q('#more').hidden = limit >= items.length;
  q('#selectionCount').textContent=`${selected.size} ausgewählt`;
  q('#selectVisible').checked=items.length>0 && items.every(i=>selected.has(i.id));
  q('#selectVisible').indeterminate=items.some(i=>selected.has(i.id)) && !q('#selectVisible').checked;
  q('#copyPrompts').disabled=selected.size===0;
  q('#reviewSelected').disabled=selected.size===0;
  q('#items').replaceChildren(...items.slice(0,limit).map(i=>{
    const row=node('li',''); row.dataset.id=i.id;
    const pick=document.createElement('input'); pick.type='checkbox'; pick.checked=selected.has(i.id); pick.setAttribute('aria-label',`Auswählen: ${i.title}`);
    pick.addEventListener('change',()=>{pick.checked?selected.add(i.id):selected.delete(i.id);render();});
    const meta=node('small',[i.sourceLabel || i.group || labels[i.source],i.topic,i.format,i.author,i.due && `Fällig ${i.due}`,i.recurring && 'Wiederkehrend',i.closed && 'Abgeschlossen',i.archived && 'Archiviert',i.attachments && `${i.attachments} Anhänge im Original`].filter(Boolean).join(' · '));
    const title=node('strong',i.title);
    row.append(pick,meta,title);
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
  } catch { for(const k of keys) sources[k]={state:'error',items:[]}; }
  finally {button.disabled=false;refreshFilters();render();}
}
const local = () => load(q('#reload'),'/api/cockpit',['to','projects','keep','reading']);
q('#reload').addEventListener('click',local);
q('#posteo').addEventListener('click',()=>load(q('#posteo'),'/api/cockpit/posteo',['posteo'],{'X-Triage-Request':'1'}));
q('#search').addEventListener('input',()=>{limit=40;render();});
for(const id of ['#sourceFilter','#topicFilter','#formatFilter']) q(id).addEventListener('change',()=>{limit=40;render();});
q('#selectVisible').addEventListener('change',()=>{for(const i of filteredItems()) q('#selectVisible').checked?selected.add(i.id):selected.delete(i.id);render();});
function handoffText(list) {
  return `Leseliste — ausgewählte Artikel zur Prüfung (manuell geteilt; nicht automatisch gesendet)\n\nBitte bearbeite diese gespeicherten Inhalte in der Leseliste. Zeige pro Eintrag den aktuellen Originalinhalt, prüfe Kontext und Quellen, fasse knapp zusammen und nenne offene Unsicherheiten. Triff keine automatischen Löschungen oder Synchronisierungen.\n\n${list.map(i=>`- ${i.title}\n  Quelle: ${i.sourceLabel||i.group||i.source} · Herkunft: ${i.sourceLabel||i.group||i.source}; Thema: ${i.topic||'nicht klassifiziert'}; Format: ${i.format||'nicht klassifiziert'}\n  Original: ${safeLink(i.url)||'kein Link'}\n  Übergabehinweis: ${i.xContent?'X-Inhalt ist in der Cockpit-Karte vorhanden; Einschränkungen beachten.':'Inhalt im Original öffnen.'}`).join('\n')}`;
}
q('#copyPrompts').addEventListener('click',async()=>{
  const list=allItems().filter(i=>selected.has(i.id));
  const text=handoffText(list);
  try {await navigator.clipboard.writeText(text);q('#copyPrompts').textContent='Kopiert';setTimeout(()=>q('#copyPrompts').textContent='Übergabe-Prompt kopieren',1500);}
  catch {const box=document.createElement('textarea');box.value=text;document.body.append(box);box.select();document.execCommand('copy');box.remove();}
});
q('#reviewSelected').addEventListener('click',()=>{
  const list=allItems().filter(i=>selected.has(i.id));
  const link=list.find(i=>safeLink(i.url))?.url;
  if(link) window.open(safeLink(link),'_blank','noopener,noreferrer');
  else window.location.href='/';
});
q('#more').addEventListener('click',()=>{const index=limit;limit+=40;render();const row=q('#items').children[index];row.tabIndex=-1;row.focus();});
for(const b of document.querySelectorAll('[data-kind]')) b.addEventListener('click',()=>{
  kind=b.dataset.kind;
  for(const other of document.querySelectorAll('[data-kind]')) other.setAttribute('aria-pressed',String(other===b));
  limit=40;render();
});
local();
