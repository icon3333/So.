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
const testButton = new Element('button'); testButton.dataset.action = 'test';
const connectButton = new Element('button'); connectButton.dataset.action = 'connect';
const buttons = [testButton, connectButton];
elements['#provider-form'].querySelector = selector => selector === '.close' ? closeButton : null;
elements['#provider-form'].querySelectorAll = selector => selector === '[data-action]' ? buttons : [];
let calls = [];
let nextResponse;
const location = { origin: 'https://local.test', assigned: null, assign(url) { this.assigned = url; } };
const context = {
  document: {
    querySelector: selector => elements[selector],
    createElement: tag => new Element(tag),
    createTextNode: text => ({ textContent: text })
  },
  window: {location},
  URL,
  fetch: async (url, options = {}) => {
    calls.push({ url, options });
    if (url === '/api/settings') {
      return { ok: true, json: async () => ({ integrations: [
        { id: 'x', name: 'X', kind: 'account', state: 'unconfigured', checkedAt: '2026-01-02', authOrigin:'https://x.com', fields: [{ name: 'clientId', type: 'text' }, {name:'confirmCost',type:'checkbox'}] },
        { id: 'n8n', name: 'n8n', kind: 'import', state: 'readable', fields: [{ name: 'baseUrl', value: 'https://automation.test' }] },
        { id: 'karakeep', name: 'Karakeep', kind: 'account', fields: [{ name: 'baseUrl', value: 'https://automation.test' }] }
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
  assert.equal(cards.length, 3);
  assert.ok(descendants(cards[0]).some(item => item.textContent === 'Nicht eingerichtet'));
  assert.match(html, /<ul id="integrations"/);
  assert.match(html, /<h1 id="page-title">Integration<\/h1>/);
  assert.ok(cards.every(row => row.tagName === 'li'));
  assert.ok(descendants(cards[0]).some(item => item.textContent === 'Verbinden'));
  assert.ok(descendants(cards[1]).some(item => item.textContent === 'Verwalten'));

  const xCardButton = descendants(cards[0]).find(item => item.tagName === 'button');
  xCardButton.listeners.click();
  assert.equal(elements['#provider-dialog'].open, true);
  assert.match(elements['#dialog-message'].textContent, /Zuletzt geprüft: 2026-01-02/);
  const checkbox = descendants(elements['#provider-fields']).find(item => item.name === 'confirmCost');
  assert.equal(checkbox.type, 'checkbox');
  assert.equal(checkbox.checked, false);

  let resolveAction;
  nextResponse = new Promise(resolve => { resolveAction = resolve; });
  const pending = testButton.listeners.click();
  await flush();
  const request = calls.find(call => call.url === '/api/settings/action');
  assert.equal(JSON.parse(request.options.body).fields.confirmCost, false);
  assert.equal(request.options.headers['X-Triage-Request'], '1');
  elements['#provider-dialog'].close();
  resolveAction({ ok: false, json: async () => ({ error: 'Testfehler' }) });
  await flush();
  assert.notEqual(elements['#action-status'].textContent, 'Testfehler', 'closed dialog must ignore stale responses');

  xCardButton.listeners.click();
  nextResponse = { ok: true, json: async () => ({ authorizationUrl: 'https://x.com/i/oauth2/authorize' }) };
  await connectButton.listeners.click();
  await flush();
  assert.equal(location.assigned, 'https://x.com/i/oauth2/authorize', JSON.stringify(calls.map(call => call.url)));
  assert.equal(calls.filter(call => call.url === '/api/settings/action').at(-1).options.method, 'POST');

  console.log('settings UI behavior checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
