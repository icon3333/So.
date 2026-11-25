const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'settings.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, 'settings.css'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, 'settings.js'), 'utf8');
for (const page of ['cockpit.html', 'index.html']) {
  assert.match(fs.readFileSync(path.join(__dirname, page), 'utf8'), /href="\/settings"/);
}
assert.match(html, /href="\/settings\.css"/);
assert.match(html, /src="\/settings\.js"/);
assert.match(html, /Hermes erhält nur Text, den du nach Prüfung manuell kopierst oder teilst/);
assert.match(css, /prefers-reduced-motion/);

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
    this.open = false;
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.type = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  appendChild(child) { this.append(child); return child; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelectorAll(selector) {
    if (selector === 'input') return descendants(this).filter(node => node.tagName === 'input');
    if (selector === '[data-action]') return buttons;
    return [];
  }
  querySelector(selector) {
    if (selector === '.close') return closeButton;
    return null;
  }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
function descendants(node) { return (node.children || []).flatMap(child => [child, ...descendants(child)]); }
const elements = Object.fromEntries(['#integrations', '#provider-dialog', '#provider-form', '#provider-fields', '#action-status', '#dialog-title', '#dialog-message'].map(key => [key, new Element()]));
const closeButton = new Element('button');
const actions = ['setup', 'test', 'resume'];
const buttons = actions.map(action => { const button = new Element('button'); button.dataset.action = action; return button; });
elements['#provider-form'].querySelector = selector => selector === '.close' ? closeButton : null;
elements['#provider-form'].querySelectorAll = selector => selector === '[data-action]' ? buttons : [];
let calls = [];
const popup = { location: { replace(url) { location.assigned = url; } }, document: { body: { append() {} }, createElement() { return new Element('p'); } }, close() { this.closed = true; } };
let nextResponse;
const location = { origin: 'https://local.test', assigned: null, assign(url) { this.assigned = url; } };
const context = {
  document: {
    querySelector: selector => elements[selector],
    createElement: tag => new Element(tag),
    createTextNode: text => ({ textContent: text })
  },
  window: {location, addEventListener(name, fn) { this.messageListener = fn; }, open() { popup.closed = false; return popup; }},
  URL,
  fetch: async (url, options = {}) => {
    calls.push({ url, options });
    if (url === '/api/settings') {
      return { ok: true, json: async () => ({ integrations: [
        { id: 'x', name: 'X', kind: 'account', state: 'unconfigured', checkedAt: '2026-01-02', authOrigin:'https://x.com', fields: [{ name: 'clientId', type: 'text' }, {name:'confirmCost',type:'checkbox'}, ...Object.entries({maxUsd:0.07,maxPages:1,maxResults:5}).map(([name,value])=>({name,value,type:'number'}))] },
        { id: 'inoreader', name: 'Inoreader', kind: 'account', state: 'untested', authOrigin: 'https://n8n.test', fields: [] },
        { id: 'tasksorg', name: 'Tasks.org / Posteo', kind: 'account', state: 'untested', fields: [] },
        { id: 'keep', name: 'Google Keep', kind: 'import', state: 'unconfigured', fields: [{ name: 'path' }] },
        { id: 'to', name: 'Desktop-To-dos', kind: 'import', state: 'unconfigured', fields: [{ name: 'path' }] },
        { id: 'projects', name: 'Projekte', kind: 'import', state: 'unconfigured', fields: [{ name: 'path' }] },
        { id: 'karakeep', name: 'Karakeep', kind: 'account', state: 'untested', fields: [{ name: 'apiKey', type: 'password' }] }
      ] }) };
    }
    return nextResponse;
  }
};
vm.runInNewContext(script, context);
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  await flush();
  const cards = elements['#integrations'].children;
  assert.equal(cards.length, 7);
  assert.ok(descendants(cards[0]).some(item => item.textContent === 'Nicht eingerichtet'));
  assert.match(html, /<ul id="integrations"/);
  assert.match(html, /<h1 id="page-title">Einstellungen<\/h1>/);
  assert.ok(cards.every(row => row.tagName === 'li'));
  assert.ok(descendants(cards[0]).some(item => item.textContent === 'Einrichten'));
  assert.ok(descendants(cards[6]).some(item => item.textContent === 'Einrichten'));
  assert.ok(descendants(cards[6]).some(item => item.textContent.includes('kein Importanbieter')));

  const xCardButton = descendants(cards[0]).find(item => item.tagName === 'button');
  xCardButton.listeners.click();
  assert.equal(elements['#provider-dialog'].open, true);
  assert.match(elements['#dialog-message'].textContent, /Zuletzt geprüft: 2026-01-02/);
  const checkbox = descendants(elements['#provider-fields']).find(item => item.name === 'confirmCost');
  assert.equal(checkbox.type, 'checkbox');
  assert.equal(checkbox.checked, false);

  await buttons[0].listeners.click();
  assert.equal(calls.filter(c => c.url === '/api/settings/action').length, 0);
  checkbox.checked = true;
  let resolveAction;
  nextResponse = new Promise(resolve => { resolveAction = resolve; });
  const pending = buttons[0].listeners.click();
  await flush();
  const request = calls.find(call => call.url === '/api/settings/action');
  if (!request) throw new Error('setup action request was not sent; calls=' + JSON.stringify(calls.map(c => c.url)) + '; status=' + elements['#action-status'].textContent);
  assert.equal(JSON.parse(request.options.body).fields.confirmCost, true);
  assert.equal(request.options.headers['X-Triage-Request'], '1');
  elements['#provider-dialog'].close();
  resolveAction({ ok: false, json: async () => ({ error: 'Testfehler' }) });
  await flush();
  assert.notEqual(elements['#action-status'].textContent, 'Testfehler', 'closed dialog must ignore stale responses');

  xCardButton.listeners.click();
  descendants(elements['#provider-fields']).find(i => i.name === 'confirmCost').checked = true;
  nextResponse = { ok: true, json: async () => ({ authorizationUrl: 'https://x.com/i/oauth2/authorize' }) };
  await buttons[0].listeners.click();
  await flush();
  assert.equal(location.assigned, 'https://x.com/i/oauth2/authorize', JSON.stringify(calls.map(call => call.url)));
  assert.equal(calls.filter(call => call.url === '/api/settings/action').at(-1).options.method, 'POST');
  assert.equal(JSON.parse(calls.filter(call => call.url === '/api/settings/action').at(-1).options.body).action, 'setup');

  descendants(elements['#provider-fields']).find(i => i.name === 'maxUsd').value = '99';
  nextResponse = {ok:true,json:async()=>({state:'connected', imported:1})};
  context.window.messageListener({source:popup, origin:location.origin,data:{type:'so-setup-callback',provider:'x',ok:true}});
  await flush();
  const resumed = JSON.parse(calls.at(-1).options.body);
  assert.equal(resumed.action, 'resume');
  assert.equal(resumed.fields.maxUsd, 0.07);
  for (const card of cards.slice(2)) {
    descendants(card).find(i => i.tagName === 'button').listeners.click();
    await buttons[0].listeners.click();
    assert.equal(JSON.parse(calls.at(-1).options.body).action, 'setup');
  }
  console.log('settings UI behavior checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
