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
    partial: 'Teilweise importiert', authorization_required: 'Anmeldung ausstehend', error: 'Prüfung fehlgeschlagen'
  };
  let entries = [];
  let active = null;
  let requestId = 0;
  let oauthPopup = null;
  let oauthProvider = null;
  let oauthHandled = false;
  let approvedFields = null;

  function callbackMessage(event) {
    if (!oauthPopup || event.source !== oauthPopup || event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.type !== 'so-setup-callback' || data.provider !== oauthProvider || typeof data.ok !== 'boolean' || oauthHandled) return;
    oauthHandled = true;
    const popup = oauthPopup;
    oauthPopup = null;
    if (data.ok && popup && !popup.closed) popup.close();
    if (!data.ok) {
      if (active && dialog.open) status.textContent = 'Anmeldung fehlgeschlagen. Bitte erneut einrichten.';
      return;
    }
    const provider = oauthProvider;
    oauthProvider = null;
    if (!active || active.id !== provider || !dialog.open) return;
    status.textContent = 'Anmeldung abgeschlossen. Verbindung wird geprüft …';
    run('resume', approvedFields);
  }

  window.addEventListener('message', callbackMessage);

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
    else input.value = field.value ?? '';
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
      const description = document.createElement('p');

      const button = document.createElement('button');
      row.className = `provider${item.id === 'karakeep' ? ' destination' : ''}`;
      state.className = `state${['connected','readable'].includes(item.state) ? ' verified' : ''}`;
      state.textContent = labels[item.state] || 'Status unbekannt';
      title.textContent = item.name;
      description.textContent = item.message || (item.id === 'karakeep' ? 'Speicherziel; kein Importanbieter.' : 'Quelle einrichten und testen.');

      button.type = 'button';
        button.textContent = item.id === 'karakeep' ? 'Einrichten' : 'Einrichten';
      button.addEventListener('click', () => open(item));
      button.setAttribute('aria-label', `${item.name}: ${button.textContent}`);
      row.append(title, description, state, button);
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
      button.textContent = 'Einrichten';
      button.hidden = button.dataset.action !== 'setup';
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

  async function run(which, snapshot = null) {
    if (!active) return;
    const item = active;
    const id = ++requestId;
    const values = snapshot || {};
    for (const input of snapshot ? [] : fields.querySelectorAll('input')) {
      if (input.type === 'password' && !input.value) continue;
      values[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? (input.value === '' ? '' : Number(input.value)) : input.value;
    }
    status.textContent = which === 'setup' ? 'Einrichtung wird gestartet …' : which === 'resume' ? 'OAuth wird fortgesetzt …' : 'Wird getestet …';
    for (const button of form.querySelectorAll('[data-action]')) button.disabled = true;
    try {
      if (which === 'setup' && item.id === 'x') {
        if (values.confirmCost !== true) throw new Error('Bitte den kostenpflichtigen X-OAuth-Identitätscheck ausdrücklich bestätigen.');
        for (const name of ['maxUsd', 'maxPages', 'maxResults']) if (typeof values[name] !== 'number' || !Number.isFinite(values[name])) throw new Error(`${name} muss eine gültige Zahl sein.`);
      }
      if (which === 'resume' && item.id === 'x') {
        if (values.confirmCost !== true) throw new Error('Bitte die X-Kosten ausdrücklich bestätigen.');
        for (const name of ['maxUsd', 'maxPages', 'maxResults']) if (typeof values[name] !== 'number' || !Number.isFinite(values[name])) throw new Error(`${name} muss eine gültige Zahl sein.`);
      }
      if (which === 'resume') {
        if (!['x', 'inoreader'].includes(item.id)) throw new Error('Fortsetzen ist nur für OAuth verfügbar.');
        // Freeze the exact values shown and approved at the click; later UI edits
        // cannot change what this request authorizes.
        const approved = Object.freeze({ ...values });
        const response = await fetch('/api/settings/action', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Triage-Request': '1' }, body: JSON.stringify({ id: item.id, action: 'resume', fields: approved }) });
        const result = await readResponse(response);
        if (id !== requestId || active !== item || !dialog.open) return;
        if (result.authorizationUrl) throw new Error('Fortsetzen darf keine neue Anmeldung starten.');
        status.textContent = result.message || 'Anmeldung geprüft.';
        if (result.state) { const current = entries.find(entry => entry.id === item.id); if (current) current.state = result.state; render(); }
        return;
      }
      // OAuth popups must be opened synchronously in the user gesture; local
      // setup/import actions should not create a throwaway browsing context.
      oauthProvider = ['x', 'inoreader'].includes(item.id) ? item.id : null;
      oauthPopup = oauthProvider ? window.open('about:blank', 'so-oauth-setup', 'popup,width=620,height=740') : null;
      oauthHandled = false;
      approvedFields = oauthProvider ? Object.freeze({...values}) : null;
      if (oauthPopup) {
        const waiting = oauthPopup.document.createElement('p');
        waiting.textContent = 'Weiterleitung zur Anmeldung …';
        oauthPopup.document.body.append(waiting);
      }
      const response = await fetch('/api/settings/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Triage-Request': '1' },
        body: JSON.stringify({ id: item.id, action: which, fields: values })
      });
      const result = await readResponse(response);
      if (id !== requestId || active !== item || !dialog.open) return;
      status.textContent = result.message || 'Aktion abgeschlossen.';
      if (which === 'resume' && result.authorizationUrl) throw new Error('OAuth-Fortsetzen darf keinen neuen Anmeldeschritt starten.');
      let authorizationUrl = null;
      if (which === 'setup' && result.authorizationUrl) {
        authorizationUrl = safeAuthorizationUrl(result.authorizationUrl);
        if (!authorizationUrl) throw new Error('Der Server hat eine nicht vertrauenswürdige Anmeldeseite zurückgegeben.');
      }
      if (result.state) {
        const current = entries.find(entry => entry.id === item.id);
        if (current) { current.state = result.state; current.message = result.message || ''; }
        render();
      }
      if (oauthPopup && authorizationUrl) oauthPopup.location.replace(authorizationUrl);
      else if (authorizationUrl || (['x', 'inoreader'].includes(item.id) && (!oauthPopup || oauthPopup.closed))) { if (oauthPopup) oauthPopup.close(); oauthPopup = null; status.textContent = 'Popup wurde blockiert oder geschlossen. Erlaube Popups und versuche es erneut.'; }
      else if (oauthPopup) { oauthPopup.close(); oauthPopup = null; oauthProvider = null; }
    } catch (error) {
      if (oauthPopup && !oauthHandled) { oauthPopup.close(); oauthPopup = null; oauthProvider = null; }
      if (id === requestId && active === item && dialog.open) status.textContent = error.message;
    } finally {
      if (oauthPopup && oauthPopup.closed) { oauthPopup = null; oauthProvider = null; if (active === item && dialog.open) status.textContent = 'Anmeldefenster wurde geschlossen. Bitte erneut einrichten.'; }
      if (id === requestId && active === item && dialog.open) {
        for (const button of form.querySelectorAll('[data-action]')) button.disabled = false;
      }
    }
  }

  form.querySelector('.close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { active = null; requestId += 1; oauthPopup?.close(); oauthPopup = null; approvedFields = null; });
  for (const button of form.querySelectorAll('[data-action]')) {
    button.addEventListener('click', () => run(button.dataset.action));
  }
  load();


})();
