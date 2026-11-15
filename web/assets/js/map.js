/* Site map: pan and zoom over the LOLA shaded relief, box zoom, context menu, keyboard.
   The image is a crop of the terrain raster in its own south polar stereographic projection
   (see scripts/render_basemap.py), so a pixel converts to latitude and longitude with the same
   closed-form formulas as core/terrain.py: no approximation. */
(function () {
  'use strict';

  const R_KM = 1737.4;          // sphere of the LOLA product
  const HALF_KM = 304;          // the image spans x, y in [-304, 304] km: the whole LOLA raster
  const ZONE_KM = 154;          // working zone, great-circle distance from the pole
  const LAT_RINGS = [-89, -88, -87, -86, -85, -84, -83, -82, -81];
  const MAXZ = 16, DRAG_PX = 6, PAN_STEP = 70, HISTORY = 30;
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- projection ----------
  function latLonToXY(lat, lon) {   // km on the stereographic plane, y towards 0 deg E
    const rho = 2 * R_KM * Math.tan((lat + 90) * Math.PI / 360), a = lon * Math.PI / 180;
    return { x: rho * Math.sin(a), y: rho * Math.cos(a) };
  }
  function xyToLatLon(x, y) {
    const rho = Math.hypot(x, y);
    return { lat: -90 + 360 / Math.PI * Math.atan(rho / (2 * R_KM)), lon: Math.atan2(x, y) * 180 / Math.PI };
  }
  const arcKm = lat => (lat + 90) * Math.PI / 180 * R_KM;   // distance from the pole
  const rhoOfArc = km => 2 * R_KM * Math.tan(km / (2 * R_KM));
  const uvOfXY = p => ({ u: (p.x + HALF_KM) / (2 * HALF_KM), v: (HALF_KM - p.y) / (2 * HALF_KM) });
  const xyOfUV = (u, v) => ({ x: u * 2 * HALF_KM - HALF_KM, y: HALF_KM - v * 2 * HALF_KM });
  const fmtLat = l => Math.abs(l).toFixed(4) + '° S';
  const fmtLon = l => Math.abs(l).toFixed(4) + '° ' + (l < 0 ? 'W' : 'E');

  // ---------- view state ----------
  const el = {};
  let opts = {}, V = { s: 1, cu: 0.5, cv: 0.5 }, hist = [], anim = null;
  const st = { marker: null, pins: [], hover: false, ptr: { x: 0, y: 0 }, space: false, drag: null, pan: null, menuUV: null, lastWheel: 0 };

  function size() { const r = el.map.getBoundingClientRect(); return { w: r.width, h: r.height }; }
  function base() { const z = size(); return Math.min(z.w, z.h); }   // zoom 1: the whole raster fits
  function clampV(v) {
    const z = size(), S = base() * v.s, hw = z.w / 2 / S, hh = z.h / 2 / S;
    v.cu = S <= z.w ? 0.5 : Math.min(1 - hw, Math.max(hw, v.cu));
    v.cv = S <= z.h ? 0.5 : Math.min(1 - hh, Math.max(hh, v.cv));
    return v;
  }
  function toUV(x, y) { const z = size(), S = base() * V.s; return { u: V.cu + (x - z.w / 2) / S, v: V.cv + (y - z.h / 2) / S }; }
  function toScreen(u, v) { const z = size(), S = base() * V.s; return { x: z.w / 2 + (u - V.cu) * S, y: z.h / 2 + (v - V.cv) * S }; }

  function cancelAnim() { if (anim) { cancelAnimationFrame(anim); anim = null; } }
  function animateTo(t, ms) {
    cancelAnim();
    t = clampV({ s: Math.min(MAXZ, Math.max(1, t.s)), cu: t.cu, cv: t.cv });
    if (reduced) { V = t; render(); return; }
    const a = { ...V }, t0 = performance.now(), dur = ms || 480;
    const step = now => {
      const p = Math.min(1, (now - t0) / dur), e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      V = { s: Math.exp(Math.log(a.s) + (Math.log(t.s) - Math.log(a.s)) * e), cu: a.cu + (t.cu - a.cu) * e, cv: a.cv + (t.cv - a.cv) * e };
      render();
      anim = p < 1 ? requestAnimationFrame(step) : null;
    };
    anim = requestAnimationFrame(step);
  }
  function pushHist() { hist.push({ ...V }); if (hist.length > HISTORY) hist.shift(); }
  function zoomAt(x, y, factor, animate) {
    const z = size(), p = toUV(x, y), s2 = Math.min(MAXZ, Math.max(1, V.s * factor)), S2 = base() * s2;
    const t = { s: s2, cu: p.u - (x - z.w / 2) / S2, cv: p.v - (y - z.h / 2) / S2 };
    if (animate) animateTo(t, 260); else { cancelAnim(); V = clampV(t); render(); }
  }
  function zoomCenter(factor) { const z = size(); pushHist(); zoomAt(z.w / 2, z.h / 2, factor, true); }
  function zoomToRect(x0, y0, x1, y1) {
    const z = size(), c = toUV((x0 + x1) / 2, (y0 + y1) / 2);
    pushHist();
    animateTo({ s: V.s * Math.min(z.w / Math.abs(x1 - x0), z.h / Math.abs(y1 - y0)), cu: c.u, cv: c.v });
  }
  function resetView() { pushHist(); animateTo({ s: 1, cu: 0.5, cv: 0.5 }); }
  // The 154 km working zone, with a small margin, fills the map.
  function zoneView() { pushHist(); animateTo({ s: HALF_KM / (rhoOfArc(ZONE_KM) + 6), cu: 0.5, cv: 0.5 }); }
  function toggleHelp(show) {
    const on = show == null ? el.help.hidden : show;
    el.help.hidden = !on; el.helpBtn.setAttribute('aria-expanded', String(on));
  }
  function prevView() { if (hist.length) animateTo(hist.pop()); render(); }
  function panBy(dx, dy) { cancelAnim(); const S = base() * V.s; V.cu -= dx / S; V.cv -= dy / S; clampV(V); render(); }

  // ---------- drawing ----------
  function drawGrid() {
    const ring = (r, cls) => `<circle cx="0" cy="0" r="${r.toFixed(3)}" class="${cls}"/>`;
    let svg = '';
    for (const lat of LAT_RINGS) svg += ring(latLonToXY(lat, 0).y, 'lat');
    for (let lon = 0; lon < 360; lon += 30) {
      const a = lon * Math.PI / 180, r = HALF_KM * Math.SQRT2;
      svg += `<path class="lon" d="M0 0L${(r * Math.sin(a)).toFixed(2)} ${(-r * Math.cos(a)).toFixed(2)}"/>`;
    }
    svg += ring(rhoOfArc(ZONE_KM), 'zone');
    for (const lat of LAT_RINGS) {
      const y = latLonToXY(lat, 0).y;
      svg += `<text x="2.4" y="${(-y - 2.4).toFixed(2)}">${-lat}° S</text>`;
    }
    el.grid.innerHTML = svg;
    // Outside the working zone the horizon circle would leave the raster: no calculation there.
    // The relief stays visible, slightly dimmed, so the region still reads as one map.
    const z = rhoOfArc(ZONE_KM), H = HALF_KM;
    el.shade.innerHTML = `<path fill-rule="evenodd" d="M${-H} ${-H}H${H}V${H}H${-H}Z M${z} 0A${z} ${z} 0 1 0 ${-z} 0A${z} ${z} 0 1 0 ${z} 0Z"/>`;
  }
  function place(node, uv) { if (!node || !uv) return; const p = toScreen(uv.u, uv.v); node.style.left = p.x + 'px'; node.style.top = p.y + 'px'; }
  function render() {
    const z = size(), b = base(), S = b * V.s;
    el.world.style.width = el.world.style.height = b + 'px';
    el.world.style.transform = `translate(${z.w / 2 - V.cu * S}px,${z.h / 2 - V.cv * S}px) scale(${V.s})`;
    el.grid.style.setProperty('--stroke', (1 / V.s).toFixed(3));
    place(el.marker, st.marker);
    el.marker.classList.toggle('on', !!st.marker);
    el.pinNodes.forEach((n, i) => place(n, st.pins[i]));
    el.labels.forEach(({ node, uv, minZoom }) => { place(node, uv); node.classList.toggle('vis', V.s >= minZoom); });
    readout();
    el.prev.disabled = !hist.length;
    el.reset.disabled = V.s <= 1.001 && Math.abs(V.cu - 0.5) < 1e-6 && Math.abs(V.cv - 0.5) < 1e-6;
    el.zoomIn.disabled = V.s >= MAXZ - 1e-6;
    el.zoomOut.disabled = V.s <= 1.001;
    readout();
  }
  function readout() {
    let text = '×' + V.s.toFixed(1);
    if (st.hover) {
      const uv = toUV(st.ptr.x, st.ptr.y);
      if (uv.u < 0 || uv.u > 1 || uv.v < 0 || uv.v > 1) text = 'Outside the map · ' + text;
      else {
        const p = xyToLatLon(...Object.values(xyOfUV(uv.u, uv.v)));
        text = fmtLat(p.lat) + ' ' + fmtLon(p.lon) + (arcKm(p.lat) > ZONE_KM ? ' · outside zone' : '') + ' · ' + text;
      }
    }
    el.readout.textContent = text;
  }

  // ---------- placing and menu ----------
  function mapXY(e) { const r = el.map.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
  function placeAtScreen(x, y) {
    const uv = toUV(x, y);
    if (uv.u < 0 || uv.u > 1 || uv.v < 0 || uv.v > 1) { opts.toast('Outside the map area.'); return; }
    const xy = xyOfUV(uv.u, uv.v), p = xyToLatLon(xy.x, xy.y);
    opts.onPlace(p.lat, p.lon);
  }
  function openMenu(x, y) {
    st.menuUV = toUV(x, y);
    const m = el.ctx, z = size();
    m.classList.add('open');
    m.style.left = Math.min(x, z.w - m.offsetWidth - 8) + 'px';
    m.style.top = Math.min(y, z.h - m.offsetHeight - 8) + 'px';
    const first = m.querySelector('button'); if (first) first.focus({ preventScroll: true });
  }
  function closeMenu() { el.ctx.classList.remove('open'); }
  function runMenu(act) {
    const uv = st.menuUV; closeMenu(); if (!uv) return;
    const s = toScreen(uv.u, uv.v), xy = xyOfUV(uv.u, uv.v), p = xyToLatLon(xy.x, xy.y);
    if (act === 'place') placeAtScreen(s.x, s.y);
    else if (act === 'copy') {
      const text = p.lat.toFixed(4) + ', ' + p.lon.toFixed(4);
      const done = () => opts.toast('Copied ' + text), fail = () => opts.toast('Copy is not available here: ' + text);
      try { navigator.clipboard.writeText(text).then(done, fail); } catch (e) { fail(); }
    } else if (act === 'zoom') { pushHist(); animateTo({ s: V.s * 2.5, cu: uv.u, cv: uv.v }); }
    else if (act === 'reset') resetView();
  }

  // ---------- events ----------
  const typing = e => { const t = e.target; return t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.isContentEditable); };
  function bind() {
    const map = el.map;
    map.addEventListener('mousedown', e => {
      if (e.target.closest('.map-ui') || e.target.closest('.ctx')) return;
      closeMenu();
      if (e.button === 1 || (e.button === 0 && st.space)) {
        e.preventDefault(); st.pan = { x: e.clientX, y: e.clientY, button: e.button }; map.classList.add('is-panning'); return;
      }
      if (e.button === 0) { e.preventDefault(); const p = mapXY(e); st.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, box: false }; map.focus({ preventScroll: true }); }
    });
    window.addEventListener('mousemove', e => {
      const p = mapXY(e); st.ptr = p;
      if (st.pan) { panBy(e.clientX - st.pan.x, e.clientY - st.pan.y); st.pan.x = e.clientX; st.pan.y = e.clientY; }
      if (st.drag) {
        const d = st.drag; d.x1 = p.x; d.y1 = p.y;
        if (!d.box && Math.hypot(p.x - d.x0, p.y - d.y0) > DRAG_PX) d.box = true;
        if (d.box) {
          const sb = el.selbox; sb.classList.add('on');
          sb.style.left = Math.min(d.x0, d.x1) + 'px'; sb.style.top = Math.min(d.y0, d.y1) + 'px';
          sb.style.width = Math.abs(d.x1 - d.x0) + 'px'; sb.style.height = Math.abs(d.y1 - d.y0) + 'px';
        }
      }
      if (st.hover) readout();
    });
    window.addEventListener('mouseup', e => {
      if (st.pan && e.button === st.pan.button) { st.pan = null; map.classList.remove('is-panning'); }
      if (st.drag && e.button === 0) {
        const d = st.drag; st.drag = null; el.selbox.classList.remove('on');
        if (!d.box) placeAtScreen(d.x0, d.y0);                       // a click places the site
        else if (Math.abs(d.x1 - d.x0) > 12 && Math.abs(d.y1 - d.y0) > 12) zoomToRect(d.x0, d.y0, d.x1, d.y1);
      }
    });
    map.addEventListener('auxclick', e => { if (e.button === 1) e.preventDefault(); });
    map.addEventListener('mouseenter', () => {
      st.hover = true; readout();
      // While the pointer is over the map, its keys belong to the map: release focus from form fields.
      const ae = document.activeElement;
      if (ae && ae !== map && /^(INPUT|SELECT|BUTTON|TEXTAREA)$/.test(ae.tagName) && !ae.closest('.map')) ae.blur();
    });
    map.addEventListener('mouseleave', () => { st.hover = false; readout(); });
    map.addEventListener('contextmenu', e => { e.preventDefault(); if (!e.target.closest('.ctx')) { const p = mapXY(e); openMenu(p.x, p.y); } });
    el.ctx.addEventListener('click', e => { const b = e.target.closest('button'); if (b) runMenu(b.dataset.act); });
    document.addEventListener('mousedown', e => { if (!e.target.closest('.ctx')) closeMenu(); });
    window.addEventListener('blur', () => { closeMenu(); st.space = false; });
    map.addEventListener('wheel', e => {
      closeMenu();
      if (e.ctrlKey) {
        e.preventDefault();
        const now = Date.now(); if (now - st.lastWheel > 450) pushHist(); st.lastWheel = now;
        const p = mapXY(e); zoomAt(p.x, p.y, Math.exp(-e.deltaY * 0.0022), false);
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { e.preventDefault(); panBy(-e.deltaX, 0); }
      // A plain vertical wheel scrolls the page, by design.
    }, { passive: false });
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape') { toggleHelp(false); if (st.drag) { st.drag = null; el.selbox.classList.remove('on'); } closeMenu(); return; }
      if (typing(e)) return;
      if (e.code === 'Space' && st.hover) { st.space = true; map.classList.add('space-pan'); e.preventDefault(); return; }
      if (st.space && e.key.startsWith('Arrow')) {
        e.preventDefault();
        panBy({ ArrowLeft: PAN_STEP, ArrowRight: -PAN_STEP }[e.key] || 0, { ArrowUp: PAN_STEP, ArrowDown: -PAN_STEP }[e.key] || 0);
        return;
      }
      if (e.code === 'Period' && st.hover && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); placeAtScreen(st.ptr.x, st.ptr.y); return; }
      if (e.key === 'Backspace' && st.hover) { e.preventDefault(); prevView(); }
    });
    window.addEventListener('keyup', e => { if (e.code === 'Space') { st.space = false; map.classList.remove('space-pan'); } });
    el.zoomIn.addEventListener('click', () => zoomCenter(1.5));
    el.zoomOut.addEventListener('click', () => zoomCenter(1 / 1.5));
    el.reset.addEventListener('click', resetView);
    el.prev.addEventListener('click', prevView);
    el.zone.addEventListener('click', zoneView);
    el.helpBtn.addEventListener('click', () => toggleHelp());
    window.addEventListener('resize', () => { clampV(V); render(); });
  }

  function init(o) {
    opts = o;
    for (const [k, id] of Object.entries({ map: 'map', world: 'world', grid: 'grid', marker: 'marker', pinLayer: 'pin-layer', labelLayer: 'label-layer',
      ctx: 'ctx', selbox: 'selbox', readout: 'readout', zoomIn: 'zoom-in', zoomOut: 'zoom-out', reset: 'zoom-reset', prev: 'zoom-prev', zone: 'zoom-zone', help: 'map-help', helpBtn: 'map-help-toggle' })) el[k] = document.getElementById(id);
    el.pinNodes = [];
    el.shade = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    el.shade.setAttribute('class', 'zone-shade'); el.shade.setAttribute('aria-hidden', 'true');
    el.shade.setAttribute('viewBox', `${-HALF_KM} ${-HALF_KM} ${2 * HALF_KM} ${2 * HALF_KM}`);
    el.shade.setAttribute('preserveAspectRatio', 'none');
    el.world.insertBefore(el.shade, el.grid);
    // Only features that fall on the basemap; the rest would float over the empty border.
    el.labels = (window.FEATURES || []).filter(f => { const p = latLonToXY(f.lat, f.lon); return Math.abs(p.x) < HALF_KM && Math.abs(p.y) < HALF_KM; }).map(f => {
      const node = document.createElement('div'); node.className = 'flabel'; node.textContent = f.name;
      el.labelLayer.appendChild(node);
      return { node, uv: uvOfXY(latLonToXY(f.lat, f.lon)), minZoom: f.minZoom };
    });
    drawGrid(); bind(); render();
  }

  window.MapView = {
    init,
    latLonToXY, xyToLatLon, arcKm, ZONE_KM, HALF_KM,
    setMarker(lat, lon) { st.marker = lat == null ? null : uvOfXY(latLonToXY(lat, lon)); render(); },
    setPins(list) {
      el.pinLayer.innerHTML = '';
      st.pins = list.map(p => uvOfXY(latLonToXY(p.lat, p.lon)));
      el.pinNodes = list.map(p => {
        const d = document.createElement('div'); d.className = 'pin'; d.textContent = p.name; d.title = 'Comparison site ' + p.name;
        el.pinLayer.appendChild(d); return d;
      });
      render();
    },
    setLabels(on) { el.map.classList.toggle('labels-off', !on); },
    setGrid(on) { el.grid.style.display = on ? '' : 'none'; },
    redraw: () => { clampV(V); render(); },
  };
})();
