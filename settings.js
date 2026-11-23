(() => {
  'use strict';

  const root = document.querySelector('#integrations');
  const dialog = document.querySelector('#provider-dialog');
  const form = document.querySelector('#provider-form');
  const fields = document.querySelector('#provider-fields');
  const status = document.querySelector('#action-status');
  const labels = {
    connected: 'Verbunden', readable: 'Lesbar', empty: 'Leer',
    unavailable: 'Nicht verfügbar', unconfigured: 'Nicht eingerichtet',
    untested: 'Noch nicht geprüft', candidate: 'Kandidat geprüft',
    error: 'Prüfung fehlgeschlagen'
  };
  let entries = [];
  let active = null;
  let requestId = 0;

  function fieldInput(field) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    label.append(document.createTextNode(field.label || field.name));
    input.name = field.name;
    input.type = field.type === 'password' || field.type === 'secret'
      ? 'password' : field.type === 'number' ? 'number' : field.type === 'checkbox' ? 'checkbox' : 'text';
    input.required = Boolean(field.required);
    input.autocomplete = 'off';
    if (input.type === 'checkbox') input.checked = Boolean(field.value);
    else input.value = field.value || '';
    input.setAttribute('aria-label', field.label || field.name);
    label.append(input);
    return label;
  }

  async function readResponse(response) {
    let data;
    try { data = await response.json(); }
    catch { throw new Error('Der Server hat keine gültige Antwort gesendet.'); }
    if (!response.ok) throw new Error(data.error || data.message || 'Die Anfrage ist fehlgeschlagen.');
    return data;
  }

  async function refresh() {
    try {
      const response = await fetch('/api/settings', { headers: { Accept: 'application/json' } });
      const data = await readResponse(response);
      if (!Array.isArray(data.integrations)) throw new Error('Ungültige Antwort vom Server.');
      entries = data.integrations;
      render();
    } catch (error) {
      root.replaceChildren();
      const warning = document.createElement('li');
      warning.setAttribute('role', 'alert');
      warning.textContent = error.message;
      root.append(warning);
    }
  }

  async function load() {
    await refresh();
  }

  function render() {
    root.replaceChildren();
    for (const item of entries) {
      const row = document.createElement('li');
      const state = document.createElement('span');
      const title = document.createElement('h2');

      const button = document.createElement('button');
      row.className = 'provider';
      state.className = `state${['connected','readable'].includes(item.state) ? ' verified' : ''}`;
      state.textContent = labels[item.state] || 'Status unbekannt';
      title.textContent = item.name;

      button.type = 'button';
      button.textContent = ['connected','readable','empty'].includes(item.state) ? 'Verwalten' : (item.kind === 'import' ? 'Import einrichten' : 'Verbinden');
      button.addEventListener('click', () => open(item));
      button.setAttribute('aria-label', `${item.name}: ${button.textContent}`);
      row.append(title, state, button);
      root.append(row);
    }
  }

  function open(item) {
    active = item;
    requestId += 1;
    document.querySelector('#dialog-title').textContent = item.name;
    document.querySelector('#dialog-message').textContent = [item.message,
      item.checkedAt ? `Zuletzt geprüft: ${item.checkedAt}` : 'Noch nicht geprüft'].filter(Boolean).join(' · ');
    status.textContent = '';
    fields.replaceChildren();
    for (const field of item.fields || []) fields.append(fieldInput(field));
    for (const button of form.querySelectorAll('[data-action]')) {
      button.textContent = item.kind === 'import' ? (button.dataset.action === 'connect' ? 'Import speichern' : 'Import testen') : (button.dataset.action === 'connect' ? 'Verbinden' : 'Verbindung testen');
      button.disabled = false;
    }
    dialog.showModal();
  }

  function safeAuthorizationUrl(value) {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value, window.location.origin);
      return active?.authOrigin === url.origin && url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  async function run(which) {
    if (!active) return;
    const item = active;
    const id = ++requestId;
    const values = {};
    for (const input of fields.querySelectorAll('input')) {
      if (input.type === 'password' && !input.value) continue;
      values[input.name] = input.type === 'checkbox' ? input.checked : input.value;
    }
    status.textContent = 'Wird ausgeführt …';
    for (const button of form.querySelectorAll('[data-action]')) button.disabled = true;
    try {
      const response = await fetch('/api/settings/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Triage-Request': '1' },
        body: JSON.stringify({ id: item.id, action: which, fields: values })
      });
      const result = await readResponse(response);
      if (id !== requestId || active !== item || !dialog.open) return;
      status.textContent = result.message || 'Aktion abgeschlossen.';
      let authorizationUrl = null;
      if (which === 'connect' && result.authorizationUrl) {
        authorizationUrl = safeAuthorizationUrl(result.authorizationUrl);
        if (!authorizationUrl) status.textContent = 'Der Server hat eine nicht vertrauenswürdige Anmeldeseite zurückgegeben.';
      }
      await refresh();
      if (authorizationUrl) window.location.assign(authorizationUrl);
    } catch (error) {
      if (id === requestId && active === item && dialog.open) status.textContent = error.message;
    } finally {
      if (id === requestId && active === item && dialog.open) {
        for (const button of form.querySelectorAll('[data-action]')) button.disabled = false;
      }
    }
  }

  form.querySelector('.close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { active = null; requestId += 1; });
  for (const button of form.querySelectorAll('[data-action]')) {
    button.addEventListener('click', () => run(button.dataset.action));
  }
  load();


})();
