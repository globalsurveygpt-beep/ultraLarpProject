/* LUCY interface: site analysis, comparison and inverse search on the LUCY API.
   Every number on screen comes from the API; this file only formats and draws. */
'use strict';

const $ = id => document.getElementById(id);
const Api = window.LucyApi, Store = window.LucyStore;
const MINUTE = 60000, DAY = 86400000;

// API request keys -> form element ids, for highlighting the field the server rejected.
const FIELD_IDS = {
  lat_deg: 'lat', lon_deg: 'lon', lat: 'lat', lon: 'lon', start_utc: 'start', time: 'start', date: 'chart-day',
  duration_days: 'duration', vehicle_type: 'vehicle', max_night_h: 'night', max_comms_gap_h: 'gap',
  min_sun_pct: 'sun', observer_height_m: 'height', height: 'height',
  from_utc: 'search-from', to_utc: 'search-until', region: 'search-region', radius_km: 'search-radius',
};
const TITLES = { max_night_h: 'Longest night', min_sun_pct: 'Sun share', max_comms_gap_h: 'Longest comms gap' };

const state = {
  meta: null, result: null, dirty: false, pinned: [], view: 'site', surface: 'map', searchRows: [], sortKey: 'share', sortAsc: false,
  calls: {},            // name -> AbortController of the request in flight
};

// ---------- small helpers ----------
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = v => v == null ? '—' : Number.isInteger(v) ? String(v) : String(+Number(v).toFixed(2));
const deg = v => v == null ? '—' : Number(v).toFixed(2);
const pad = n => String(n).padStart(2, '0');
const iso = ms => new Date(ms).toISOString().replace('.000Z', 'Z');
const utc = ms => new Date(ms).toISOString().slice(0, 16).replace('T', ' ');
const dateOf = ms => new Date(ms).toISOString().slice(0, 10);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const unitOf = c => c.unit === 'pct' ? '%' : ' h';

function notify(text) {
  const n = $('notification'); n.textContent = text; n.hidden = false;
  clearTimeout(notify.t); notify.t = setTimeout(() => { n.hidden = true; }, 3500);
}

/* Start a request under a name; a newer request of the same name aborts the older one.
   Resolves to null when this request was superseded. */
async function call(name, fn) {
  if (state.calls[name]) state.calls[name].abort();
  const ctl = new AbortController(); state.calls[name] = ctl;
  try {
    const data = await fn(ctl.signal);
    return ctl.signal.aborted ? null : data;
  } catch (e) {
    if (e.name === 'AbortError' || ctl.signal.aborted) return null;
    throw e;
  } finally {
    if (state.calls[name] === ctl) delete state.calls[name];
  }
}

// ---------- form ----------
function config() {
  return {
    lat: parseFloat($('lat').value), lon: parseFloat($('lon').value), start: $('start').value,
    hour: parseInt($('hour').value, 10), days: parseFloat($('duration').value), night: parseFloat($('night').value),
    gap: parseFloat($('gap').value), sun: parseFloat($('sun').value), height: parseFloat($('height').value),
    vehicle: $('vehicle').value.trim(),
  };
}
const startUtc = c => c.start + 'T' + pad(c.hour) + ':00:00Z';
const profileOf = c => ({ duration_days: c.days, vehicle_type: c.vehicle, max_night_h: c.night, max_comms_gap_h: c.gap, min_sun_pct: c.sun, observer_height_m: c.height });

function clearErrors() {
  document.querySelectorAll('[aria-invalid=true]').forEach(n => { n.removeAttribute('aria-invalid'); n.removeAttribute('aria-describedby'); });
  document.querySelectorAll('.field-error').forEach(n => { n.textContent = ''; });
  $('form-error').textContent = '';
}
function fieldError(key, text) {
  const id = FIELD_IDS[key] || key, input = $(id), error = $('error-' + id);
  if (input) {
    input.setAttribute('aria-invalid', 'true');
    if (error) { error.textContent = text; input.setAttribute('aria-describedby', 'error-' + id); }
    if (input.offsetParent) input.focus({ preventScroll: false });
    return true;
  }
  return false;
}
/* Only checks that fields hold numbers; ranges and rules are the server's. */
function readForm() {
  clearErrors();
  const c = config();
  for (const [key, ok] of [['lat_deg', Number.isFinite(c.lat)], ['lon_deg', Number.isFinite(c.lon)], ['start_utc', /^\d{4}-\d{2}-\d{2}$/.test(c.start)],
    ['hour', Number.isInteger(c.hour) && c.hour >= 0 && c.hour <= 23], ['duration_days', Number.isFinite(c.days)], ['max_night_h', Number.isFinite(c.night)],
    ['max_comms_gap_h', Number.isFinite(c.gap)], ['min_sun_pct', Number.isFinite(c.sun)], ['observer_height_m', Number.isFinite(c.height)]]) {
    if (!ok) { fieldError(key, key === 'hour' ? 'Enter a whole hour from 0 to 23.' : key === 'start_utc' ? 'Choose a date.' : 'Enter a number.'); return null; }
  }
  return c;
}
/* Shows an API error where it belongs. `where` is the inline message box of the screen. */
function showError(e, where) {
  if (e.status === 400 && e.field && fieldError(e.field, e.message)) { message(where, 'Check the highlighted field.', 'input'); return; }
  const text = e.status === 503 ? 'Server data are not loaded: ' + e.message
    : e.status === 0 ? e.message : (e.field ? e.field + ': ' : '') + e.message;
  message(where, text, e.status === 400 ? 'input' : 'service');
}
function message(id, text, kind = 'notice') { const n = $(id); n.textContent = text || ''; n.dataset.kind = kind; n.hidden = !text; }

function updateEnd() {
  const c = config();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(c.start) || !Number.isFinite(c.days) || !Number.isInteger(c.hour)) { $('interval-end').textContent = ''; return; }
  const end = Date.parse(startUtc(c)) + Math.round(c.days * 1440) * MINUTE;
  $('interval-end').textContent = Number.isFinite(end) ? 'End (exclusive): ' + utc(end) + ' UTC' : '';
}
function markStale() {
  if (!state.result) return;
  state.dirty = true;
  $('site-view').dataset.stale = 'true';
  $('pin').disabled = true; $('export').disabled = true;
}

// ---------- site analysis ----------
function setBusy(busy, fresh) {
  $('assessment-results').setAttribute('aria-busy', String(busy));
  $('window-skeleton').hidden = !busy;
  $('marker').dataset.pulse = String(busy);
  $('run-button').disabled = busy;
  $('run-button').innerHTML = busy ? '<span class="spinner" aria-hidden="true"></span>Calculating…' : 'Run assessment →';
}

async function runWindow() {
  const c = readForm(); if (!c) return false;
  setBusy(true);
  message('window-message', 'Calculating window…');
  const slow = setTimeout(() => message('window-message', 'Building the terrain horizon for this site… The first calculation at a new site takes a few seconds.'), 1400);
  try {
    const r = await call('window', s => Api.window({ lat_deg: c.lat, lon_deg: c.lon, start_utc: startUtc(c), profile: profileOf(c) }, s));
    if (!r) return false;
    paintWindow(r, c);
    return true;
  } catch (e) {
    showError(e, 'window-message');
    return false;
  } finally {
    clearTimeout(slow);
    if (!state.calls.window) setBusy(false);
  }
}

function paintWindow(r, c) {
  state.result = { r, c }; state.dirty = false;
  $('site-view').dataset.stale = 'false';
  const ok = r.data_status === 'ok';
  $('pin').disabled = !ok; $('export').disabled = !ok;
  MapView.setMarker(c.lat, c.lon);
  const start = Date.parse(r.window.start_utc), end = Date.parse(r.window.end_utc);
  $('window-label').textContent = utc(start) + ' → ' + utc(end) + ' UTC · ' + num(c.days) + ' d';
  $('run-state').textContent = ok ? 'CALCULATED · 1-MIN STEPS' : 'NO TERRAIN DATA';
  message('window-message', '');
  const clear = ['criteria', 'secondary-metrics', 'report', 'timeline', 'interval-table', 'instant-table', 'sun-chart', 'earth-chart', 'mini-meta'];
  if (!ok) {
    message('window-message', 'Insufficient terrain data at this site. Choose a site within 154 km of the south pole.');
    $('verdict').textContent = 'No assessment available'; $('verdict').className = 'verdict';
    $('explanation').textContent = 'Terrain coverage is insufficient here; this is not a failed mission constraint.';
    clear.forEach(id => { $(id).innerHTML = ''; });
    $('instant-state').textContent = 'No terrain data'; $('epoch').textContent = '—'; $('instant-meta').textContent = '';
    $('cursor').disabled = true; $('report-context').textContent = '';
    return;
  }
  $('verdict').textContent = r.status === 'acceptable' ? 'Acceptable' : 'Not acceptable';
  $('verdict').className = 'verdict ' + (r.status === 'acceptable' ? 'is-acceptable' : 'is-unacceptable');
  $('explanation').textContent = r.explanation;
  $('criteria').innerHTML = r.criteria.map(x => {
    const body = x.key === 'max_comms_gap_h' ? 'earth' : 'sun';
    const width = Math.min(100, 100 * x.value / Math.max(x.limit, x.unit === 'pct' ? 100 : 1));
    return `<div data-body="${body}" class="criterion ${x.met ? '' : 'is-failed'}"><div class="criterion-name">${TITLES[x.key]}<b class="${x.met ? 'pass' : 'fail'}">${x.met ? '✓ MET' : '× NOT MET'}</b></div>`
      + `<div class="reading"><strong>${num(x.value)}</strong><small>${x.unit === 'pct' ? '%' : 'h'}</small><span>Limit ${x.kind === 'max' ? '≤' : '≥'} ${num(x.limit)}${unitOf(x)}</span></div>`
      + `<div class="meter"><i style="width:${width}%"></i></div><p>Margin ${x.margin >= 0 ? '+' : ''}${num(x.margin)} ${x.unit === 'pct' ? 'pp' : 'h'}</p></div>`;
  }).join('');
  const m = r.metrics;
  $('secondary-metrics').innerHTML = `<div>Total time without Earth <b>${num(m.comms_down_h)} h</b></div><div>Earth visibility share <b>${num(m.comms_pct)}%</b></div>`
    + `<div class="profile-label">Ground height <b>${num(r.site.height_m)} m</b> · Vehicle <b>${esc(c.vehicle || 'Not specified')}</b> · Instrument height <b>${num(c.height)} m</b></div>`;
  $('mini-meta').innerHTML = `<span>Terrain <b>${esc(r.meta.terrain)}</b></span><span>Ephemeris <b>${esc(r.meta.ephemeris)}</b></span>`
    + `<span>Horizon <b>${num(r.meta.horizon_radius_km)} km</b></span><span>Sampling <b>${num(r.meta.time_step_min)} min</b></span><span>Model <b>${esc(r.meta.model)}</b></span>`;
  $('report').innerHTML = [['Longest night', m.longest_night_h, 'h'], ['Sun share', m.sun_pct, '%'], ['Longest comms gap', m.longest_comms_gap_h, 'h'],
    ['Total time without Earth', m.comms_down_h, 'h'], ['Earth visibility share', m.comms_pct, '%']]
    .map(a => `<tr><td>${a[0]}</td><td>${num(a[1])}</td><td>${a[2]}</td></tr>`).join('');
  $('report-meta').textContent = r.status === 'acceptable' ? 'ACCEPTABLE' : 'NOT ACCEPTABLE';
  $('report-context').textContent = 'Site ' + c.lat.toFixed(4) + '°, ' + c.lon.toFixed(4) + '° · window ' + utc(start) + ' UTC · ' + num(c.days) + ' d';
  const len = end - start;
  $('timeline').innerHTML = ['sun', 'earth'].map(key => `<div class="track-row" data-body="${key}"><span>${key.toUpperCase()}</span><div class="track" aria-label="${key} visibility intervals">`
    + r.intervals[key].map(i => {
      const a = Date.parse(i.start_utc), b = Date.parse(i.end_utc), label = (i.visible ? 'Visible' : 'Blocked') + ' · ' + utc(a) + ' — ' + utc(b) + ' UTC';
      return `<button type="button" class="interval ${i.visible ? '' : 'off'}" style="flex:${b - a}" data-minute="${(a - start) / MINUTE}" aria-label="${key} ${label}" title="${label}"></button>`;
    }).join('') + '</div></div>').join('')
    + `<div class="ticks">${[0, .25, .5, .75, 1].map(f => '<span>' + dateOf(start + len * f).slice(5) + '</span>').join('')}</div>`;
  $('timeline').querySelectorAll('[data-minute]').forEach(b => { b.onclick = () => { $('cursor').value = b.dataset.minute; inspect(); }; });
  $('interval-table').innerHTML = '<table><thead><tr><th>Body / state</th><th>Start / UTC</th><th>End / UTC</th></tr></thead><tbody>'
    + ['sun', 'earth'].flatMap(k => r.intervals[k].map(i => `<tr><td>${k.toUpperCase()} · ${i.visible ? 'Visible' : 'Blocked'}</td><td>${utc(Date.parse(i.start_utc))}</td><td>${utc(Date.parse(i.end_utc))}</td></tr>`)).join('')
    + '</tbody></table>';
  $('cursor').max = Math.max(0, Math.round(len / MINUTE) - 1); $('cursor').value = 0; $('cursor').disabled = false;
  $('chart-day').value = dateOf(start);
  inspect(); renderCharts(); updateContext();
}

// Geometry at the cursor: /api/instant, a short pause after the slider stops.
function inspect() {
  if (!state.result || state.result.r.data_status !== 'ok') return;
  const { r, c } = state.result, ms = Date.parse(r.window.start_utc) + Number($('cursor').value) * MINUTE;
  if (window.Site3D && state.surface === '3d') window.Site3D.setTime(iso(ms));
  $('epoch').textContent = utc(ms); $('cursor-label').textContent = utc(ms) + ' UTC'; $('instant-meta').textContent = utc(ms) + ' UTC';
  clearTimeout(inspect.t);
  inspect.t = setTimeout(async () => {
    try {
      const g = await call('instant', s => Api.instant({ lat: c.lat, lon: c.lon, time: iso(ms), height: c.height }, s));
      if (!g) return;
      $('instant-message').hidden = true;
      if (g.data_status !== 'ok') { $('instant-state').textContent = 'No terrain data'; $('instant-table').innerHTML = ''; return; }
      const vis = b => g[b].visible ? 'VISIBLE' : 'BLOCKED';
      $('instant-state').textContent = 'Sun ' + vis('sun') + ' / Earth ' + vis('earth');
      $('instant-table').innerHTML = [['Altitude / °', 'alt_deg'], ['Azimuth / °', 'az_deg'], ['Terrain horizon / °', 'horizon_deg'], ['Clearance / °', 'margin_deg']]
        .map(([label, k]) => `<tr><td>${label}</td><td>${deg(g.sun[k])}</td><td>${deg(g.earth[k])}</td></tr>`).join('')
        + `<tr><td>Visibility</td><td>${g.sun.visible ? 'Visible' : 'Blocked'}</td><td>${g.earth.visible ? 'Visible' : 'Blocked'}</td></tr>`;
    } catch (e) { $('instant-message').hidden = false; $('instant-message').textContent = e.message; }
  }, 150);
}

// Daily charts: /api/day-series, 145 points every 10 minutes.
async function renderCharts() {
  if (!state.result || state.result.r.data_status !== 'ok') return;
  const { c } = state.result, day = $('chart-day').value;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
  const slow = setTimeout(() => { $('geometry-meta').textContent = 'Updating geometry…'; }, 300);
  try {
    const d = await call('day', s => Api.daySeries({ lat: c.lat, lon: c.lon, date: day, height: c.height }, s));
    if (!d) return;
    if (d.data_status !== 'ok') { $('sun-chart').innerHTML = $('earth-chart').innerHTML = '<p class="data-note">No terrain data.</p>'; return; }
    for (const key of ['sun', 'earth']) drawChart(key, d[key], day);
    explainTerrain(d);
  } catch (e) {
    $('geometry-meta').textContent = e.message;
    if (e.field) fieldError(e.field, e.message);
    return;
  } finally { clearTimeout(slow); }
  $('geometry-meta').textContent = '145 samples · 10-minute steps';
}
function drawChart(key, b, day) {
  const n = b.alt_deg.length, values = b.alt_deg.concat(b.horizon_deg);
  const lo = Math.floor(Math.min(0, ...values)), hi = Math.ceil(Math.max(1, ...values));
  const X = i => 42 + i / (n - 1) * 650, Y = v => 130 - (v - lo) / (hi - lo) * 105;
  const path = arr => arr.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(2) + ',' + Y(v).toFixed(2)).join(' ');
  const ticks = [lo, 0, hi].filter((v, i, a) => a.indexOf(v) === i);
  $(key + '-chart').innerHTML = `<svg class="chart" viewBox="0 0 720 165" role="img" aria-label="${key} altitude and terrain horizon in its direction, ${n} points on ${esc(day)}">`
    + ticks.map(v => `<path d="M42 ${Y(v)}H692" stroke="#383838" stroke-dasharray="2 4"/><text x="4" y="${Y(v) + 3}">${v}°</text>`).join('')
    + `<path d="${path(b.alt_deg)}" fill="none" stroke="var(--${key}-color)" stroke-width="1.6"/>`
    + `<path d="${path(b.horizon_deg)}" fill="none" stroke="#999999" stroke-dasharray="7 4" stroke-width="1.5"/>`
    + [0, 6, 12, 18, 24].map(h => `<text x="${X(h * 6) - 8}" y="154">${pad(h)}</text>`).join('') + '</svg>';
}
// A real example of the project's point: the Sun above the mathematical horizon but behind terrain.
function explainTerrain(d) {
  const s = d.sun, i = s.alt_deg.findIndex((a, k) => a > 0 && !s.visible[k]);
  const base = 'Clearance = body altitude − terrain horizon in the body\'s direction; negative means hidden.';
  $('terrain-example').textContent = i < 0 ? base
    : `On ${utc(Date.parse(d.times_utc[i]))} UTC here: Sun altitude ${deg(s.alt_deg[i])}° − terrain horizon ${deg(s.horizon_deg[i])}° = clearance ${deg(s.margin_deg[i])}°. `
      + 'The Sun is above the mathematical horizon, but blocked by terrain.';
}

function exportCsv() {
  if (!state.result || state.dirty || state.result.r.data_status !== 'ok') return;
  const { r, c } = state.result;
  const rows = [['model', r.meta.model], ['terrain', r.meta.terrain], ['ephemeris', r.meta.ephemeris], ['latitude_deg', c.lat], ['longitude_deg_e', c.lon],
    ['ground_height_m', r.site.height_m], ['start_utc', r.window.start_utc], ['end_utc', r.window.end_utc], ...Object.entries(r.profile),
    ['status', r.status], ['reason_code', r.reason_code], ['explanation', r.explanation], ...Object.entries(r.metrics),
    ['criterion', 'limit', 'value', 'margin', 'met'], ...r.criteria.map(x => [x.key, x.limit, x.value, x.margin, x.met])];
  const csv = rows.map(row => row.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })), a = document.createElement('a');
  a.href = url; a.download = `lucy-window-${c.lat.toFixed(4)}_${c.lon.toFixed(4)}_${r.window.start_utc.slice(0, 13).replace('T', '-')}h.csv`;
  a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- comparison sites (the comparison itself is on compare.html) ----------
function updatePinned() {
  $('pin-count').textContent = '[' + state.pinned.length + '/3]';
  MapView.setPins(state.pinned.map((p, i) => ({ ...p, name: String.fromCharCode(65 + i) })));
  Store.save({ sites: state.pinned });
}

// ---------- inverse search ----------
function updateContext() {
  const c = config();
  const text = c.start + ' ' + pad(c.hour || 0) + ':00 · ' + num(c.days) + ' d · Night ≤ ' + num(c.night) + ' h · Comms gap ≤ ' + num(c.gap) + ' h · Sun ≥ ' + num(c.sun) + '%';
  $('search-context').textContent = 'Profile from Site analysis: ' + text.split(' · ').slice(1).join(' · ');
}
const shareColor = p => {
  const t = Math.max(0, Math.min(1, p / 100));
  const mix = (a, b) => Math.round(a + (b - a) * Math.sqrt(t));
  return `rgb(${mix(58, 216)},${mix(52, 187)},${mix(40, 121)})`;   // dark to the Sun colour
};
function paintGrid(points) {
  const zone = 2 * 1737.4 * Math.tan(MapView.ZONE_KM / (2 * 1737.4));
  $('search-grid').innerHTML = `<image href="assets/img/basemap.jpg" x="-304" y="-304" width="608" height="608" opacity=".35" preserveAspectRatio="none"/>`
    + `<circle cx="0" cy="0" r="${zone.toFixed(2)}" fill="none" stroke="#bbb" stroke-dasharray="3 3" stroke-width=".5"/>`
    + points.map((p, i) => {
      const xy = MapView.latLonToXY(p.lat_deg, p.lon_deg), title = `${p.lat_deg.toFixed(4)}°, ${p.lon_deg.toFixed(4)}° · ${num(p.acceptable_pct)}% acceptable starts`;
      return p.acceptable_pct > 0
        ? `<circle cx="${xy.x.toFixed(2)}" cy="${(-xy.y).toFixed(2)}" r="2.4" fill="${shareColor(p.acceptable_pct)}" data-grid="${i}"><title>${title}</title></circle>`
        : `<circle cx="${xy.x.toFixed(2)}" cy="${(-xy.y).toFixed(2)}" r="1.1" fill="none" stroke="#6a6a6a" stroke-width=".35" data-grid="${i}"><title>${title}</title></circle>`;
    }).join('') + '<text x="2" y="-155" fill="#aaa" font-size="5">0° E ↑</text>';
  $('search-grid').querySelectorAll('[data-grid]').forEach(n => {
    n.onclick = () => { const p = points[+n.dataset.grid]; notify(`${p.lat_deg.toFixed(4)}°, ${p.lon_deg.toFixed(4)}° · ${num(p.acceptable_pct)}% acceptable starts`); };
  });
}
function tableSkeleton() {
  return '<table><thead><tr><th>Site</th><th>Best start / UTC</th><th>Acceptable starts</th><th>Longest night</th></tr></thead><tbody>'
    + Array.from({ length: 10 }, () => '<tr>' + '<td><span class="skeleton cell-skeleton"></span></td>'.repeat(4) + '</tr>').join('') + '</tbody></table>';
}
function renderSearch() {
  const rows = state.searchRows;
  if (!rows.length) { $('search-results').innerHTML = '<div class="empty"><h2>No acceptable windows</h2><p>No site in this region meets all three thresholds for any start time in the period. Relax the thresholds or widen the period.</p></div>'; return; }
  const value = (t, k) => ({ share: t.acceptable_pct, lat: t.window.site.lat_deg, lon: t.window.site.lon_deg, start: Date.parse(t.window.window.start_utc),
    night: t.window.metrics.longest_night_h, gap: t.window.metrics.longest_comms_gap_h, sun: t.window.metrics.sun_pct, rank: t.rank }[k]);
  const sorted = [...rows].sort((a, b) => (value(a, state.sortKey) - value(b, state.sortKey)) * (state.sortAsc ? 1 : -1) || a.rank - b.rank);
  const cols = [['lat', 'Latitude / °'], ['lon', 'Longitude / ° E'], ['start', 'Best start / UTC'], ['share', 'Acceptable starts / %'], ['night', 'Longest night / h'], ['gap', 'Comms gap / h'], ['sun', 'Sun / %']];
  $('search-results').innerHTML = '<table><thead><tr>' + cols.map(([k, l]) => `<th><button type="button" data-sort="${k}" aria-label="Sort by ${l}" ${state.sortKey === k ? 'aria-pressed="true"' : ''}>${l}${state.sortKey === k ? (state.sortAsc ? ' ↑' : ' ↓') : ''}</button></th>`).join('')
    + '<th>Assessment</th></tr></thead><tbody>' + sorted.map(t => {
      const w = t.window;
      return `<tr><td>${w.site.lat_deg.toFixed(4)}</td><td>${w.site.lon_deg.toFixed(4)}</td><td>${utc(Date.parse(w.window.start_utc))}</td><td>${num(t.acceptable_pct)}</td>`
        + `<td>${num(w.metrics.longest_night_h)}</td><td>${num(w.metrics.longest_comms_gap_h)}</td><td>${num(w.metrics.sun_pct)}</td><td><button type="button" data-inspect="${t.rank}">Inspect →</button></td></tr>`;
    }).join('') + '</tbody></table>';
  $('search-results').querySelectorAll('[data-sort]').forEach(b => { b.onclick = () => { state.sortAsc = state.sortKey === b.dataset.sort ? !state.sortAsc : true; state.sortKey = b.dataset.sort; renderSearch(); }; });
  $('search-results').querySelectorAll('[data-inspect]').forEach(b => { b.onclick = () => inspectTop(rows.find(t => t.rank === +b.dataset.inspect)); });
}
// The top window was computed by the same code as a single assessment: show it without recalculating.
function inspectTop(t) {
  const w = t.window, start = new Date(Date.parse(w.window.start_utc));
  $('lat').value = w.site.lat_deg.toFixed(6); $('lon').value = w.site.lon_deg.toFixed(6);
  $('start').value = dateOf(start); $('hour').value = start.getUTCHours();
  $('height').value = 0; updateEnd();
  paintWindow(w, config()); saveProfile();
  setView('site');
  if (!showSite3D()) $('results-section').scrollIntoView({ block: 'start' });
}
async function search() {
  clearErrors();
  const c = readForm(); if (!c) { setView('site'); notify('Fix the mission profile on Site analysis first.'); return; }
  const from = $('search-from').value, until = $('search-until').value;
  if (!from) { fieldError('from_utc', 'Choose a date.'); return; }
  if (!until) { fieldError('to_utc', 'Choose a date.'); return; }
  let region = { kind: 'zone' };
  if ($('search-region').value === 'circle') {
    const radius = parseFloat($('search-radius').value);
    if (!Number.isFinite(radius)) { fieldError('radius_km', 'Enter a radius in km.'); return; }
    region = { kind: 'circle', lat_deg: c.lat, lon_deg: c.lon, radius_km: radius };
  }
  $('search-button').disabled = true; $('search-button').innerHTML = '<span class="spinner" aria-hidden="true"></span>Searching…';
  $('search-overlay').hidden = false;
  $('search-overlay').innerHTML = '<strong>Searching the 5 km grid…</strong><small>usually 2–5 s</small>';
  $('search-results').innerHTML = tableSkeleton(); message('search-message', '');
  try {
    const r = await call('search', s => Api.search({ from_utc: from + 'T00:00:00Z', to_utc: until + 'T00:00:00Z', region, profile: { ...profileOf(c), observer_height_m: 0 } }, s));
    if (!r) return;
    const g = r.grid;
    $('search-count').textContent = `${g.sites_checked.toLocaleString('en-US')} of ${g.sites_total.toLocaleString('en-US')} sites checked`
      + (g.sites_insufficient_data ? ` · ${g.sites_insufficient_data} without terrain` : '') + (g.sites_not_precomputed ? ` · ${g.sites_not_precomputed} not precomputed` : '');
    $('search-note').textContent = r.note;
    paintGrid(r.map);
    state.searchRows = r.top; state.sortKey = 'share'; state.sortAsc = false;
    if (!r.map.length) message('search-message', 'No site with terrain data in this region.');
    renderSearch();
  } catch (e) {
    if (e.status === 400 && e.field && FIELD_IDS[e.field] && $(FIELD_IDS[e.field]).closest('#site-view')) { setView('site'); fieldError(e.field, e.message); }
    else showError(e, 'search-message');
    $('search-results').innerHTML = '';
  } finally {
    if (!state.calls.search) {
      $('search-overlay').hidden = true; $('search-button').disabled = false; $('search-button').textContent = 'Search';
    }
  }
}

// ---------- views ----------
const VIEWS = { site: 'Landing window assessment', search: 'Find sites & start dates' };
function setView(v) {
  state.view = v;
  for (const key of Object.keys(VIEWS)) { $(key + '-view').hidden = key !== v; $('tab-' + key).setAttribute('aria-selected', String(key === v)); }
  $('page-title').textContent = VIEWS[v];
  updateContext();
  if (v === 'site') MapView.redraw();
}

/* Surface panel: the 2D map, or the 3D view of the selected site. The 3D view is an optional
   module (assets/js/site3d.js) that sets window.Site3D; without it the switch stays hidden. */
function setSurface(mode) {
  const three = mode === '3d' && !!window.Site3D;
  state.surface = three ? '3d' : 'map';
  $('site3d').hidden = !three;
  $('view-map').setAttribute('aria-pressed', String(!three));
  $('view-3d').setAttribute('aria-pressed', String(three));
  if (three) window.Site3D.resize(); else MapView.redraw();
}
function showSite3D() {
  if (!window.Site3D || !state.result || state.result.r.data_status !== 'ok') return false;
  const { r, c } = state.result;
  window.Site3D.show({ lat: c.lat, lon: c.lon, height_m: c.height, ground_m: r.site.height_m, time_utc: currentTime() });
  setSurface('3d');
  return true;
}
function currentTime() {
  if (!state.result) return null;
  return iso(Date.parse(state.result.r.window.start_utc) + Number($('cursor').value) * MINUTE);
}
function saveProfile() {
  const c = config();
  Store.save({ profile: { start: c.start, hour: c.hour, days: c.days, night: c.night, gap: c.gap, sun: c.sun, height: c.height, vehicle: c.vehicle },
    site: Number.isFinite(c.lat) && Number.isFinite(c.lon) ? { lat: c.lat, lon: c.lon } : null });
}
function restore() {
  const saved = Store.load();
  const ids = { start: 'start', hour: 'hour', days: 'duration', night: 'night', gap: 'gap', sun: 'sun', height: 'height', vehicle: 'vehicle' };
  if (saved.profile) for (const [k, id] of Object.entries(ids)) if (saved.profile[k] != null && saved.profile[k] !== '') $(id).value = saved.profile[k];
  if (saved.site) { $('lat').value = saved.site.lat; $('lon').value = saved.site.lon; }
  state.pinned = saved.sites.map(p => ({ lat: p.lat, lon: p.lon }));
}

// ---------- start ----------
async function loadMeta() {
  try {
    const m = await Api.meta();
    state.meta = m;
    $('data-state').textContent = m.data.present ? 'LOLA 80 m · DE440 · LIVE' : 'DATA NOT LOADED';
    $('data-state').dataset.kind = m.data.present ? 'ok' : 'error';
    $('example-note').textContent = m.example_note;
    const ids = { duration_days: 'duration', max_night_h: 'night', max_comms_gap_h: 'gap', min_sun_pct: 'sun', observer_height_m: 'height' };
    for (const f of m.profile_fields) {
      const input = $(ids[f.key]); if (!input) continue;
      if (typeof f.min === 'number') input.min = f.min;
      if (typeof f.max === 'number') input.max = f.max;
    }
    $('zone-hint').textContent = `Supported zone: within ${m.zone.max_distance_from_pole_km} km of the pole, approximately ${Math.abs(m.zone.min_lat_deg)}–90° S. Click the map to pick a site.`;
    if (!m.data.present) message('window-message', 'Server data are not loaded: ' + m.data.missing.join(', ') + '. Run python scripts/fetch_data.py on the server.', 'service');
  } catch (e) {
    $('data-state').textContent = 'SERVER UNREACHABLE'; $('data-state').dataset.kind = 'error';
    message('window-message', e.message, 'service');
  }
}

MapView.init({
  onPlace(lat, lon) {
    $('lat').value = lat.toFixed(4); $('lon').value = lon.toFixed(4);
    MapView.setMarker(lat, lon);
    clearErrors(); markStale(); updateContext(); saveProfile();
    const km = MapView.arcKm(lat);
    if (km > MapView.ZONE_KM) notify(`Outside the working zone (${km.toFixed(0)} km from the pole): the result will be "insufficient terrain data".`);
  },
  toast: notify,
});
for (const id of ['lat', 'lon', 'start', 'hour', 'duration', 'vehicle', 'height', 'night', 'gap', 'sun']) {
  $(id).addEventListener('input', () => {
    markStale(); updateEnd(); updateContext(); saveProfile();
    if (id === 'lat' || id === 'lon') { const c = config(); if (Number.isFinite(c.lat) && Number.isFinite(c.lon)) MapView.setMarker(c.lat, c.lon); }
  });
}
// After a calculation: the 3D site view when it is available, otherwise the results.
$('mission-form').onsubmit = async e => { e.preventDefault(); saveProfile(); if (await runWindow() && !showSite3D()) $('results-section').scrollIntoView({ block: 'start' }); };
$('view-results').onclick = () => $('results-section').scrollIntoView({ block: 'start' });
$('view-map').onclick = () => setSurface('map');
$('view-3d').onclick = () => { if (!showSite3D()) notify('Run the assessment first: the site view shows the calculated site.'); };
$('cursor').oninput = inspect;
$('chart-day').addEventListener('change', renderCharts);
$('grid-toggle').onchange = e => MapView.setGrid(e.target.checked);
$('labels-toggle').onchange = e => MapView.setLabels(e.target.checked);
document.querySelectorAll('[data-view]').forEach(b => { b.onclick = () => setView(b.dataset.view); });
$('pin').onclick = () => {
  if (!state.result || state.dirty || state.result.r.data_status !== 'ok') return;
  const p = { lat: state.result.c.lat, lon: state.result.c.lon };
  if (state.pinned.some(s => s.lat === p.lat && s.lon === p.lon)) { notify('This site is already in the comparison.'); return; }
  if (state.pinned.length === 3) { notify('At most 3 sites. Remove one first.'); return; }
  state.pinned.push(p); updatePinned(); notify('Site added to comparison.');
};
$('export').onclick = exportCsv;
$('search-form').onsubmit = e => { e.preventDefault(); search(); };
$('search-region').onchange = () => { $('radius-field').hidden = $('search-region').value !== 'circle'; };
$('about').onclick = () => $('sources').showModal();
$('close-sources').onclick = () => $('sources').close();

restore();
updateEnd(); updateContext(); updatePinned();
// The 3D site view is an ES module that loads after this script: enable it when it reports ready.
function enableSite3D() { if (!window.Site3D || !$('view-switch').hidden) return; window.Site3D.mount($('site3d')); $('view-switch').hidden = false; }
document.addEventListener('lucy:site3d-ready', enableSite3D);
enableSite3D();
MapView.setMarker(config().lat, config().lon);
$('search-results').innerHTML = '<div class="empty"><h2>Search the supported region</h2><p>Choose the start-date period and a region, then press Search.</p></div>';
loadMeta();
if (location.hash === '#search') setView('search');
