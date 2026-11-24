'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const ui = { kind: '', collection: 'all', platform: '', topic: '', source: '', search: '', status: 'saved', page: 0, pageSize: 10, selected: new Set(), busy: false, detailId: null, preview: null };
let data = null;
let remoteIds = null;
let loginTimer = null;
let loginAttempts = 0;
let detailOpener = null;
let karakeepOpener = null;

function captureFocus() {
  const active = document.activeElement;
  const row = active?.closest('.article-row');
  if (row) return { article: row.dataset.id, action: active.dataset.action || 'title', more: Boolean(active.closest('.row-more')), index: [...row.parentElement.children].indexOf(row) };
  if (active?.dataset.topic) return { topic: active.dataset.topic };
  if (active?.id) return { id: active.id };
  return null;
}

function restoreFocus(target) {
  if (!target) return;
  let element;
  if (target.article) {
    const rows = $$('.article-row');
    const row = rows.find((item) => item.dataset.id === target.article) || rows[Math.min(target.index, rows.length - 1)];
    element = row && [...row.querySelectorAll('button,input,a')].find((item) => item.dataset.action === target.action && !item.disabled);
    if (target.more) element = row?.querySelector('.row-more summary');
    element ||= row?.querySelector('.article-title');
    if (!element) { element = $('#collection-title'); element.tabIndex = -1; }
  } else if (target.topic) element = $$('.topic-button').find((item) => item.dataset.topic === target.topic);
  else if (target.id) element = document.getElementById(target.id);
  if (element && !element.disabled) element.focus({ preventScroll: true });
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function formatDate(value, includeTime = false) {
  if (!value) return 'Datum unbekannt';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return 'Datum unbekannt';
  return new Intl.DateTimeFormat('de-DE', includeTime ? { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' } : { day: '2-digit', month: 'short', year: 'numeric' }).format(date);
}

function notice(message, error = false) {
  $('#notice').textContent = message;
  $('#notice').hidden = !message;
  $('#notice').classList.toggle('error', error);
  $('#notice').setAttribute('role', error ? 'alert' : 'status');
}

async function request(path, body) {
  const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Triage-Request': '1' }, body: JSON.stringify(body) };
  let response;
  try { response = await fetch(path, { ...options, cache: 'no-store' }); }
  catch { throw new Error('Die lokale Anwendung ist nicht erreichbar. Prüfe, ob sie noch läuft, und versuche es erneut.'); }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('Die Anwendung hat keine gültige Antwort geliefert. Bitte erneut versuchen.'); }
  if (!response.ok) {
    const message = typeof result.error === 'string' ? result.error : typeof result.message === 'string' ? result.message : 'Die Aktion konnte nicht abgeschlossen werden.';
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return result;
}

function applyState(result) {
  if (!Array.isArray(result.articles) || !result.state) throw new Error('Der gespeicherte Stand konnte nicht gelesen werden.');
  if (data && result.state.version < data.state.version) return;
  data = { ...result, eligibleIds: Array.isArray(result.eligibleIds) ? result.eligibleIds : result.articles.map(a => a.id) };
  remoteIds = Array.isArray(data.savedIds) ? new Set(data.savedIds) : (Array.isArray(data.state.remote?.starredIds) ? new Set(data.state.remote.starredIds) : null);
  const available = new Set(data.articles.filter((article) => !isRemoved(article)).map((article) => article.id));
  ui.selected = new Set([...ui.selected].filter((id) => available.has(id)));
  render();
}

function choice(article) { return data.state.decisions?.[article.id]?.choice || ''; }
function isRemoved(article) {
  if (article.origin === 'x' && !Array.isArray(data.savedIds)) {
    const remote = data.state.xRemote;
    return Boolean(remote && remote.accountId === article.originAccountId && Array.isArray(remote.starredIds) && !remote.starredIds.includes(article.postId));
  }
  return remoteIds !== null && !remoteIds.has(article.id);
}
function isDecided(article) { return isRemoved(article) || ['keep', 'remove'].includes(choice(article)); }
function topic(article) { return enrichment(article)?.topic || article.topic || 'Ohne Thema'; }
function source(article) { return article.source || article.domain || article.publisher?.title || (article.origin === 'x' ? 'X' : 'Quelle unbekannt'); }
function savedToKarakeep(article) { return Boolean(data.state.karakeepSaved?.[article.id]); }
function karakeepLabel(article) {
  if (!savedToKarakeep(article)) return 'In Karakeep speichern';
  return choice(article) === 'remove' ? 'In Karakeep gespeichert ✓' : 'Gespeichert · erneut vormerken';
}
function karakeepDisabled(article) { return ui.busy || !safeUrl(article.url) || isRemoved(article) || (savedToKarakeep(article) && choice(article) === 'remove'); }
function stats(articles) {
  return {
    total: articles.length,
    decided: articles.filter(isDecided).length,
    keep: articles.filter((a) => !isRemoved(a) && choice(a) === 'keep').length,
    remove: articles.filter((a) => !isRemoved(a) && choice(a) === 'remove').length,
    removed: articles.filter(isRemoved).length,
    open: articles.filter((a) => !isDecided(a)).length,
  };
}

function isXArticle(article) {
  try { return ['x.com', 'www.x.com'].includes(new URL(article.url).hostname); }
  catch { return false; }
}
function origin(article) { return article.origin === 'x' ? 'x' : 'inoreader'; }
function destinationLabel(article) { return origin(article) === 'x' ? 'X' : 'Inoreader'; }
function platform(article) {
  let host;
  try { host = new URL(article.url).hostname.toLowerCase(); }
  catch { return 'web'; }
  if (host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com')) return 'youtube';
  if (['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com'].includes(host)) return 'x';
  if (host === 'substack.com' || host.endsWith('.substack.com')) return 'substack';
  return 'web';
}
function eligible(article) {
  return (ui.collection === 'all' || origin(article) === ui.collection) && (!ui.platform || platform(article) === ui.platform);
}
function enrichment(article) { return data.xContent?.entries?.[article.id] || (article.origin === 'x' && article.summary ? article : null); }
function displayTitle(article) { return enrichment(article)?.title || article.title || 'Ohne Titel'; }
function displayText(article) { const e = enrichment(article); return [e?.summary, e?.text, article.text, article.excerpt, article.author].filter(Boolean).join(' '); }
function scopedArticles() {
  const query = ui.search.toLocaleLowerCase('de');
  return data.articles.filter((a) => eligible(a) && (!ui.topic || topic(a) === ui.topic) && (!ui.source || source(a) === ui.source) && (!query || [displayTitle(a), displayText(a), a.url, a.ref, source(a), topic(a)].join(' ').toLocaleLowerCase('de').includes(query)));
}


// Supplemental cockpit items share the review list, but never reader decisions.
let cockpitLocal = [];
let cockpitAreas = {};
const localHidden = new Set();
const localUndo = new Set();
let readerKeys = new Map();
const areaKey = a => a.originalId || readerKeys.get(a.id);
const allItems = () => [...data.articles, ...cockpitLocal];
function isLocalItem(a) { return a?.localItem === true; }
function localUrl(a) { try { const u = new URL(a.url); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
function matchesCommonFilters(a) {
  const value = id => document.querySelector('#'+id)?.value || '';
  return (!ui.kind || (a.kind || 'reading') === ui.kind) &&
    (!value('area-filter') || (cockpitAreas[areaKey(a)] || a.topic) === value('area-filter')) &&
    (!value('format-filter') || (a.format || a.kind) === value('format-filter'));
}
async function loadCockpit() {
  try {
    const response = await fetch('/api/cockpit', { cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    cockpitAreas = Object.fromEntries(Object.values(payload).flatMap(x => x?.items || []).map(i => [i.id, i.lifeArea || '']).filter(x => x[1]));
    readerKeys = new Map((payload.reading?.items || []).map(i => [i.readerId, i.id]));
    const entries = Object.values(payload).flatMap(x => x?.items || []);
    cockpitLocal = entries.filter(i => i.source !== 'reading').map(i => ({...i, id:`local:${i.id}`, originalId:i.id, localItem:true, url:i.url || '', title:i.title || '(ohne Titel)', sourceLabel:i.sourceLabel || i.group || i.source || 'Lokal', excerpt:i.excerpt || i.detail || ''}));
  } catch {}
}
async function assignAreas(ids, lifeArea) {
  const response = await fetch('/api/cockpit/areas', {method:'POST', headers:{'Content-Type':'application/json','X-Triage-Request':'1'}, body:JSON.stringify({ids, lifeArea})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Bereich konnte nicht gespeichert werden.');
  ids.forEach(id => { if (lifeArea) cockpitAreas[id] = lifeArea; else delete cockpitAreas[id]; });
  render();
}
function hideSelectedLocal() {
  const ids = [...ui.selected];
  ids.forEach(id => { localHidden.add(id); localUndo.add(id); ui.selected.delete(id); }); render();
}
function undoHiddenLocal() {
  localUndo.forEach(id => localHidden.delete(id)); localUndo.clear(); render();
}

function filteredArticles() {
  return [...scopedArticles().filter(a=>!localHidden.has(a.id)&&matchesCommonFilters(a)), ...cockpitLocal.filter(a => !localHidden.has(a.id) && matchesCommonFilters(a) && (!ui.search || `${a.title} ${a.sourceLabel} ${a.excerpt}`.toLowerCase().includes(ui.search.toLowerCase())) && (!ui.source || a.sourceLabel === ui.source))].filter((a) => {
    if (isLocalItem(a)) return ui.collection === 'all' && !ui.platform && !ui.topic && ['all','open','saved'].includes(ui.status);
    if (ui.status === 'all') return true;
    if (ui.status === 'saved') return !isRemoved(a);
    if (ui.status === 'open') return !isDecided(a);
    return !isRemoved(a) && choice(a) === ui.status;
  }).sort((a, b) => ui.status === 'open' && !isLocalItem(a) && !isLocalItem(b) ? Number(choice(a) === 'skip') - Number(choice(b) === 'skip') : 0);
}

function visibleArticles() {
  const articles = filteredArticles();
  ui.page = Math.max(0, Math.min(ui.page, Math.ceil(articles.length / ui.pageSize) - 1));
  return articles.slice(ui.page * ui.pageSize, (ui.page + 1) * ui.pageSize);
}

function resetView() { ui.page = 0; ui.selected.clear(); ui.selectionScope = 'round'; render(); }

function setCollection(collection) {
  ui.collection = collection;
  ui.topic = ''; ui.source = ''; ui.search = ''; ui.status = 'saved';
  $('#search').value = '';
  resetView();
}

function setPlatform(value) {
  ui.platform = value;
  ui.source = '';
  if (ui.topic && !data.articles.some(a => eligible(a) && topic(a) === ui.topic)) ui.topic = '';
  resetView();
}

function xImportParameters() {
  const posts = Number($('#x-import-posts').value);
  const users = Number($('#x-import-users').value);
  const pages = Number($('#x-import-pages').value);
  if (!Number.isInteger(posts) || posts < 1 || posts > 1000 || !Number.isInteger(users) || users < 0 || users > posts || !Number.isInteger(pages) || pages < 1 || pages > 1000) {
    throw new Error('Bitte gültige Obergrenzen eingeben: 1–1000 Beiträge/Seiten und höchstens ebenso viele Autor:innen.');
  }
  if (data.xImport?.restartRequired && !$('#x-import-restart').checked) throw new Error('Dieser Import braucht einen ausdrücklich bestätigten Neustart.');
  return { posts, users, pages, usd: (posts * 5 + users * 10) / 1000,
    verification_posts: 0, mutations: 0, maxPages: pages, maxResults: Math.min(posts, 100),
    restart: Boolean($('#x-import-restart').checked) };
}

function updateXImportQuote() {
  $('#x-import-approval').checked = false;
  $('#confirm-x-import').disabled = true;
  try {
    const params = xImportParameters();
    $('#x-import-quote').textContent = `Höchstens ${params.posts} Posts + ${params.users} Autor:innen, bis zu ${params.pages} Seiten · reservierter Kostenrahmen ${params.usd.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 3 })} USD. Reguläre Tarife, keine Deduplizierungsrabatte angenommen.`;
  } catch (error) { $('#x-import-quote').textContent = error.message; }
}

function openXImport() {
  if (ui.busy || !data.xConnection?.connected) return;
  const prior = data.xImport;
  $('#x-import-description').textContent = prior?.restartRequired
    ? 'Der Cursor ist nicht mehr verwendbar. Starte eine neue Inventur nur mit erneuter Kostenfreigabe. Gesicherte Einträge und Entscheidungen bleiben erhalten.'
    : prior?.scanStarted ? 'Setzt die gespeicherte Teilinventur fort. Bereits bekannte Beiträge können erneut berechnet werden.'
    : 'Startet eine neue Inventur. Doppelte Beiträge können erneut berechnet werden; bestehende Entscheidungen bleiben erhalten.';
  $('#x-import-restart-label').hidden = !prior?.restartRequired;
  $('#x-import-restart').checked = false;
  updateXImportQuote();
  $('#x-import-dialog').showModal();
}

async function confirmXImport() {
  if (ui.busy || !data.xConnection?.connected || !$('#x-import-approval').checked) {
    notice('Bitte den angezeigten Kostenrahmen ausdrücklich bestätigen; X muss verbunden sein.', true);
    return;
  }
  let params;
  try { params = xImportParameters(); }
  catch (error) { notice(error.message, true); return; }
  $('#confirm-x-import').disabled = true;
  const result = await perform(() => request('/api/x/import', params));
  $('#confirm-x-import').disabled = !$('#x-import-approval').checked;
  if (result) {
    $('#x-import-dialog').close();
    notice(result.xImport?.complete ? 'X-Inventur abgeschlossen. Vorhandene Entscheidungen bleiben erhalten.' : 'Teilimport gesichert. Du kannst nach neuer Kostenfreigabe fortsetzen; fehlende Einträge gelten nicht als entfernt.');
  }
}

function renderSourceInventory() {
  const inventory = data.sourceInventory || {};
  for (const source of ['x', 'inoreader']) {
    const item = inventory[source] || {count:null, checkedAt:null, coverage:'unknown', state:'unknown'};
    const count = $(`#source-inventory-${source}-count`);
    const status = $(`#source-inventory-${source}-status`);
    const time = $(`#source-inventory-${source}-time`);
    count.textContent = Number.isSafeInteger(item.count) && item.count >= 0 ? String(item.count) : '–';
    const labels = {complete:'API-Inventar vollständig',partial:'Unvollständig · nur erfasste Einträge',stale:'Veraltet · letzter erfolgreicher Stand',unknown:'Unbekannt · noch nicht erfolgreich geprüft'};
    status.textContent = item.state === 'stale'
      ? `${labels.stale} · ${item.coverage === 'partial' ? 'Unvollständige Abdeckung' : item.coverage === 'complete' ? 'API-Inventar vollständig' : 'Abdeckung unbekannt'} · ${item.checkedAt ? formatDate(item.checkedAt, true) : 'Zeit unbekannt'}`
      : labels[item.state] || labels.unknown;
    time.textContent = item.checkedAt ? `Letzter erfolgreicher Stand: ${formatDate(item.checkedAt, true)}` : 'Erfolgreiche Prüfung: unbekannt';
    const button = $(`#source-inventory-${source}-check`);
    button.disabled = ui.busy || (source === 'inoreader' && !data.connection?.connected);
  }
}

async function checkSourceInventory(source) {
  if (ui.busy || !['inoreader', 'x'].includes(source)) return;
  if (source === 'inoreader' && !data.connection?.connected) return;

  await perform(async () => {
    try { return await request('/api/source-inventory/check', {source}); }
    catch (error) {
      try { applyState(await request('/api/state')); } catch { /* keep the last rendered snapshot */ }
      throw error;
    }
  });
}

function renderTopics() {
  const groups = new Map();
  for (const article of data.articles.filter(eligible)) {
    if (!groups.has(topic(article))) groups.set(topic(article), []);
    groups.get(topic(article)).push(article);
  }
  $('#all-topics').classList.toggle('active', !ui.topic);
  $('#all-topics').setAttribute('aria-pressed', String(!ui.topic));
  $('#topics').replaceChildren();
  for (const [name, articles] of [...groups].sort((a, b) => b[1].length - a[1].length)) {
    const progress = stats(articles);
    const entry = node('div', 'topic-entry');
    const button = node('button', `topic-button${ui.topic === name ? ' active' : ''}`);
    button.type = 'button';
    button.dataset.topic = name;
    button.setAttribute('aria-pressed', String(ui.topic === name));
    button.append(node('span', '', name), node('span', 'small-count', `${progress.decided}/${progress.total}`));
    button.title = `${name}: ${progress.decided} von ${progress.total} entschieden`;
    button.addEventListener('click', () => { ui.topic = name; resetView(); restoreFocus({ topic: name }); });
    const track = node('div', 'topic-mini-track');
    track.setAttribute('aria-hidden', 'true');
    const fill = node('div');
    fill.style.width = `${progress.decided / progress.total * 100}%`;
    track.append(fill);
    entry.append(button, track);
    $('#topics').append(entry);
  }
}

function renderSourceOptions() {
  const names = [...new Set([...data.articles.filter(eligible).map(source), ...cockpitLocal.map(a=>a.sourceLabel)])].sort((a, b) => a.localeCompare(b, 'de'));
  const options = [new Option('Alle Quellen', ''), ...names.map((name) => new Option(name, name))];
  $('#source').replaceChildren(...options);
  $('#source').value = ui.source;
  $('#source').closest('.source-field').hidden = names.length < 2;
}

function decisionLabel(article) {
  if (isRemoved(article)) return `Aus ${origin(article) === 'x' ? 'X-Lesezeichen' : 'Inoreader „Später lesen“'} entfernt`;
  return { keep: 'Behalten', remove: 'Zum Entfernen vorgemerkt', skip: 'Übersprungen · noch offen' }[choice(article)] || 'Noch offen';
}

function renderArticle(article) {
  const row = node('article', `article-row${ui.selected.has(article.id) ? ' selected' : ''}`);
  if (isLocalItem(article)) row.classList.add('local-item-row');
  row.dataset.id = article.id;
  const label = node('label', 'row-select');
  const checkbox = node('input');
  checkbox.type = 'checkbox';
  checkbox.dataset.action = 'select';
  checkbox.checked = ui.selected.has(article.id);
  checkbox.disabled = ui.busy || (!isLocalItem(article) && isRemoved(article));
  checkbox.setAttribute('aria-label', `Artikel auswählen: ${displayTitle(article)}`);
  checkbox.addEventListener('change', () => {
    if (checkbox.checked) ui.selected.add(article.id); else ui.selected.delete(article.id);
    row.classList.toggle('selected', checkbox.checked);
    renderSelection();
  });
  label.append(checkbox);
  const content = node('div', 'article-main');
  const title = node('button', 'article-title', displayTitle(article));
  title.type = 'button';
  title.dataset.action = 'title';
  title.addEventListener('click', () => openDetails(article.id));
  const meta = node('div', 'article-meta');
  meta.append(node('span', 'source-name', source(article)), node('span', 'meta-separator', '·'), node('span', '', formatDate(article.published)), node('span', 'topic-tag', topic(article)));
  if (isLocalItem(article)) meta.append(node('span', 'decision-tag', cockpitAreas[article.originalId] || 'Lokal'));
  if (isLocalItem(article)) meta.append(node('span','decision-tag',article.sourceLabel));
  if (!isLocalItem(article) && (choice(article) || isRemoved(article))) meta.append(node('span', `decision-tag ${isRemoved(article) ? 'removed' : choice(article)}`, decisionLabel(article)));
  if (!enrichment(article)?.summary && article.missingContent) meta.append(node('span', 'missing-tag', 'Inhalt unklar'));
  if (savedToKarakeep(article)) meta.append(node('span', 'decision-tag', 'In Karakeep gespeichert'));
  content.append(title, meta);
  const excerpt = enrichment(article)?.summary || enrichment(article)?.text || article.excerpt;
  if (excerpt) content.append(node('p', 'article-excerpt', excerpt.replace(/\s+/g, ' ').trim()));
  const url = isLocalItem(article) ? localUrl(article) : safeUrl(article.url);
  const links = node('div', 'row-links');
  if (url && !isLocalItem(article)) {
    const original = node('a', 'button secondary row-original', 'Original öffnen ↗');
    original.href = url;
    original.target = '_blank';
    original.rel = 'noopener noreferrer';
    original.dataset.action = 'original';
    links.append(original);
    const save = node('button', 'button keep-button row-karakeep', karakeepLabel(article));
    save.type = 'button';
    save.dataset.action = 'karakeep';
    save.title = `In Karakeep speichern und danach Entfernung aus ${destinationLabel(article)} vormerken`;
    save.setAttribute('aria-label', `${karakeepLabel(article)}: ${displayTitle(article)}`);
    save.disabled = karakeepDisabled(article);
    save.addEventListener('click', () => saveToKarakeep(article.id));
    links.append(save);
  }
  const actions = node('div', 'row-actions');
  if (isLocalItem(article)) { if (url) { const original=node('a','button secondary row-original','Original öffnen ↗'); original.href=url; original.target='_blank'; original.rel='noopener noreferrer'; content.append(original); } const hide = node('button','row-action','×'); hide.type='button'; hide.dataset.action='local-hide'; hide.title='Lokal ausblenden'; hide.setAttribute('aria-label',`Lokal ausblenden: ${article.title}`); hide.addEventListener('click',()=>{ui.selected.add(article.id); hideSelectedLocal();}); actions.append(hide); row.append(label,content,actions); return row; }
  const more = node('details', 'row-more');
  const summary = node('summary', '', 'Weitere');
  summary.setAttribute('aria-label', `Weitere Aktionen: ${displayTitle(article)}`);
  const secondary = node('div', 'row-secondary');
  for (const [action, description] of [['keep', 'Behalten'], ['remove', 'Entfernen vormerken'], ['skip', 'Überspringen'], ['clear', 'Entscheidung zurücksetzen']]) {
    if (action === 'clear' && !choice(article)) continue;
    const button = node('button', `row-action ${action}${choice(article) === action ? ' chosen' : ''}`, description);
    button.type = 'button';
    button.dataset.action = action;
    button.title = action === 'remove' ? `Erst nach „Entfernungen prüfen“ und deiner Bestätigung aus ${destinationLabel(article)} entfernen` : description;
    button.setAttribute('aria-label', `${description}: ${displayTitle(article)}`);
    if (action !== 'clear') button.setAttribute('aria-pressed', String(choice(article) === action));
    button.disabled = ui.busy || isRemoved(article);
    button.addEventListener('click', () => saveDecision([article.id], action));
    (action === 'keep' ? actions : secondary).append(button);
  }
  const details = node('button', 'row-action details', 'Details');
  details.type = 'button';
  details.dataset.action = 'details';
  details.title = 'Details öffnen';
  details.setAttribute('aria-label', `Details öffnen: ${displayTitle(article)}`);
  details.addEventListener('click', () => openDetails(article.id));
  actions.append(details);
  if (links.childElementCount) secondary.append(links);
  more.append(summary, secondary);
  actions.append(more);
  row.append(label, content, actions);
  return row;
}

function renderSelection() {
  const available = visibleArticles().filter((a) => (isLocalItem(a) || eligible(a)) && !isRemoved(a));
  const count = available.filter((a) => ui.selected.has(a.id)).length;
  const filteredAvailable = filteredArticles().filter((a) => (isLocalItem(a) || eligible(a)) && !isRemoved(a));
  const filteredCount = filteredAvailable.filter((a) => ui.selected.has(a.id)).length;
  $('#select-all').checked = available.length > 0 && count === available.length;
  $('#select-all').indeterminate = count > 0 && count < available.length;
  $('#select-all').disabled = ui.busy || !available.length;
  const allFilteredSelected = filteredAvailable.length > 0 && filteredCount === filteredAvailable.length;
  $('#select-filtered').textContent = allFilteredSelected ? 'Gefilterte Auswahl aufheben' : 'Alle gefilterten auswählen';
  $('#select-filtered').disabled = ui.busy || !filteredAvailable.length;
  $('#selection-label').textContent = ui.selectionScope === 'filtered' && filteredCount ? `${filteredCount} gefiltert ausgewählt` : ui.selected.size ? `${ui.selected.size} ausgewählt` : 'Runde auswählen';
  $('#selection-count').textContent = ui.selected.size ? ` (${ui.selected.size})` : '';
  $('#batch-actions').hidden = !ui.selected.size;
  $$('[data-batch]').forEach((button) => { button.disabled = ui.busy || !data.articles.some(a=>ui.selected.has(a.id)); });
  $('#open-selected').disabled = ui.busy || !allItems().some(a=>ui.selected.has(a.id) && localUrl(a));
  $('#share-selected').disabled = ui.busy || !ui.selected.size;
  $('#hermes-note').disabled = ui.busy || !ui.selected.size;
  $('#karakeep-selected').disabled = !data.articles.some((a) => ui.selected.has(a.id) && !karakeepDisabled(a));
  for (const id of ['local-hide','local-undo']) { const el = document.getElementById(id); if (el) el.disabled = ui.busy || (id === 'local-hide' ? !ui.selected.size : !localUndo.size); }
  const assign = document.getElementById('area-assign'); if (assign) assign.disabled = ui.busy || !allItems().some(a=>ui.selected.has(a.id) && areaKey(a));
  $('#karakeep-selected').title = 'Speichert die ausgewählten Links und merkt ihre Entfernung aus der jeweiligen Herkunft vor.';
}

function render() {
  if (!data) return;
  const overall = stats(data.articles.filter(eligible));

  $('#pending-count').textContent = overall.remove;
  $('#saved-status').textContent = ui.busy ? 'Wird verarbeitet …' : '✓ Fortschritt lokal gespeichert';
  renderTopics();
  renderSourceOptions();
  const kindLabels = { reading: 'Lesestoff', task: 'Aufgaben', project: 'Projekte' };
  $('#collection-title').textContent = ui.topic || (ui.collection === 'x' ? 'X-Lesezeichen' : ui.collection === 'inoreader' ? 'Inoreader-Leseliste' : kindLabels[ui.kind] || 'Alle Einträge');
  $$('[data-kind-view]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.kindView === ui.kind)));
  const group = stats(data.articles.filter((a) => eligible(a) && (!ui.topic || topic(a) === ui.topic)));
  $('#topic-progress').textContent = `${group.decided} von ${group.total} entschieden · ${group.open} offen${ui.topic ? ' in diesem Thema' : ' in deiner Sammlung'}`;
  $('#open-count').textContent = stats(scopedArticles()).open;
  $$('.status-tab').forEach((button) => {
    button.classList.toggle('active', button.dataset.status === ui.status);
    button.setAttribute('aria-pressed', String(button.dataset.status === ui.status));
  });
  const filtered = filteredArticles();
  const visible = visibleArticles();
  $('#article-list').replaceChildren(...visible.map(renderArticle));
  for (const [id,values] of [['area-filter', allItems().map(a=>cockpitAreas[areaKey(a)] || a.topic)], ['format-filter',allItems().map(a=>a.format || a.kind)]]) {
    const select = document.getElementById(id), current = select.value;
    select.replaceChildren(new Option('Alle',''), ...[...new Set(values.filter(Boolean))].sort().map(v=>new Option(v,v))); select.value=current;
  }
  if (!visible.length) {
    const empty = node('div', 'empty-state');
    const isAllDone = !ui.search && !ui.source && ui.status === 'open' && group.open === 0;
    empty.append(node('span', 'empty-icon', isAllDone ? '✓' : '⌕'), node('h3', '', isAllDone ? 'Ein Stück leichter.' : 'Hier gibt es gerade keine Artikel.'), node('p', '', isAllDone ? 'Alles in dieser Ansicht ist entschieden. Vorgemerkte Entfernungen kannst du oben prüfen und anschließend in Inoreader bestätigen.' : 'Wähle ein anderes Thema, ändere den Filter oder verkürze deine Suche.'));
    $('#article-list').append(empty);
  }
  const filterCount = [ui.source, $('#area-filter').value, $('#format-filter').value].filter(Boolean).length;
  $('#facet-count').textContent = filterCount ? ` (${filterCount})` : '';
  $('#result-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'Eintrag' : 'Einträge'}`;
  const pages = Math.max(1, Math.ceil(filtered.length / ui.pageSize));
  $('#page-label').textContent = `Runde ${ui.page + 1} von ${pages}`;
  $('#previous-page').disabled = ui.page === 0 || ui.busy;
  $('#next-page').disabled = ui.page + 1 >= pages || ui.busy;
  $('#undo').disabled = ui.busy || !data.state.history?.length;
  $('#preview').disabled = ui.busy || !overall.remove;
  $('#snapshot-label').textContent = `${data.articles.filter(eligible).length} Einträge in dieser Herkunftsansicht · Inoreader-Ausgangsstand: ${formatDate(data.fetchedAt)}${data.state.xRemote?.checkedAt ? ` · X-Inventur: ${formatDate(data.state.xRemote.checkedAt, true)}` : ' · X-Live-Stand ungeprüft'}`;
  $$('[data-collection]').forEach(button => {
    button.classList.toggle('active', button.dataset.collection === ui.collection);
    button.setAttribute('aria-pressed', String(button.dataset.collection === ui.collection));
  });
  $$('[data-platform]').forEach(button => {
    button.classList.toggle('active', button.dataset.platform === ui.platform);
    button.setAttribute('aria-pressed', String(button.dataset.platform === ui.platform));
  });
  const collectionCount = $('#collection-all-count');
  if (collectionCount) collectionCount.textContent = data.articles.length;
  const xCount = $('#collection-x-count');
  if (xCount) xCount.textContent = data.articles.filter(isXArticle).length;
  const connection = data.connection || {};
  renderSourceInventory();
  $('#connection-message').textContent = connection.message || (connection.connected ? 'Verbunden. Du kannst den aktuellen Stand prüfen.' : 'Deine Entscheidungen bleiben bis zur Bestätigung lokal.');
  $('#connection-dot').classList.toggle('connected', Boolean(connection.connected));
  $('#connect').hidden = Boolean(connection.connected);
  $('#connect').disabled = ui.busy || !connection.ready;
  $('#refresh').disabled = ui.busy || !connection.connected;
  const xConnection = data.xConnection || {};
  $('#x-connection-message').textContent = xConnection.message || 'X ist nicht verbunden.';
  $('#x-connection-dot').classList.toggle('connected', Boolean(xConnection.connected));
  $('#x-connect').hidden = Boolean(xConnection.connected);
  $('#x-disconnect').hidden = !xConnection.connected;
  $('#x-import').disabled = ui.busy || !xConnection.connected;
  $('#x-check').disabled = ui.busy || !xConnection.connected;
  $('#x-import').title = xConnection.connected ? 'Import mit eigener Kostenfreigabe starten' : 'Zuerst X unter „Verbindungen“ anmelden';
  const imported = data.xImport;
  $('#x-import-status').textContent = !imported?.count && !imported?.scanStarted ? 'Noch keine X-Lesezeichen importiert.' : `${imported?.count || 0} lokal gespeichert · ${imported?.complete ? 'API-Inventur vollständig' : imported?.restartRequired ? 'Teilimport · expliziter Neustart nötig' : 'Teilimport · Fortsetzung möglich'}`;
  $('#x-import').textContent = imported?.restartRequired ? 'X-Import neu starten' : imported?.scanStarted ? 'X-Import fortsetzen' : 'X-Lesezeichen importieren';
  $('#x-connect').disabled = ui.busy;
  $('#x-disconnect').disabled = ui.busy;
  renderSelection();
  if (ui.detailId && $('#detail-dialog').open) renderDetails();
}

function openKarakeepConnection() {
  karakeepOpener = captureFocus();
  $('#karakeep-key').value = '';
  $('#karakeep-error').hidden = true;
  $('#karakeep-dialog').showModal();
  $('#karakeep-key').focus();
}

async function connectKarakeep() {
  if (ui.busy) return;
  let apiKey = $('#karakeep-key').value.trim();
  $('#karakeep-key').value = '';
  if (!apiKey) { $('#karakeep-error').textContent = 'Bitte deinen Karakeep-Schlüssel eingeben.'; $('#karakeep-error').hidden = false; return; }
  $('#karakeep-error').hidden = true;
  $('#confirm-karakeep').disabled = true;
  $('#karakeep-key').disabled = true;
  $('#close-karakeep').disabled = true;
  const result = await perform(() => {
    const pending = request('/api/karakeep/connect', { apiKey });
    apiKey = '';
    return pending;
  }, 'Karakeep ist verbunden. Wähle jetzt beim gewünschten Artikel „In Karakeep speichern“.');
  $('#confirm-karakeep').disabled = false;
  $('#karakeep-key').disabled = false;
  $('#close-karakeep').disabled = false;
  if (result) $('#karakeep-dialog').close();
  else { $('#karakeep-error').textContent = $('#notice').textContent; $('#karakeep-error').hidden = false; $('#karakeep-key').focus(); }
}

async function saveToKarakeep(id) {
  const article = data.articles.find((item) => item.id === id);
  if (!article || karakeepDisabled(article)) return;
  if (!data.karakeep?.connected) { openKarakeepConnection(); return; }
  const result = await perform(async () => {
    try { return await request('/api/karakeep/save', { version: data.state.version, id }); }
    catch (error) { if (error.status === 401) data.karakeep.connected = false; throw error; }
  }, (response) => response.result?.message || 'In Karakeep gespeichert. Entfernung aus Inoreader ist vorgemerkt und wartet auf deine Bestätigung.');
  if (result) {
    const focusBefore = captureFocus();
    ui.selected.delete(id);
    render();
    if (!document.querySelector('dialog[open]')) restoreFocus(focusBefore);
  }
}

async function saveSelectedToKarakeep() {
  if (ui.busy) return;
  const articles = data.articles.filter((a) => ui.selected.has(a.id) && !karakeepDisabled(a));
  if (!articles.length) return;
  if (!data.karakeep?.connected) { openKarakeepConnection(); return; }
  let saved = 0;
  let failure = '';
  await perform(async () => {
    for (const article of articles) {
      notice(`In Karakeep speichern: ${saved} von ${articles.length} bestätigt …`);
      try {
        const result = await request('/api/karakeep/save', { version: data.state.version, id: article.id });
        ui.selected.delete(article.id);
        applyState(result);
        saved += 1;
      } catch (error) {
        failure = error.message;
        try { applyState(await request('/api/state')); }
        catch { failure += ' Der aktuelle Stand konnte nicht erneut geladen werden.'; }
        if (error.status === 401) data.karakeep.connected = false;
        ui.selected = new Set(articles.slice(saved).filter((a) => !isRemoved(a)).map((a) => a.id));
        break;
      }
    }
    return data;
  });
  notice(`${saved} von ${articles.length} Artikeln in Karakeep gespeichert und zum Entfernen vorgemerkt. ${failure ? `Angehalten: ${failure} Die übrigen Artikel bleiben ausgewählt; es wurde nichts automatisch erneut gesendet.` : 'Die Entfernung aus Inoreader bestätigst du separat über „Entfernungen prüfen“.'}`, Boolean(failure));
}

function buildHermesHandoff() {
  const selected = [...data.articles.filter((article) => ui.selected.has(article.id)), ...cockpitLocal.filter(article => ui.selected.has(article.id))];
  const note = $('#hermes-note').value.trim();
  const lines = ['So. — ausgewählte Einträge zur Prüfung (manuell geteilt; nicht automatisch gesendet)', ''];
  if (note) lines.push(`Notiz: ${note}`, '');
  for (const article of selected) {
    if (isLocalItem(article)) { lines.push(`- ${article.title}`, `  Quelle: ${article.sourceLabel}`, `  URL: ${localUrl(article) || 'keine sichere URL verfügbar'}`); if (article.excerpt) lines.push(`  Auszug: ${String(article.excerpt).slice(0,1500)}`); continue; }
    lines.push(`- ${displayTitle(article)}`, `  Quelle: ${source(article)} · Herkunft: ${origin(article) === 'x' ? 'X' : 'Inoreader'}`, `  URL: ${safeUrl(article.url) || 'keine sichere URL verfügbar'}`);
    const e = enrichment(article);
    const excerpt = e?.text || e?.summary || article.text || article.excerpt;
    if (excerpt) lines.push(`  Auszug: ${String(excerpt).slice(0, 1500)}`);
  }
  return lines.join('\n');
}

async function shareSelectedWithHermes() {
  if (!ui.selected.size) return;
  const text = buildHermesHandoff();
  try {
    if (navigator.share) {
      await navigator.share({ title: 'Leseliste — Hermes-Übergabe', text });
      notice('Das Teilen-Menü wurde geöffnet. Ob die Übergabe zugestellt wurde, ist nicht bestätigt.');
      return;
    }
    await navigator.clipboard.writeText(text);
    notice('In die Zwischenablage kopiert. Jetzt manuell in Hermes einfügen; nichts wurde gesendet.');
  } catch (error) {
    if (error.name === 'AbortError') notice('Teilen abgebrochen. Es wurde keine erfolgreiche Übergabe bestätigt.');
    else {
      notice('Kopieren/Teilen nicht verfügbar. Übergabetext manuell kopieren:', true);
      const field = document.createElement('textarea');
      field.readOnly = true; field.value = text; field.setAttribute('aria-label', 'Übergabetext');
      $('#notice').append(field); field.focus(); field.select();
    }
  }
}

async function perform(operation, message) {
  if (ui.busy) return;
  const focusBefore = captureFocus();
  ui.busy = true;
  render();
  try {
    const result = await operation();
    applyState(result);
    if (message) notice(typeof message === 'function' ? message(result) : message);
    return result;
  } catch (error) {
    if (error.status === 409) {
      try { applyState(await request('/api/state')); }
      catch (reloadError) { notice(reloadError.message, true); return null; }
      notice('Der Stand wurde in einem anderen Fenster geändert. Die aktuelle Version ist geladen; deine letzte Aktion wurde nicht übernommen.', true);
    } else notice(error.message, true);
    return null;
  } finally {
    ui.busy = false;
    render();
    if (!document.querySelector('dialog[open]') && document.activeElement === document.body) restoreFocus(focusBefore);
  }
}

async function saveDecision(ids, nextChoice) {
  const actionable = ids.filter((id) => data.articles.some((a) => a.id === id && !isRemoved(a)));
  if (!actionable.length) return;
  const labels = { keep: 'behalten', remove: 'zum Entfernen vorgemerkt', skip: 'übersprungen – weiterhin offen', clear: 'zurückgesetzt' };
  const result = await perform(() => request('/api/decisions', { version: data.state.version, ids: actionable, choice: nextChoice }), `${actionable.length} Artikel ${labels[nextChoice]}. Lokal gespeichert.`);
  if (result) {
    const focusBefore = captureFocus();
    ui.selected.clear();
    render();
    if (!document.querySelector('dialog[open]') && document.activeElement === document.body) restoreFocus(focusBefore);
  }
}

async function fetchXContent() {
  if (!ui.detailId) return;
  await perform(() => request('/api/fetch-x', { id: ui.detailId }), 'X-Inhalt lokal abgerufen (Rohtext, Status Teilabruf). Eine geprüfte Zusammenfassung wird später ergänzt; es wird nichts erfunden.');
}

function openDetails(id) {
  detailOpener = captureFocus();
  ui.detailId = id;
  renderDetails();
  $('#detail-dialog').showModal();
}

function renderDetails() {
  const article = data.articles.find((a) => a.id === ui.detailId);
  if (!article) { $('#detail-dialog').close(); return; }
  $('#detail-ref').textContent = `ARTIKEL ${article.ref || ''}`;
  $('#detail-title').textContent = displayTitle(article);
  $('#detail-meta').textContent = `Herkunft: ${origin(article) === 'x' ? 'X-Lesezeichen' : 'Inoreader'} · Plattform: ${{youtube:'YouTube',x:'X',substack:'Substack',web:'Webseite / Sonstiges'}[platform(article)]} · ${source(article)} · ${article.author || 'Autor nicht mitgeliefert'} · ${formatDate(article.published || article.createdAt)} · ${topic(article)}`;
  $('#detail-status').textContent = decisionLabel(article);
  $('#detail-karakeep').textContent = karakeepLabel(article);
  $('#detail-karakeep').disabled = karakeepDisabled(article);
  const url = safeUrl(article.url);
  $('#detail-link').hidden = !url;
  if (url) $('#detail-link').href = url; else $('#detail-link').removeAttribute('href');
  const content = enrichment(article);
  const native = origin(article) === 'x';
  $('#detail-dialog .karakeep-note').textContent = `Speichert den Link und merkt anschließend die Entfernung aus ${native ? 'X-Lesezeichen' : 'Inoreader „Später lesen“'} vor. Die Herkunft ändert sich erst nach eigener Vorschau und Bestätigung.`;
  $('#detail-dialog .drawer-note').textContent = 'Entfernen betrifft nur die gespeicherte Zuordnung in der jeweiligen Herkunft, nicht den ursprünglichen Beitrag.';
  $('#detail-excerpt').textContent = content?.text?.trim() || article.text?.trim() || article.excerpt?.trim() || (native ? 'Kein lesbarer Rohtext mitgeliefert; Auswertung fehlt.' : 'Für diesen Artikel ist kein lesbarer Auszug vorhanden.');
  $('#detail-missing').hidden = native ? Boolean(content?.summary) : (!isXArticle(article) || content?.status === 'read');
  $('#detail-missing').textContent = native ? (content?.summary ? 'Zusammenfassung vorhanden; Rohtext und Einschränkungen siehe unten.' : 'Rohtext; Auswertung fehlt. Es wird keine Zusammenfassung erfunden.') : (content ? `X-Inhalt: ${content.status === 'partial' ? 'Teilweise lesbar – Einschränkungen stehen unten.' : content.status === 'unavailable' ? 'Nicht verfügbar. Erneut abrufen ist möglich.' : 'Angereichert.'}` : 'Noch nicht abgerufen. „X-Inhalt abrufen“ lädt nur den Rohtext und erfindet keine Zusammenfassung.');
  const fetchButton = $('#detail-fetch-x');
  fetchButton.hidden = native || !isXArticle(article) || content?.status === 'read' || content?.status === 'partial';
  fetchButton.disabled = ui.busy;
  const fields = enrichment(article) || (native ? article : null);
  const parts = [];
  if (fields) {
    if (fields.title) parts.push(`Titel: ${fields.title}`);
    if (fields.author) parts.push(`Autor (Quelle): ${fields.author}`);
    if (fields.topic) parts.push(`Thema: ${fields.topic}`);
    if (fields.summary) parts.push(`Zusammenfassung des Beitrags: ${fields.summary}`);
    if (fields.takeaway) parts.push(`Takeaway (Redaktion): ${fields.takeaway}`);
    if (fields.assessment) parts.push(`Einschätzung (Redaktion): ${fields.assessment}`);
    if (Array.isArray(fields.limitations) && fields.limitations.length) parts.push(`Grenzen: ${fields.limitations.join('; ')}`);
  }
  if (native) {
    parts.push(`Sichtbarkeit: ${article.visibility === 'public' ? 'öffentlich bestätigt' : article.visibility === 'protected' ? 'geschützt; keine öffentliche Nachladung' : 'ungeklärt; keine öffentliche Nachladung'}`);
    if (fields !== article && Array.isArray(article.limitations)) parts.push(`Abrufgrenzen: ${article.limitations.join('; ')}`);
  }
  $('#detail-enrichment').hidden = !parts.length;
  $('#detail-enrichment').textContent = parts.join('\n');
  const duplicates = Array.isArray(article.duplicateRefs) ? article.duplicateRefs : [];
  $('#detail-duplicates').hidden = !duplicates.length;
  $('#detail-duplicates').textContent = `Dieselbe URL ist auch bei Artikel ${duplicates.join(', ')} gespeichert. Die Einträge bleiben getrennt entscheidbar.`;
  $$('[data-detail]').forEach((button) => {
    button.disabled = ui.busy || isRemoved(article) || (button.dataset.detail === 'clear' && !choice(article));
    if (button.dataset.detail !== 'clear') button.setAttribute('aria-pressed', String(choice(article) === button.dataset.detail));
  });
}

async function showPreview() {
  if (ui.busy) return;
  ui.busy = true;
  render();
  try {
    ui.preview = await request('/api/sync/preview');
    if (!Array.isArray(ui.preview.items)) throw new Error('Die Vorschau konnte nicht geladen werden.');
    const groups = ui.preview.groups || {inoreader:{count:ui.preview.items.length,available:true},x:{count:0,available:false}};
    const xCount = groups.x.count || 0;
    $('#sync-description').textContent = `${groups.inoreader.count} Inoreader-Einträge${groups.inoreader.available ? '' : ' (derzeit nicht verbunden)'} und ${xCount} X-Lesezeichen${groups.x.available ? '' : xCount ? ' (nicht verbunden; bleiben vorgemerkt)' : ''}. Nur verbundene Quellen werden bearbeitet; nicht verfügbare Einträge bleiben vorgemerkt.`;
    $('#sync-items').replaceChildren(...ui.preview.items.map((article) => {
      const item = node('li', '', article.title || 'Ohne Titel');
      item.append(node('span', '', `Herkunft: ${origin(article) === 'x' ? 'X' : 'Inoreader'} · #${article.ref || '–'} · ${source(article)}`));
      return item;
    }));
    $('#sync-error').hidden = true;
    const cost = ui.preview.xCost || {deleteUsdEach:.010,verificationPostUsdEach:.005,verificationPosts:xCount,pages:1};
    $('#sync-x-controls').hidden = !(groups.x.available && xCount);
    $('#sync-x-approval').checked = false;
    $('#sync-x-posts').value = String(cost.verificationPosts);
    $('#sync-x-pages').value = String(cost.pages);
    updateSyncQuote();
    $('#confirm-sync').textContent = `${ui.preview.items.length} vorgemerkte Einträge prüfen`;
    $('#confirm-sync').disabled = !ui.preview.items.length || (!groups.inoreader.available && !(groups.x.available && xCount)) || (groups.x.available && xCount > 0 && !$('#sync-x-approval').checked);
    $('#sync-dialog').showModal();
  } catch (error) { notice(error.message, true); }
  finally { ui.busy = false; render(); }
}

function removalBudget() {
  const groups=ui.preview.groups, xCount=groups?.x?.available?groups.x.count:0;
  const posts=Number($('#sync-x-posts').value), pages=Number($('#sync-x-pages').value);
  if(xCount&&(!Number.isSafeInteger(posts)||posts<1||!Number.isSafeInteger(pages)||pages<1)) throw new Error('Bitte positive ganzzahlige Obergrenzen für Rückprüfposts und Seiten eingeben.');
  const verification_posts=xCount?posts:0;
  const mills=xCount*10+verification_posts*5;
  return {posts:0,users:0,mutations:xCount,verification_posts,pages:xCount?pages:0,usd:mills/1000};
}
function updateSyncQuote() {
  if(!ui.preview)return;
  $('#sync-x-approval').checked=false;
  try { const b=removalBudget(); $('#sync-x-quote').textContent=`${b.mutations} X-DELETE + höchstens ${b.verification_posts} Verifikationsposts / ${b.pages} Seiten · maximal ${b.usd.toFixed(3)} USD. Teilbestätigung ist möglich; keine automatische Wiederholung. Nach jedem Versuch ist eine neue Vorschau nötig.`; }
  catch(e){$('#sync-x-quote').textContent=e.message;}
  $('#confirm-sync').disabled=ui.busy||!ui.preview.items.length||(ui.preview.groups?.x?.available&&ui.preview.groups.x.count>0&&!$('#sync-x-approval').checked);
}

async function sync() {
  if (!ui.preview || ui.busy) return;
  const preview=ui.preview;
  let budget; try{budget=removalBudget();}catch(e){notice(e.message,true);return;}
  const xCount=preview.groups?.x?.available?preview.groups.x.count:0;
  if(xCount&&!$('#sync-x-approval').checked){notice('Bitte den X-Lösch- und Verifikationsrahmen ausdrücklich bestätigen.',true);return;}
  $('#confirm-sync').disabled = true;
  $('#confirm-sync').textContent = 'Wird je Herkunft geprüft …';
  $('#cancel-sync').disabled = true;
  $('#close-sync').disabled = true;
  ui.preview=null;
  const result = await perform(() => request('/api/sync', { version: preview.version, ids: preview.items.map((article) => article.id), confirmationToken: preview.confirmationToken, catalogRevision: preview.catalogRevision, ...budget }));
  $('#cancel-sync').disabled = false;
  $('#close-sync').disabled = false;
  if (result) {
    $('#sync-dialog').close();
    const confirmed = result.result?.confirmed?.length ?? 0;
    const unconfirmed = result.result?.unconfirmed?.length ?? 0;
    notice(result.result?.message || `${confirmed} Entfernungen bestätigt.${unconfirmed ? ` ${unconfirmed} konnten noch nicht bestätigt werden. Prüfe den Live-Stand erneut.` : ''}`, unconfirmed > 0);
    ui.preview = null;
  } else {
    $('#sync-error').textContent = 'Der Versuch wurde nicht bestätigt. Die Einmal-Vorschau ist verbraucht. Bitte schließe den Dialog und lade eine neue Vorschau; es erfolgt kein automatischer Wiederholungsversuch.';
    $('#sync-error').hidden = false;
    $('#confirm-sync').textContent = 'Erneute Vorschau erforderlich';
    $('#confirm-sync').disabled = true;
  }
}

function showXCheck() {
  if(ui.busy||!data.xConnection?.connected)return;
  $('#x-check-posts').value=String(Math.max(100,data.xImport?.count||0));
  $('#x-check-pages').value=String(Math.max(1,Math.ceil((data.xImport?.count||0)/100)));
  updateXCheckQuote(); $('#x-check-dialog').showModal();
}
function xCheckBudget() {
  const posts=Number($('#x-check-posts').value),pages=Number($('#x-check-pages').value);
  if(!Number.isSafeInteger(posts)||posts<1||!Number.isSafeInteger(pages)||pages<1)throw new Error('Bitte positive ganzzahlige Obergrenzen für Prüfposts und Seiten eingeben.');
  return {posts:0,users:0,mutations:0,verification_posts:posts,pages,usd:Math.round(posts*5)/1000};
}
function updateXCheckQuote(){ $('#x-check-approval').checked=false;try{const b=xCheckBudget();$('#x-check-quote').textContent=`Nur lesend: höchstens ${b.verification_posts} Prüfposts / ${b.pages} Seiten · maximal ${b.usd.toFixed(3)} USD. Unvollständige Prüfung lässt die Mitgliedschaft unbekannt.`;}catch(e){$('#x-check-quote').textContent=e.message;}$('#confirm-x-check').disabled=true; }
async function runXCheck(){
  if(ui.busy||!data.xConnection?.connected)return;let budget;try{budget=xCheckBudget();}catch(e){notice(e.message,true);return;}
  if(!$('#x-check-approval').checked){notice('Bitte den reinen Lese-Kostenrahmen ausdrücklich bestätigen.',true);return;}
  const result=await perform(()=>request('/api/x/check',budget));
  if(result){$('#x-check-dialog').close();notice(result.xMembership?.complete?'X-Mitgliedschaft vollständig geprüft.':'X-Prüfung unvollständig: Mitgliedschaft bleibt unbekannt; es wurde kein DELETE gesendet.',!result.xMembership?.complete);}
}

async function pollConnection() {
  if (ui.busy) { loginTimer = setTimeout(pollConnection, 2000); return; }
  try {
    applyState(await request('/api/state'));
    if (data.connection?.connected) { notice('Inoreader ist verbunden. Prüfe den Live-Stand, bevor du Entfernungen bestätigst.'); loginTimer = null; return; }
  } catch { /* A temporary connection error must not erase local decisions. */ }
  loginAttempts += 1;
  if (loginAttempts < 60) loginTimer = setTimeout(pollConnection, 3000);
  else { loginTimer = null; notice('Die Anmeldung ist noch nicht bestätigt. Nach der Anmeldung kannst du diese Seite neu laden.'); }
}

async function connect() {
  if (ui.busy) return;
  // Reserve the tab during the user gesture so browser popup protection permits login.
  const loginWindow = window.open('about:blank', '_blank');
  if (loginWindow) loginWindow.opener = null;
  ui.busy = true;
  render();
  try {
    const result = await request('/api/connect', {});
    const url = safeUrl(result.url);
    if (!url) throw new Error('Die Anmeldeadresse ist ungültig.');
    if (loginWindow) loginWindow.location.replace(url);
    else {
      notice('Der Browser hat das Anmeldefenster blockiert. Öffne die Anmeldung hier:');
      const link = node('a', '', 'Inoreader-Anmeldung öffnen');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      $('#notice').append(document.createTextNode(' '), link);
    }
    if (loginWindow) notice('Die Inoreader-Anmeldung ist in einem neuen Tab geöffnet. Diese Seite wartet auf die Verbindung.');
    if (loginTimer) clearTimeout(loginTimer);
    loginAttempts = 0;
    loginTimer = setTimeout(pollConnection, 2000);
  } catch (error) { if (loginWindow) loginWindow.close(); notice(error.message, true); }
  finally { ui.busy = false; render(); }
}

$('#all-topics').addEventListener('click', () => { ui.topic = ''; resetView(); });
$$('[data-collection]').forEach(button => button.addEventListener('click', () => setCollection(button.dataset.collection)));
$('#search').addEventListener('input', (event) => { ui.search = event.target.value.trim(); if (data) resetView(); });
$('#source').addEventListener('change', (event) => { ui.source = event.target.value; resetView(); });
$$('[data-kind-view]').forEach((button) => button.addEventListener('click', () => {
  ui.kind = button.dataset.kindView;
  ui.platform = '';
  $('#area-filter').value = '';
  $('#format-filter').value = '';
  setCollection('all');
}));
for (const id of ['area-filter', 'format-filter']) document.getElementById(id)?.addEventListener('change', resetView);
document.getElementById('area-assign')?.addEventListener('click', async () => { try { await assignAreas(allItems().filter(a=>ui.selected.has(a.id)).map(areaKey).filter(Boolean), document.getElementById('area-entry')?.value || ''); } catch(e) { notice(e.message,true); } });
document.getElementById('local-hide')?.addEventListener('click',hideSelectedLocal);
document.getElementById('local-undo')?.addEventListener('click',undoHiddenLocal);
document.getElementById('posteo-load')?.addEventListener('click',async()=>{ try { const r=await fetch('/api/cockpit/posteo',{headers:{'X-Triage-Request':'1'}}); const payload=await r.json(); if(!r.ok) throw new Error(payload.error||'Posteo-Abruf fehlgeschlagen.'); const items=payload.items||[]; cockpitLocal=[...cockpitLocal.filter(a=>a.source!=='posteo'),...items.map(i=>({...i,id:`local:${i.id}`,originalId:i.id,localItem:true,url:i.url||'',title:i.title||'(ohne Titel)',sourceLabel:'Posteo',excerpt:i.detail||''}))]; items.forEach(i=>{if(i.lifeArea)cockpitAreas[i.id]=i.lifeArea;}); render(); } catch(e){notice(e.message,true);} });
$$('[data-platform]').forEach(button => button.addEventListener('click', () => setPlatform(button.dataset.platform)));
$('#page-size').addEventListener('change', (event) => { ui.pageSize = Number(event.target.value); resetView(); });
$$('.status-tab').forEach((button) => button.addEventListener('click', () => { ui.status = button.dataset.status; resetView(); }));
$('#select-all').addEventListener('change', (event) => {
  ui.selectionScope = 'round';
  visibleArticles().filter((a) => !isRemoved(a)).forEach((article) => { if (event.target.checked) ui.selected.add(article.id); else ui.selected.delete(article.id); });
  render();
});
$('#select-filtered').addEventListener('click', () => {
  const available = filteredArticles().filter((a) => !isRemoved(a));
  const allSelected = available.length > 0 && available.every((article) => ui.selected.has(article.id));
  available.forEach((article) => allSelected ? ui.selected.delete(article.id) : ui.selected.add(article.id));
  ui.selectionScope = allSelected ? 'round' : 'filtered';
  render();
});
$$('[data-batch]').forEach((button) => button.addEventListener('click', () => saveDecision(data.articles.filter(a=>ui.selected.has(a.id)).map(a=>a.id), button.dataset.batch)));
$$('[data-detail]').forEach((button) => button.addEventListener('click', () => saveDecision([ui.detailId], button.dataset.detail)));
$('#previous-page').addEventListener('click', () => { ui.page -= 1; ui.selected.clear(); render(); });
$('#next-page').addEventListener('click', () => { ui.page += 1; ui.selected.clear(); render(); });
$('#undo').addEventListener('click', () => perform(() => request('/api/undo', { version: data.state.version }), 'Die letzte lokale Entscheidung wurde rückgängig gemacht. Bereits bestätigte Entfernungen in Inoreader werden nicht wiederhergestellt.'));
$('#refresh').addEventListener('click', () => perform(() => request('/api/refresh', {}), 'Der aktuelle Stand von „Später lesen“ wurde geprüft.'));
$('#source-inventory-inoreader-check').addEventListener('click', () => checkSourceInventory('inoreader'));
$('#source-inventory-x-check').addEventListener('click', () => checkSourceInventory('x'));
$('#open-selected').addEventListener('click', () => {
  const urls = [...new Set([...data.articles.filter((a) => ui.selected.has(a.id)), ...cockpitLocal.filter(a => ui.selected.has(a.id))].map((a) => isLocalItem(a) ? localUrl(a) : safeUrl(a.url)).filter(Boolean))];
  let opened = 0;
  for (const url of urls) {
    const tab = window.open('about:blank', '_blank');
    if (!tab) break;
    tab.opener = null;
    tab.location.replace(url);
    opened += 1;
  }
  notice(opened === urls.length ? `${opened} Originale in neuen Tabs geöffnet.` : `${opened} von ${urls.length} Originalen geöffnet. Der Pop-up-Schutz blockiert die weiteren Tabs. In Firefox: oben im Pop-up-Hinweis „Preferences“ / „Einstellungen“ wählen und Pop-ups für 127.0.0.1 erlauben. Alternativ die übrigen Originale hier einzeln öffnen:`);
  for (const url of urls.slice(opened)) {
    const link = node('a', 'blocked-original', url);
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    $('#notice').append(link);
  }
});
$('#share-selected').addEventListener('click', shareSelectedWithHermes);
window.addEventListener('keydown', (event) => {
  if (event.key !== 'Delete' || event.repeat || event.isComposing || event.defaultPrevented || event.altKey || event.metaKey || event.ctrlKey || event.shiftKey || ui.busy || !ui.selected.size || document.querySelector('dialog[open]')) return;
  if (event.target.closest?.('input:not([type=checkbox]),textarea,select,[contenteditable]')) return;
  event.preventDefault(); saveDecision(data.articles.filter(a=>ui.selected.has(a.id)).map(a=>a.id), 'remove');
});
$('#connect').addEventListener('click', connect);
$('#x-connect').addEventListener('click', async () => {
  if (ui.busy) return;
  if (!$('#x-user-cost-approval').checked) { notice('Der Identitätsabruf kostet bis zu 0,01 USD. Bitte ausdrücklich freigeben.', true); return; }
  ui.busy = true; render();
  try {
    const result = await request('/api/x/connect', { clientId: $('#x-client-id').value, users: 1, posts: 0, mutations: 0, verification_posts: 0, usd: 0.01 });
    const url = safeUrl(result.url);
    if (!url || new URL(url).hostname !== 'x.com') throw new Error('Die X-Anmeldeadresse ist ungültig.');
    window.location.assign(url);
  } catch (error) { notice(error.message, true); }
  finally { ui.busy = false; render(); }
});
$('#x-import').addEventListener('click', openXImport);
$('#x-check').addEventListener('click', showXCheck);
for(const id of ['x-check-posts','x-check-pages']) $(`#${id}`).addEventListener('input',updateXCheckQuote);
$('#x-check-approval').addEventListener('change',()=>{try{xCheckBudget();$('#confirm-x-check').disabled=ui.busy||!$('#x-check-approval').checked;}catch{$('#confirm-x-check').disabled=true;}});
$('#confirm-x-check').addEventListener('click',runXCheck);
for(const id of ['close-x-check','cancel-x-check']) $(`#${id}`).addEventListener('click',()=>{if(!ui.busy)$('#x-check-dialog').close();});
for(const id of ['sync-x-posts','sync-x-pages']) $(`#${id}`).addEventListener('input',updateSyncQuote);
$('#sync-x-approval').addEventListener('change',()=>{try{removalBudget();$('#confirm-sync').disabled=ui.busy||!ui.preview?.items?.length||(ui.preview.groups?.x?.count>0&&ui.preview.groups.x.available&&!$('#sync-x-approval').checked);}catch{$('#confirm-sync').disabled=true;}});
$('#confirm-x-import').addEventListener('click', confirmXImport);
for (const id of ['x-import-posts', 'x-import-users', 'x-import-pages', 'x-import-restart']) $(`#${id}`).addEventListener('input', updateXImportQuote);
$('#x-import-approval').addEventListener('change', () => {
  try { xImportParameters(); $('#confirm-x-import').disabled = ui.busy || !$('#x-import-approval').checked; }
  catch { $('#confirm-x-import').disabled = true; }
});
for (const id of ['close-x-import','cancel-x-import']) $(`#${id}`).addEventListener('click', () => { if (!ui.busy) $('#x-import-dialog').close(); });
$('#x-import-dialog').addEventListener('cancel', (event) => { if (ui.busy) event.preventDefault(); });
$('#x-disconnect').addEventListener('click', () => perform(() => request('/api/x/disconnect', {}), 'X-Verbindung getrennt.'));
$('#karakeep-selected').addEventListener('click', saveSelectedToKarakeep);
$('#detail-karakeep').addEventListener('click', () => saveToKarakeep(ui.detailId));
$('#detail-fetch-x').addEventListener('click', fetchXContent);
$('#confirm-karakeep').addEventListener('click', connectKarakeep);
$('#karakeep-key').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); connectKarakeep(); } });
$('#close-karakeep').addEventListener('click', () => $('#karakeep-dialog').close());
$('#karakeep-dialog').addEventListener('cancel', (event) => { if (ui.busy) event.preventDefault(); });
$('#karakeep-dialog').addEventListener('close', () => { $('#karakeep-key').value = ''; restoreFocus(karakeepOpener); karakeepOpener = null; });
$('#preview').addEventListener('click', showPreview);
$('#close-detail').addEventListener('click', () => $('#detail-dialog').close());
$('#detail-dialog').addEventListener('close', () => { ui.detailId = null; restoreFocus(detailOpener); detailOpener = null; });
for (const id of ['close-sync', 'cancel-sync']) $(`#${id}`).addEventListener('click', () => $('#sync-dialog').close());
$('#sync-dialog').addEventListener('cancel', (event) => { if (ui.busy) event.preventDefault(); });
$('#x-check-dialog').addEventListener('cancel',(event)=>{if(ui.busy)event.preventDefault();});
$('#confirm-sync').addEventListener('click', sync);

$('#select-all').disabled = true;
request('/api/state').then(async state => { applyState(state); await loadCockpit(); render(); }).catch((error) => {
  $('#saved-status').textContent = 'Stand nicht geladen';
  notice(error.message, true);
  const empty = node('div', 'empty-state');
  empty.append(node('h3', '', 'Die Sammlung konnte nicht geladen werden.'), node('p', '', 'Starte die lokale Anwendung und lade diese Seite erneut.'));
  $('#article-list').replaceChildren(empty);
});
