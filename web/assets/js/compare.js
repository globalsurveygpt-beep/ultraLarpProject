/* LUCY comparison page: one mission window and profile, two or three sites, one POST /api/compare.
   Every number comes from the API. Profile and sites are shared with the site page (LucyStore). */
'use strict';

const $ = id => document.getElementById(id);
const Api = window.LucyApi, Store = window.LucyStore;
const EXAMPLES = [{ lat: -88.9, lon: 43.2 }, { lat: -88.8, lon: 55.2 }, { lat: -88.7, lon: 67.2 }];  // example coordinates, not recommendations
const PROFILE_IDS = ['start', 'hour', 'days', 'night', 'gap', 'sun', 'height'];
// API request keys -> form ids, for showing the server's error at the right field.
const FIELD_IDS = { start_utc: 'start', duration_days: 'days', max_night_h: 'night', max_comms_gap_h: 'gap', min_sun_pct: 'sun', observer_height_m: 'height' };
const TITLES = { max_night_h: 'Night margin', min_sun_pct: 'Sun share margin', max_comms_gap_h: 'Comms gap margin' };

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = v => v == null ? '—' : Number.isInteger(v) ? String(v) : String(+Number(v).toFixed(2));
const pad = n => String(n).padStart(2, '0');
const utc = ms => new Date(ms).toISOString().slice(0, 16).replace('T', ' ');
const letter = i => String.fromCharCode(65 + i);

let sites = [], results = null, ctl = null;

function notify(text) {
  const n = $('notification'); n.textContent = text; n.hidden = false;
  clearTimeout(notify.t); notify.t = setTimeout(() => { n.hidden = true; }, 3500);
}
function message(text) { $('comparison-message').textContent = text || ''; $('comparison-message').hidden = !text; }

function config() {
  const c = {};
  for (const id of PROFILE_IDS) c[id] = id === 'start' ? $(id).value.trim() : parseFloat($(id).value);
  return c;
}
const startUtc = c => c.start + 'T' + pad(c.hour) + ':00:00Z';
const profileOf = c => ({ duration_days: c.days, max_night_h: c.night, max_comms_gap_h: c.gap, min_sun_pct: c.sun, observer_height_m: c.height });

function clearErrors() {
  document.querySelectorAll('[aria-invalid=true]').forEach(n => { n.removeAttribute('aria-invalid'); n.removeAttribute('aria-describedby'); });
  document.querySelectorAll('.field-error').forEach(n => { n.textContent = ''; });
}
function fieldError(id, text) {
  const input = $(id), error = $('error-' + id);
  if (!input) return false;
  input.setAttribute('aria-invalid', 'true');
  if (error) { error.textContent = text; input.setAttribute('aria-describedby', 'error-' + id); }
  input.focus();
  return true;
}
/* Only checks that fields hold numbers; ranges and rules are the server's. */
function readForm() {
  clearErrors();
  const c = config();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(c.start)) { fieldError('start', 'Choose a date.'); return null; }
  if (!Number.isInteger(c.hour) || c.hour < 0 || c.hour > 23) { fieldError('hour', 'Enter a whole hour from 0 to 23.'); return null; }
  for (const id of ['days', 'night', 'gap', 'sun', 'height']) if (!Number.isFinite(c[id])) { fieldError(id, 'Enter a number.'); return null; }
  for (let i = 0; i < sites.length; i++) for (const axis of ['lat', 'lon'])
    if (!Number.isFinite(sites[i][axis])) { fieldError(`site-${i}-${axis}`, 'Enter a number.'); return null; }
  return c;
}

function save() {
  const c = config();
  Store.save({ profile: { start: c.start, hour: c.hour, days: c.days, night: c.night, gap: c.gap, sun: c.sun, height: c.height }, sites });
}
function stale() {
  if (ctl) { ctl.abort(); ctl = null; setBusy(false); }
  if (results) { $('comparison-matrix').dataset.stale = 'true'; message('Settings changed. Compare again to update.'); }
  save();
}
function setBusy(busy) {
  $('compare-run').disabled = busy || sites.length < 2;
  $('compare-run').innerHTML = busy ? '<span class="spinner" aria-hidden="true"></span>Comparing…' : 'Compare sites →';
}

function renderCandidates() {
  for (const id of ['candidate-form', 'comparison-timelines']) $(id).style.setProperty('--site-count', Math.max(2, sites.length));
  $('candidate-form').innerHTML = sites.map((p, i) => `<section class="panel candidate-editor"><div class="panel-head"><h2><span class="site-letter">${letter(i)}</span> Site ${letter(i)}</h2>`
    + `<button type="button" data-remove="${i}" aria-label="Remove site ${letter(i)}">Remove</button></div><div class="pair">`
    + `<div><label for="site-${i}-lat">Latitude [°]</label><input id="site-${i}-lat" data-site="${i}" data-axis="lat" type="number" step="0.0001" value="${esc(p.lat)}" required><span class="field-error" id="error-site-${i}-lat" role="alert"></span></div>`
    + `<div><label for="site-${i}-lon">Longitude [° E]</label><input id="site-${i}-lon" data-site="${i}" data-axis="lon" type="number" step="0.0001" value="${esc(p.lon)}" required><span class="field-error" id="error-site-${i}-lon" role="alert"></span></div>`
    + '</div></section>').join('');
  $('add-site').disabled = sites.length >= 3;
  setBusy(false);
  $('candidate-form').querySelectorAll('[data-remove]').forEach(b => { b.onclick = () => { sites.splice(+b.dataset.remove, 1); results = null; renderCandidates(); renderResults(); save(); }; });
  $('candidate-form').querySelectorAll('[data-site]').forEach(input => { input.oninput = () => { sites[+input.dataset.site][input.dataset.axis] = parseFloat(input.value); stale(); }; });
}

function renderResults(loading = false) {
  if (sites.length < 2) {
    $('comparison-matrix').innerHTML = '<div class="empty">Add at least two sites to compare: “+ Add site to comparison” on the analysis page, or Example sites.</div>';
    $('comparison-timelines').innerHTML = ''; $('comparison-window').textContent = '';
    return;
  }
  const rows = [
    ['Status', r => `<span class="status-badge ${r.status === 'acceptable' ? 'met' : 'unmet'}">${r.status === 'acceptable' ? '✓ Acceptable' : '× Not acceptable'}</span>`],
    ['Longest night', r => num(r.metrics.longest_night_h) + ' <small>h</small>'],
    ['Sun share', r => num(r.metrics.sun_pct) + ' <small>%</small>'],
    ['Longest comms gap', r => num(r.metrics.longest_comms_gap_h) + ' <small>h</small>'],
    ['Total time without Earth', r => num(r.metrics.comms_down_h) + ' <small>h</small>'],
    ['Earth visibility share', r => num(r.metrics.comms_pct) + ' <small>%</small>'],
    ['Ground height', r => num(r.site.height_m) + ' <small>m</small>'],
    ...['max_night_h', 'min_sun_pct', 'max_comms_gap_h'].map(k => [TITLES[k], r => {
      const q = r.criteria.find(x => x.key === k);
      return `<span class="${q.met ? 'met' : 'unmet'}">${q.margin >= 0 ? '+' : ''}${num(q.margin)} <small>${q.unit === 'pct' ? 'pp' : 'h'}</small></span>`;
    }]),
    ['Explanation', r => esc(r.explanation)],
  ];
  const head = sites.map((p, i) => `<th scope="col">Site ${letter(i)}<small>${Number(p.lat).toFixed(4)}°, ${Number(p.lon).toFixed(4)}°</small></th>`).join('');
  const cell = (r, fn) => !r ? '—' : r.data_status === 'ok' ? fn(r) : 'Insufficient terrain data';
  $('comparison-matrix').innerHTML = '<table><thead><tr><th scope="col">Quantity</th>' + head + '</tr></thead><tbody>'
    + rows.map(([label, fn]) => '<tr><th scope="row">' + label + '</th>' + sites.map((p, i) => {
      const value = cell(results && results[i], fn);
      return '<td>' + (loading ? `<span class="previous-value">${value}</span><span class="skeleton cell-skeleton"></span>` : value) + '</td>';
    }).join('') + '</tr>').join('') + '</tbody></table>';
  if (loading || !results) { if (!results) $('comparison-timelines').innerHTML = ''; return; }
  const w = results.find(r => r.window) || null;
  if (w) $('comparison-window').textContent = utc(Date.parse(w.window.start_utc)) + ' → ' + utc(Date.parse(w.window.end_utc)) + ' UTC';
  $('comparison-timelines').innerHTML = results.map((r, i) => `<div class="site-timeline"><h3>Site ${letter(i)}</h3>`
    + (r.data_status !== 'ok' ? '<p>Insufficient terrain data</p>' : ['sun', 'earth'].map(k => `<div class="track-row" data-body="${k}"><span>${k.toUpperCase()}</span><div class="track">`
      + r.intervals[k].map(v => `<i class="${v.visible ? '' : 'off'}" style="flex:${Date.parse(v.end_utc) - Date.parse(v.start_utc)}" title="${v.visible ? 'Visible' : 'Blocked'} · ${utc(Date.parse(v.start_utc))} — ${utc(Date.parse(v.end_utc))} UTC"></i>`).join('')
      + '</div></div>').join('')) + '</div>').join('');
}

async function compare() {
  if (sites.length < 2) { message('Select two or three sites.'); return; }
  const c = readForm(); if (!c) return;
  if (ctl) ctl.abort();
  ctl = new AbortController(); const mine = ctl;
  setBusy(true); message('Calculating comparison…'); renderResults(true);
  try {
    const r = await Api.compare({ sites: sites.map(p => ({ lat_deg: p.lat, lon_deg: p.lon })), start_utc: startUtc(c), profile: profileOf(c) }, mine.signal);
    if (mine.signal.aborted) return;
    results = r.results; $('comparison-matrix').dataset.stale = 'false'; message('');
    save();
  } catch (e) {
    if (e.name === 'AbortError' || mine.signal.aborted) return;
    // The server names the coordinate (lat_deg / lon_deg) but not which site it belongs to.
    if (e.status === 400 && (e.field === 'lat_deg' || e.field === 'lon_deg')) message('Check the site coordinates: ' + (e.field === 'lat_deg' ? 'latitude ' : 'longitude ') + e.message + '.');
    else if (e.status === 400 && FIELD_IDS[e.field] && fieldError(FIELD_IDS[e.field], e.message)) message('Check the highlighted field.');
    else message(e.status === 503 ? 'Server data are not loaded: ' + e.message : e.message);
  } finally {
    if (ctl === mine) { ctl = null; setBusy(false); }
  }
  renderResults();
}

async function loadMeta() {
  try {
    const m = await Api.meta();
    $('data-state').textContent = m.data.present ? 'LOLA 80 m · DE440 · LIVE' : 'DATA NOT LOADED';
    $('data-state').dataset.kind = m.data.present ? 'ok' : 'error';
    $('example-note').textContent = m.example_note;
  } catch (e) {
    $('data-state').textContent = 'SERVER UNREACHABLE'; $('data-state').dataset.kind = 'error';
  }
}

// ---------- start ----------
const saved = Store.load();
if (saved.profile) for (const id of PROFILE_IDS) if (saved.profile[id] != null && saved.profile[id] !== '') $(id).value = saved.profile[id];
sites = saved.sites.length ? saved.sites.map(p => ({ lat: p.lat, lon: p.lon })) : [];
$('compare-profile').onsubmit = e => { e.preventDefault(); compare(); };
$('candidate-form').onsubmit = e => { e.preventDefault(); compare(); };
for (const id of PROFILE_IDS) $(id).addEventListener('input', stale);
$('add-site').onclick = () => { if (sites.length >= 3) return; sites.push({ ...EXAMPLES[sites.length] }); results = null; renderCandidates(); renderResults(); save(); };
$('load-examples').onclick = () => { sites = EXAMPLES.slice(0, 2).map(p => ({ ...p })); results = null; renderCandidates(); renderResults(); save(); };
$('about').onclick = () => $('sources').showModal();
$('close-sources').onclick = () => $('sources').close();
renderCandidates(); renderResults(); loadMeta();
if (sites.length >= 2) compare();
