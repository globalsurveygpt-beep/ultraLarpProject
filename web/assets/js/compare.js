/* LUCY comparison page: one mission window and profile, two or three sites, one POST /api/compare.
   Each site is a card with its own map (and, on request, a 3D view). A click on a map chooses that
   card's site; any change recalculates the comparison by itself. Every number comes from the API.
   The mission profile is shared with the site page (LucyStore); sites are not: the comparison keeps its own sites. */
'use strict';

const $ = id => document.getElementById(id);
const Api = window.LucyApi, Store = window.LucyStore;
const PROFILE_IDS = ['start', 'hour', 'days', 'night', 'gap', 'sun', 'height'];
// API request keys -> form ids, for showing the server's error at the right field.
const FIELD_IDS = { start_utc: 'start', duration_days: 'days', max_night_h: 'night', max_comms_gap_h: 'gap', min_sun_pct: 'sun', observer_height_m: 'height' };
const TITLES = { max_night_h: 'Night margin', min_sun_pct: 'Sun share margin', max_comms_gap_h: 'Comms gap margin' };
const CARD_NUMBERS = [['min_sun_pct', 'Sun'], ['max_night_h', 'Longest night'], ['max_comms_gap_h', 'No Earth, longest']];
const COMPARE_DELAY_MS = 250;

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = v => v == null ? '—' : Number.isInteger(v) ? String(v) : String(+Number(v).toFixed(2));
const pad = n => String(n).padStart(2, '0');
const utc = ms => new Date(ms).toISOString().slice(0, 16).replace('T', ' ');
const letter = i => String.fromCharCode(65 + i);
const isPlaced = s => Number.isFinite(s.lat) && Number.isFinite(s.lon);

// Three fixed slots A, B, C. A slot shows a plus until its site is added; sites are { slot, lat, lon, result }
// and lat/lon stay null while a card waits for its first click.
const slots = [null, null, null];
let cards = [], results = null, ctl = null, timer = null, surface = 'map';
const sitesNow = () => slots.filter(Boolean);

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
  return true;
}
/* Only checks that fields hold numbers; ranges and rules are the server's. The comparison runs while
   the visitor types, so a problem is shown at the field but never steals the focus. */
function readForm() {
  clearErrors();
  const c = config();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(c.start)) { fieldError('start', 'Choose a date.'); return null; }
  if (!Number.isInteger(c.hour) || c.hour < 0 || c.hour > 23) { fieldError('hour', 'Enter a whole hour from 0 to 23.'); return null; }
  for (const id of ['days', 'night', 'gap', 'sun', 'height']) if (!Number.isFinite(c[id])) { fieldError(id, 'Enter a number.'); return null; }
  return c;
}

// The comparison keeps its own sites under its own key, so that leaving the page and coming back does not
// lose them, while the site analysis page and its point stay separate. Storage can be unavailable.
const SITES_KEY = 'lucy-compare-sites-v1';
function loadSites() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(SITES_KEY) || 'null');
    if (!Array.isArray(raw)) return [];
    return raw.slice(0, 3).map((p, i) => p && typeof p === 'object'
      ? { slot: i, lat: Number.isFinite(p.lat) ? p.lat : null, lon: Number.isFinite(p.lon) ? p.lon : null } : null);
  } catch (e) { return []; }
}
function save() {
  const c = config();
  Store.save({ profile: { start: c.start, hour: c.hour, days: c.days, night: c.night, gap: c.gap, sun: c.sun, height: c.height } });
  try { window.localStorage.setItem(SITES_KEY, JSON.stringify(slots.map(site => site ? { lat: isPlaced(site) ? site.lat : null, lon: isPlaced(site) ? site.lon : null } : null))); } catch (e) { /* nothing to keep */ }
}

// ---------- site cards ----------
function cardHtml(i) {
  return `<div class="site-card-head"><h2><span class="site-letter">${letter(i)}</span> Site ${letter(i)}</h2>`
    + '<span class="site-verdict" data-k="verdict" hidden></span><button type="button" data-k="remove">Remove</button></div>'
    + '<div class="site-card-stage map-stage">' + window.MapView.markup(letter(i)) + '<div class="site3d" data-k="s3d" hidden></div></div>'
    + '<div class="site-card-foot"><p class="site-coords" data-k="coords"></p><div class="answer-metrics" data-k="numbers"></div></div>';
}

function createCard(site) {
  const el = document.createElement('section');
  el.className = 'panel site-card';
  el.innerHTML = cardHtml(site.slot);
  const holder = document.querySelector(`.site-slot[data-slot="${site.slot}"]`);
  holder.querySelector('.site-add').hidden = true;
  holder.appendChild(el);
  const card = { el, site, map: window.MapView.create(el), view: null };
  card.map.init({ onPlace: (lat, lon) => choose(card, lat, lon), toast: notify });
  card.map.setLabels(true);
  el.querySelector('[data-k="remove"]').onclick = () => removeSite(card);
  if (isPlaced(site)) card.map.setMarker(site.lat, site.lon);
  cards.push(card);
  paintCard(card);
  return card;
}

function paintCard(card) {
  const { el, site } = card, r = site.result;
  const verdict = el.querySelector('[data-k="verdict"]'), numbers = el.querySelector('[data-k="numbers"]');
  el.querySelector('[data-k="coords"]').textContent = isPlaced(site)
    ? Number(site.lat).toFixed(4) + '°, ' + Number(site.lon).toFixed(4) + '° E' : 'Click the map to choose this site.';
  el.dataset.stale = String(!!r && !!el.dataset.pending);
  verdict.hidden = !isPlaced(site);
  if (!isPlaced(site)) { numbers.innerHTML = ''; return; }
  if (!r) { verdict.textContent = sitesNow().filter(isPlaced).length < 2 ? 'Choose another site' : 'Checking…'; verdict.dataset.kind = 'busy'; numbers.innerHTML = ''; return; }
  if (r.data_status !== 'ok') { verdict.textContent = 'No terrain data'; verdict.dataset.kind = 'none'; numbers.innerHTML = ''; return; }
  const good = r.status === 'acceptable';
  verdict.textContent = good ? '✓ Acceptable' : '× Not acceptable'; verdict.dataset.kind = good ? 'ok' : 'bad';
  numbers.innerHTML = CARD_NUMBERS.map(([key, label]) => {
    const x = r.criteria.find(k => k.key === key); if (!x) return '';
    return `<div class="${x.met ? 'is-met' : 'is-unmet'}"><small>${label}</small><b>${num(x.value)}${x.unit === 'pct' ? '%' : ' h'}</b><span>limit ${x.kind === 'max' ? '≤' : '≥'} ${num(x.limit)}${x.unit === 'pct' ? '%' : ' h'}</span></div>`;
  }).join('');
}

// One plus per free slot, all visible at once.
function buildSlots() {
  $('site-cards').innerHTML = slots.map((_, i) => `<div class="site-slot" data-slot="${i}"><button type="button" class="site-add" data-slot="${i}"><i aria-hidden="true">+</i><span>Add site ${letter(i)}</span></button></div>`).join('');
  $('site-cards').querySelectorAll('.site-add').forEach(b => { b.onclick = () => addSite(+b.dataset.slot); });
}

function addSite(slot, lat = null, lon = null) {
  if (slots[slot]) return;
  const site = { slot, lat: Number.isFinite(lat) ? lat : null, lon: Number.isFinite(lon) ? lon : null, result: null };
  slots[slot] = site;
  createCard(site);
  if (surface === '3d') setSurface('3d');
  save(); schedule(true);
}
function removeSite(card) {
  slots[card.site.slot] = null; cards.splice(cards.indexOf(card), 1);
  if (card.view) card.view.renderer.dispose();
  const holder = card.el.parentElement;
  card.el.remove();
  holder.querySelector('.site-add').hidden = false;
  cards.forEach(paintCard);
  results = null; save(); schedule(true);
}

// A click on a card's map chooses that card's site.
function choose(card, lat, lon) {
  if (window.MapView.arcKm(lat) > window.MapView.ZONE_KM) { notify('Outside the 154 km working zone. Click inside the bright circle.'); return; }
  card.site.lat = lat; card.site.lon = lon; card.site.result = null;
  card.map.setMarker(lat, lon);
  cards.forEach(paintCard);
  save(); schedule(true);
}

// ---------- shared view: map or 3D ----------
function setSurface(mode) {
  if (mode === '3d' && !window.Site3D) { notify('The 3D view needs WebGL, which is not available here.'); return; }
  surface = mode;
  $('cmp-map').setAttribute('aria-pressed', String(mode === 'map')); $('cmp-3d').setAttribute('aria-pressed', String(mode === '3d'));
  for (const card of cards) {
    card.el.querySelector('[data-k="s3d"]').hidden = mode !== '3d';
    if (mode !== '3d') card.map.redraw();
  }
  if (mode === '3d') refresh3d();
}
function refresh3d() {
  if (surface !== '3d' || !window.Site3D) return;
  const c = config();
  for (const card of cards) {
    if (!isPlaced(card.site)) continue;
    const box = card.el.querySelector('[data-k="s3d"]');
    if (!card.view) card.view = window.Site3D.create(box);
    card.view.show({ lat: card.site.lat, lon: card.site.lon, height_m: Number.isFinite(c.height) ? c.height : 0,
      ground_m: card.site.result && card.site.result.site ? card.site.result.site.height_m : null, time_utc: /^\d{4}-\d{2}-\d{2}$/.test(c.start) && Number.isInteger(c.hour) ? startUtc(c) : null });
    card.view.resize();
  }
}

// ---------- comparison ----------
function schedule(immediately) {
  clearTimeout(timer);
  timer = setTimeout(compare, immediately ? 60 : COMPARE_DELAY_MS);
}
function markPending() {
  if (ctl) { ctl.abort(); ctl = null; }
  if (results) $('comparison-matrix').dataset.stale = 'true';
  cards.forEach(card => { card.el.dataset.pending = '1'; paintCard(card); });
  save();
}

function renderResults(loading = false) {
  const placed = sitesNow().filter(isPlaced);
  if (placed.length < 2) {
    $('comparison-matrix').innerHTML = '<div class="empty">Choose a point on at least two maps to compare the sites.</div>';
    $('comparison-timelines').innerHTML = ''; $('comparison-window').textContent = '';
    $('comparison-timelines').style.setProperty('--site-count', 2);
    return;
  }
  $('comparison-timelines').style.setProperty('--site-count', Math.max(2, placed.length));
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
  const head = placed.map(p => `<th scope="col">Site ${letter(p.slot)}<small>${Number(p.lat).toFixed(4)}°, ${Number(p.lon).toFixed(4)}°</small></th>`).join('');
  const cell = (r, fn) => !r ? '—' : r.data_status === 'ok' ? fn(r) : 'Insufficient terrain data';
  $('comparison-matrix').innerHTML = '<table><thead><tr><th scope="col">Quantity</th>' + head + '</tr></thead><tbody>'
    + rows.map(([label, fn]) => '<tr><th scope="row">' + label + '</th>' + placed.map(p => {
      const value = cell(p.result, fn);
      return '<td>' + (loading ? `<span class="previous-value">${value}</span><span class="skeleton cell-skeleton"></span>` : value) + '</td>';
    }).join('') + '</tr>').join('') + '</tbody></table>';
  const done = placed.filter(p => p.result);
  if (loading || done.length < placed.length) { if (!done.length) $('comparison-timelines').innerHTML = ''; return; }
  const w = done.map(p => p.result).find(r => r.window) || null;
  if (w) $('comparison-window').textContent = utc(Date.parse(w.window.start_utc)) + ' → ' + utc(Date.parse(w.window.end_utc)) + ' UTC';
  $('comparison-timelines').innerHTML = placed.map(p => { const r = p.result;
    return `<div class="site-timeline"><h3>Site ${letter(p.slot)}</h3>`
      + (r.data_status !== 'ok' ? '<p>Insufficient terrain data</p>' : ['sun', 'earth'].map(k => `<div class="track-row" data-body="${k}"><span>${k.toUpperCase()}</span><div class="track">`
        + r.intervals[k].map(v => `<i class="${v.visible ? '' : 'off'}" style="flex:${Date.parse(v.end_utc) - Date.parse(v.start_utc)}" title="${v.visible ? 'Visible' : 'Blocked'} · ${utc(Date.parse(v.start_utc))} — ${utc(Date.parse(v.end_utc))} UTC"></i>`).join('')
        + '</div></div>').join('')) + '</div>';
  }).join('');
}

async function compare() {
  const placed = sitesNow().filter(isPlaced);
  if (ctl) { ctl.abort(); ctl = null; }
  if (placed.length < 2) {
    results = null; placed.forEach(p => { p.result = null; });
    cards.forEach(card => { delete card.el.dataset.pending; paintCard(card); });
    $('comparison-matrix').dataset.stale = 'false';
    message(placed.length ? 'Choose a point on another map to start the comparison.' : sitesNow().length ? 'Click the map to choose the site.' : 'Press + under Site A, B or C to add a site.');
    renderResults();
    return;
  }
  const c = readForm();
  if (!c) { message('Check the highlighted field in the mission settings.'); return; }
  const mine = ctl = new AbortController();
  message('Calculating comparison…'); renderResults(true);
  placed.forEach(p => { p.result = null; }); cards.forEach(paintCard);
  try {
    const r = await Api.compare({ sites: placed.map(p => ({ lat_deg: p.lat, lon_deg: p.lon })), start_utc: startUtc(c), profile: profileOf(c) }, mine.signal);
    if (mine.signal.aborted) return;
    placed.forEach((p, k) => { p.result = r.results[k]; });
    results = r.results; $('comparison-matrix').dataset.stale = 'false'; message('');
  } catch (e) {
    if (e.name === 'AbortError' || mine.signal.aborted) return;
    // The server names the coordinate (lat_deg / lon_deg) but not which site it belongs to.
    if (e.status === 400 && (e.field === 'lat_deg' || e.field === 'lon_deg')) message('Check the site coordinates: ' + (e.field === 'lat_deg' ? 'latitude ' : 'longitude ') + e.message + '.');
    else if (e.status === 400 && FIELD_IDS[e.field] && fieldError(FIELD_IDS[e.field], e.message)) message('Check the highlighted field in the mission settings.');
    else message(e.status === 503 ? 'The server is busy with other calculations. Change a point or a setting to try again.' : e.message);
  } finally {
    if (ctl === mine) ctl = null;
  }
  cards.forEach(card => { delete card.el.dataset.pending; paintCard(card); });
  renderResults(); refresh3d(); save();
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
$('compare-profile').onsubmit = e => { e.preventDefault(); schedule(true); };
for (const id of PROFILE_IDS) $(id).addEventListener('input', () => { markPending(); schedule(false); });
$('cmp-map').onclick = () => setSurface('map');
$('cmp-3d').onclick = () => setSurface('3d');
$('about').onclick = () => $('sources').showModal();
$('close-sources').onclick = () => $('sources').close();
// The 3D view is an ES module that loads after this script: show its switch when it reports ready.
function enable3d() { if (window.Site3D) $('cmp-3d').hidden = false; }
document.addEventListener('lucy:site3d-ready', enable3d);
enable3d();

buildSlots();
loadSites().forEach((p, i) => { if (p) addSite(i, p.lat, p.lon); });
renderResults(); loadMeta(); schedule(true);
