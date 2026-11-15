/* Shared state of the two pages (site analysis and comparison): the mission profile and up to
   three comparison sites. Kept in this browser only. Storage can be unavailable (private
   window, blocked site data); then each page simply starts from its defaults. */
(function () {
  'use strict';
  const KEY = 'lucy-state-v1';
  const PROFILE_KEYS = ['start', 'hour', 'days', 'night', 'gap', 'sun', 'height', 'vehicle'];

  function load() {
    try {
      const raw = JSON.parse(window.localStorage.getItem(KEY) || 'null');
      if (!raw || typeof raw !== 'object') return { profile: null, sites: [], site: null };
      const sites = Array.isArray(raw.sites) ? raw.sites
        .filter(p => p && Number.isFinite(p.lat) && Number.isFinite(p.lon)).slice(0, 3) : [];
      let profile = null;
      if (raw.profile && typeof raw.profile === 'object') {
        profile = {};
        for (const k of PROFILE_KEYS) if (raw.profile[k] != null) profile[k] = raw.profile[k];
      }
      const site = raw.site && Number.isFinite(raw.site.lat) && Number.isFinite(raw.site.lon) ? raw.site : null;
      return { profile, sites, site };
    } catch (e) {
      return { profile: null, sites: [], site: null };
    }
  }

  function save(patch) {
    try {
      const next = { ...load(), ...patch };
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch (e) { /* storage unavailable: nothing to keep */ }
  }

  window.LucyStore = { load, save, PROFILE_KEYS };
})();
