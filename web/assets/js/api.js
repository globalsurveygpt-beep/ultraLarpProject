/* LUCY API client. Same origin as the page: the server that serves this page answers /api/*.
   Every error has the shape {status, field, message}; field is the request key the server
   rejected (for example "max_night_h"), or null. */
(function () {
  'use strict';

  class ApiError extends Error {
    constructor(status, field, message) {
      super(message);
      this.status = status;
      this.field = field;
    }
  }

  async function request(method, path, body, signal) {
    let response;
    try {
      response = await fetch(path, {
        method,
        signal,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      throw new ApiError(0, null, 'The LUCY server cannot be reached. Check that it is running.');
    }
    let data = null;
    try { data = await response.json(); } catch (e) { /* non-JSON body: handled below */ }
    if (!response.ok) {
      const err = data && data.error;
      throw new ApiError(response.status, err ? err.field : null,
        err ? err.message : 'The server answered ' + response.status + '.');
    }
    return data;
  }

  const query = params => Object.entries(params)
    .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');

  window.LucyApi = {
    ApiError,
    meta: signal => request('GET', '/api/meta', null, signal),
    instant: (p, signal) => request('GET', '/api/instant?' + query(p), null, signal),
    daySeries: (p, signal) => request('GET', '/api/day-series?' + query(p), null, signal),
    window: (body, signal) => request('POST', '/api/window', body, signal),
    compare: (body, signal) => request('POST', '/api/compare', body, signal),
    search: (body, signal) => request('POST', '/api/search', body, signal),
  };
})();
