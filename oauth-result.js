(() => {
  const {provider, ok} = document.body.dataset;
  history.replaceState(null, '', '/settings');
  if (window.opener) {
    window.opener.postMessage({type: 'so-setup-callback', provider, ok: ok === 'yes'}, window.location.origin);
  } else if (ok === 'yes') {
    window.location.replace('/');
  }
})();
