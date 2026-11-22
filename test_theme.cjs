const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
const themeScript = source.split('// Appearance preference: local browser setting only.')[1];

function browser({ saved = null, dark = false, storageBlocked = false } = {}) {
  const dataset = {};
  const events = {};
  const writes = [];
  const buttons = ['light', 'dark', 'system'].map(mode => ({
    dataset: { themeMode: mode },
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this[name] = callback; },
  }));
  const media = { matches: dark, addEventListener(name, callback) { this[name] = callback; } };
  vm.runInNewContext(themeScript, {
    window: { matchMedia: () => media },
    localStorage: {
      getItem() { if (storageBlocked) throw Error('blocked'); return saved; },
      setItem(key, value) { if (storageBlocked) throw Error('blocked'); writes.push([key, value]); },
    },
    document: {
      documentElement: { dataset },
      querySelectorAll: () => buttons,
      addEventListener(name, callback) { events[name] = callback; },
    },
  });
  events.DOMContentLoaded();
  return { dataset, buttons, writes,
    click: mode => buttons.find(b => b.dataset.themeMode === mode).click(),
    system: value => { media.matches = value; media.change(); } };
}

test('default and invalid saved preference follow system changes without storing a forced mode', () => {
  for (const saved of [null, 'obsolete']) {
    const b = browser({ saved, dark: true });
    assert.equal(b.dataset.theme, 'dark');
    assert.equal(b.dataset.themePreference, 'system');
    b.system(false);
    assert.equal(b.dataset.theme, 'light');
    assert.deepEqual(b.writes, []);
  }
});

test('explicit choice persists, survives reload and ignores subsequent system changes', () => {
  const b = browser();
  b.click('dark');
  assert.deepEqual(b.writes, [['leseliste-theme', 'dark']]);
  b.system(false);
  assert.equal(b.dataset.theme, 'dark');
  assert.equal(browser({ saved: b.writes[0][1] }).dataset.theme, 'dark');
  b.click('light');
  b.system(true);
  assert.equal(b.dataset.theme, 'light');
  assert.equal(b.buttons.filter(button => button.attributes['aria-pressed'] === 'true').length, 1);
});

test('selecting System resumes automatic appearance changes', () => {
  const b = browser({ saved: 'light', dark: true });
  b.click('system');
  assert.equal(b.dataset.theme, 'dark');
  b.system(false);
  assert.equal(b.dataset.theme, 'light');
  assert.deepEqual(b.writes, [['leseliste-theme', 'system']]);
});

test('blocked storage leaves all three appearance controls usable', () => {
  const b = browser({ storageBlocked: true });
  b.click('dark');
  assert.equal(b.dataset.theme, 'dark');
  b.click('light');
  assert.equal(b.dataset.theme, 'light');
  b.click('system');
  b.system(true);
  assert.equal(b.dataset.theme, 'dark');
});
