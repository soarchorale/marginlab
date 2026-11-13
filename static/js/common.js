'use strict';
window.marginFetch = async function(url, options={}) {
  const headers = new Headers(options.headers || {});
  if (!['GET','HEAD','OPTIONS'].includes((options.method || 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]').content);
  }
  const response = await fetch(url, {...options, headers, credentials:'same-origin'});
  if (response.status === 401) {
    window.location.replace('/login?next='+encodeURIComponent(window.location.pathname));
    throw new Error('登录已过期，请重新登录');
  }
  return response;
};
// A browser may restore a protected page from its back/forward cache after logout.
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
