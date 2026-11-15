/* LUCY site view: the terrain around the selected site, with the Sun and Earth.
   Two cameras over the same terrain:
   - Overview (default): a camera above the site looking down; drag rotates and tilts, wheel zooms.
     Only the camera moves; terrain, scale and the calculation are the same as in Observer.
   - Observer: the camera stays at the instrument height and looks around (drag, wheel, arrows).

   What is drawn and from where:
   - near terrain: GET /api/terrain-patch, a 20 x 20 km LOLA grid in the site's local frame,
     including the drop of the lunar sphere, no vertical exaggeration;
   - far terrain: the same horizon profile the calculation uses (720 directions, out to 150 km),
     drawn as a silhouette ring, so what hides the Sun here is what hides it in the numbers;
   - Sun and Earth: GET /api/instant for the selected minute; their discs are enlarged for
     visibility, their positions are exact. Relief shading follows the Sun direction; cast
     shadows are not drawn.
   Three.js coordinates: x = east, y = up, z = -north (metres). */
import * as THREE from './vendor/three.module.min.js';

const Api = window.LucyApi;
const RING_M = 30000;          // silhouette ring: beyond the near grid (its corners are at 14.1 km)
const SKY_M = 40000;           // Sun and Earth sit beyond the ring, so the ring hides them when it should
const LABEL_M = 36000;
const MIN_EYE_M = 2;           // camera at the instrument height, never below 2 m (a standing observer)
const SUN_DRAW_DEG = 2.0;      // real Sun: 0.53 deg
const EARTH_DRAW_DEG = 2.6;    // real Earth: about 1.9 deg
const COLORS = { sun: 0xd8bb79, earth: 0x82b2cc };
const OVERVIEW = { fov: 45, pitchDeg: 50, distM: 11000, minDistM: 1500, maxDistM: 40000, minPitchDeg: 8, maxPitchDeg: 88, arrowM: 4500, edgeM: 11500 };

const dirOf = (altDeg, azDeg) => {
  const a = THREE.MathUtils.degToRad(altDeg), z = THREE.MathUtils.degToRad(azDeg);
  return new THREE.Vector3(Math.cos(a) * Math.sin(z), Math.sin(a), -Math.cos(a) * Math.cos(z));
};
const fmt = v => (v == null ? '—' : Number(v).toFixed(2));

function webglAvailable() {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch (e) { return false; }
}

function textSprite(text, color) {
  const c = document.createElement('canvas'); c.width = 256; c.height = 64;
  const g = c.getContext('2d');
  g.font = '500 30px Menlo, Consolas, monospace'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.lineWidth = 6; g.strokeStyle = 'rgba(0,0,0,.85)'; g.strokeText(text, 128, 32);
  g.fillStyle = color; g.fillText(text, 128, 32);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.renderOrder = 10;
  return s;
}

function discSprite(color, glow) {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'), grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  const hex = '#' + color.toString(16).padStart(6, '0');
  grd.addColorStop(0, '#ffffff'); grd.addColorStop(glow ? 0.35 : 0.55, hex); grd.addColorStop(glow ? 0.5 : 0.62, hex);
  grd.addColorStop(glow ? 1 : 0.66, 'rgba(0,0,0,0)');
  g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  return new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
}

class SiteView {
  constructor(container) {
    this.el = container;
    this.el.innerHTML = '<div class="s3d-canvas"></div>'
      + '<div class="s3d-hud s3d-top"><b>SITE VIEW</b> <span data-k="site"></span><span data-k="time"></span></div>'
      + '<div class="s3d-hud s3d-bodies"><div data-k="sun"></div><div data-k="earth"></div></div>'
      + '<div class="s3d-hud s3d-tools"><span class="s3d-modes"><button type="button" data-mode="overview" aria-pressed="true">Overview</button><button type="button" data-mode="observer" aria-pressed="false">Observer</button></span>'
      + '<button type="button" data-look="sun">Look at Sun</button><button type="button" data-look="earth">Look at Earth</button></div>'
      + '<div class="s3d-hud s3d-note"><span data-for="overview">Overview: drag to rotate and tilt · Wheel: zoom · Arrows: rotate<br>Camera above the site; the arrows point to the Sun and Earth. Terrain ±10 km from LOLA, no vertical exaggeration. Shading lit for readability; cast shadows not drawn.</span>'
      + '<span data-for="observer">Observer: drag to look around · Wheel: zoom · Arrows: turn<br>Camera at the instrument height. Terrain ±10 km from LOLA, no vertical exaggeration. Far ridges: horizon profile to 150 km. Sun and Earth discs enlarged; positions exact. Shading lit for readability; cast shadows not drawn.</span></div>'
      + '<div class="s3d-status" data-k="status" hidden></div>';
    this.q = k => this.el.querySelector(`[data-k="${k}"]`);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.el.querySelector('.s3d-canvas').appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x020203);
    this.camera = new THREE.PerspectiveCamera(60, 1, 5, 120000);
    this.mode = 'overview';
    this.yaw = 0; this.pitch = 0;                  // observer: radians; yaw = azimuth of the view direction
    this.orbit = { yaw: 0, pitch: THREE.MathUtils.degToRad(OVERVIEW.pitchDeg), dist: OVERVIEW.distM };
    this.observerFov = this.camera.fov;
    this.eyeY = MIN_EYE_M;
    this.ambient = new THREE.AmbientLight(0xffffff, 0.45);
    this.sunLight = new THREE.DirectionalLight(0xfff4e0, 2.2);
    this.scene.add(this.ambient, this.sunLight, this.sunLight.target);
    this.world = new THREE.Group(); this.scene.add(this.world);
    // far: only for the Observer camera (horizon ring, compass); near: only for Overview (marker, arrows).
    this.far = new THREE.Group(); this.near = new THREE.Group();
    this.world.add(this.far, this.near);
    this.bodies = {};
    for (const k of ['sun', 'earth']) {
      const disc = discSprite(COLORS[k], k === 'sun'), label = textSprite(k === 'sun' ? 'SUN' : 'EARTH', k === 'sun' ? '#d8bb79' : '#82b2cc');
      const size = 2 * SKY_M * Math.tan(THREE.MathUtils.degToRad(k === 'sun' ? SUN_DRAW_DEG : EARTH_DRAW_DEG) / 2) * (k === 'sun' ? 2.2 : 1.6);
      disc.scale.set(size, size, 1); label.scale.set(5200, 1300, 1);
      disc.visible = label.visible = false;
      this.scene.add(disc, label);
      this.bodies[k] = { disc, label, state: null, arrow: null, tag: null };
    }
    this.site = null; this.key = null; this.time = null; this.instant = null; this.frame = 0;
    this.bindInput();
    this.el.querySelectorAll('[data-look]').forEach(b => { b.onclick = () => this.lookAt(b.dataset.look); });
    this.el.querySelectorAll('[data-mode]').forEach(b => { b.onclick = () => this.setMode(b.dataset.mode); });
    this.el.dataset.mode = this.mode;
    new ResizeObserver(() => this.resize()).observe(this.el);
  }

  // The first time the Observer camera is shown for a site, face the Sun (or Earth if only Earth is up).
  faceFirstBody() {
    const g = this.instant;
    if (this.mode !== 'observer' || this.faced || !g) return;
    this.faced = true;
    this.lookAt(g.sun.visible || !g.earth.visible ? 'sun' : 'earth');
  }

  setMode(mode) {
    if (mode === this.mode || (mode !== 'overview' && mode !== 'observer')) return;
    if (this.mode === 'observer') this.observerFov = this.camera.fov;
    this.mode = mode;
    this.el.dataset.mode = mode;
    this.el.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    this.camera.fov = mode === 'overview' ? OVERVIEW.fov : this.observerFov;
    this.camera.updateProjectionMatrix();
    this.applyMode();
    this.faceFirstBody();
  }

  // Shows only what belongs to the current camera; Overview also lifts the shading floor so night terrain stays readable.
  applyMode() {
    const overview = this.mode === 'overview';
    this.far.visible = !overview; this.near.visible = overview;
    for (const b of Object.values(this.bodies)) b.disc.visible = b.label.visible = !overview && !!b.state;
    this.ambient.intensity = overview ? 0.8 : 0.45;
    if (this.instant) this.shade(this.instant);
    this.request();
  }

  status(text) { const s = this.q('status'); s.textContent = text || ''; s.hidden = !text; }
  request() { if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); }); }

  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.request();
  }

  draw() {
    if (this.mode === 'overview') {
      // Orbit around the site: the camera sits behind the view direction, raised by the tilt angle.
      const { yaw, pitch, dist } = this.orbit, back = dirOf(0, THREE.MathUtils.radToDeg(yaw));
      this.camera.position.set(-back.x * dist * Math.cos(pitch), dist * Math.sin(pitch), -back.z * dist * Math.cos(pitch));
      this.camera.lookAt(0, 0, 0);
    } else {
      this.camera.position.set(0, this.eyeY, 0);
      const dir = dirOf(THREE.MathUtils.radToDeg(this.pitch), THREE.MathUtils.radToDeg(this.yaw));
      this.camera.lookAt(this.camera.position.clone().add(dir));
    }
    this.renderer.render(this.scene, this.camera);
  }

  bindInput() {
    const c = this.el;
    let drag = null;
    c.addEventListener('pointerdown', e => {
      if (e.target.closest('button')) return;
      drag = { x: e.clientX, y: e.clientY }; c.setPointerCapture(e.pointerId); c.classList.add('is-dragging');
    });
    c.addEventListener('pointermove', e => {
      if (!drag) return;
      const k = THREE.MathUtils.degToRad(this.camera.fov) / c.clientHeight;
      this.turn(-(e.clientX - drag.x) * k, (e.clientY - drag.y) * k);
      drag = { x: e.clientX, y: e.clientY };
    });
    const end = () => { drag = null; c.classList.remove('is-dragging'); };
    c.addEventListener('pointerup', end); c.addEventListener('pointercancel', end);
    c.addEventListener('wheel', e => {
      e.preventDefault();
      if (this.mode === 'overview') {
        this.orbit.dist = THREE.MathUtils.clamp(this.orbit.dist * Math.exp(e.deltaY * 0.001), OVERVIEW.minDistM, OVERVIEW.maxDistM);
      } else {
        this.camera.fov = THREE.MathUtils.clamp(this.camera.fov * Math.exp(e.deltaY * 0.001), 8, 90);
        this.camera.updateProjectionMatrix();
      }
      this.request();
    }, { passive: false });
    c.tabIndex = 0;
    c.addEventListener('keydown', e => {
      const s = THREE.MathUtils.degToRad(this.camera.fov / 12);
      const map = { ArrowLeft: [-s, 0], ArrowRight: [s, 0], ArrowUp: [0, s], ArrowDown: [0, -s] }[e.key];
      if (map) { e.preventDefault(); this.turn(map[0], map[1]); }
    });
  }

  // dx turns toward east (clockwise seen from above), dy raises the view.
  turn(dx, dy) {
    if (this.mode === 'overview') {
      // Dragging down tilts the camera toward the top view; dragging sideways rotates around the site.
      const o = this.orbit;
      o.yaw = (o.yaw + dx) % (2 * Math.PI);
      o.pitch = THREE.MathUtils.clamp(o.pitch + dy, THREE.MathUtils.degToRad(OVERVIEW.minPitchDeg), THREE.MathUtils.degToRad(OVERVIEW.maxPitchDeg));
      this.request();
      return;
    }
    this.yaw = (this.yaw + dx) % (2 * Math.PI);
    this.pitch = THREE.MathUtils.clamp(this.pitch + dy, -THREE.MathUtils.degToRad(60), THREE.MathUtils.degToRad(80));
    this.request();
  }

  lookAt(body) {
    const s = this.bodies[body].state;
    if (!s) return;
    this.yaw = THREE.MathUtils.degToRad(s.az_deg);
    this.pitch = THREE.MathUtils.degToRad(THREE.MathUtils.clamp(s.alt_deg, -20, 60));
    this.request();
  }

  async show(o) {
    const key = [o.lat, o.lon, o.height_m].join(',');
    this.q('site').textContent = `${Number(o.lat).toFixed(4)}°, ${Number(o.lon).toFixed(4)}° · ground ${o.ground_m == null ? '—' : Number(o.ground_m).toFixed(0)} m · instrument ${Number(o.height_m)} m`;
    if (key !== this.key) {
      this.key = key; this.site = o;
      this.clearWorld();
      this.status('Loading terrain around the site…');
      try {
        const p = await Api.terrainPatch({ lat: o.lat, lon: o.lon, height: o.height_m });
        if (this.key !== key) return;
        if (p.data_status !== 'ok') { this.status('No terrain data around this site.'); return; }
        this.build(p, o);
        this.status('');
      } catch (e) {
        if (this.key === key) { this.status('Site view unavailable: ' + e.message); this.key = null; }
        return;
      }
    }
    await this.setTime(o.time_utc, true);
  }

  clearWorld() {
    for (const obj of [...this.world.children]) {
      if (obj === this.far || obj === this.near) continue;
      this.world.remove(obj);
      obj.traverse(n => { if (n.geometry) n.geometry.dispose(); if (n.material) [].concat(n.material).forEach(m => { if (m.map) m.map.dispose(); m.dispose(); }); });
    }
    for (const group of [this.far, this.near]) {
      for (const obj of [...group.children]) {
        group.remove(obj);
        obj.traverse(n => { if (n.geometry) n.geometry.dispose(); if (n.material) [].concat(n.material).forEach(m => { if (m.map) m.map.dispose(); m.dispose(); }); });
      }
    }
    for (const b of Object.values(this.bodies)) { b.disc.visible = b.label.visible = false; b.state = null; b.arrow = b.tag = null; }
    this.instant = null; this.faced = false;
    this.request();
  }

  build(p, o) {
    const g = p.grid, n = g.size, half = g.half_size_m, step = g.step_m, up = g.up_m;
    // Near terrain: row 0 = north edge (z = -half), column 0 = west edge (x = -half).
    const pos = new Float32Array(n * n * 3);
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      const i = r * n + c;
      pos[i * 3] = -half + c * step; pos[i * 3 + 1] = up[i]; pos[i * 3 + 2] = -half + r * step;
    }
    const idx = new Uint32Array((n - 1) * (n - 1) * 6);
    let k = 0;
    for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) {
      const a = r * n + c, b = a + 1, d = a + n, e = d + 1;
      idx[k++] = a; idx[k++] = d; idx[k++] = b; idx[k++] = b; idx[k++] = d; idx[k++] = e;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setIndex(new THREE.BufferAttribute(idx, 1));
    geo.computeVertexNormals();
    this.world.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: 0x9a9a9a })));

    // Eye: instrument height above the ground at the site (the grid's zero).
    const eye = Math.max(Number(o.height_m) || 0, MIN_EYE_M);
    this.eyeY = eye;

    // Far terrain: the horizon profile as a silhouette ring. The profile is measured from the
    // instrument height, so the ring top sits at that height plus r * tan(elevation).
    const h = p.horizon, m = h.elevation_deg.length, ringPos = new Float32Array((m + 1) * 2 * 3), linePos = new Float32Array((m + 1) * 3);
    const obs = Number(h.observer_height_m) || 0;
    for (let i = 0; i <= m; i++) {
      const j = i % m, az = (j + 0.5) * h.step_deg, d = dirOf(0, az);
      const top = obs + RING_M * Math.tan(THREE.MathUtils.degToRad(h.elevation_deg[j]));
      const bottom = obs - RING_M * Math.tan(THREE.MathUtils.degToRad(25));
      ringPos.set([d.x * RING_M, top, d.z * RING_M, d.x * RING_M, bottom, d.z * RING_M], i * 6);
      linePos.set([d.x * RING_M * 0.999, top, d.z * RING_M * 0.999], i * 3);
    }
    const ringIdx = [];
    for (let i = 0; i < m; i++) { const a = i * 2, b = a + 1, c = a + 2, d = a + 3; ringIdx.push(a, b, c, c, b, d); }
    const ring = new THREE.BufferGeometry();
    ring.setAttribute('position', new THREE.BufferAttribute(ringPos, 3)); ring.setIndex(ringIdx);
    this.far.add(new THREE.Mesh(ring, new THREE.MeshBasicMaterial({ color: 0x2b2b2b, side: THREE.DoubleSide })));
    const crest = new THREE.BufferGeometry(); crest.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
    this.far.add(new THREE.Line(crest, new THREE.LineBasicMaterial({ color: 0x8a8a8a })));

    // Mathematical horizon (altitude 0) for reference: dashed, at the instrument height.
    const zero = [];
    for (let i = 0; i <= 360; i++) { const d = dirOf(0, i); zero.push(d.x * LABEL_M, obs, d.z * LABEL_M); }
    const zg = new THREE.BufferGeometry(); zg.setAttribute('position', new THREE.Float32BufferAttribute(zero, 3));
    const zl = new THREE.Line(zg, new THREE.LineDashedMaterial({ color: 0x5a5a5a, dashSize: 600, gapSize: 500, depthTest: false }));
    zl.computeLineDistances(); zl.renderOrder = 5; this.far.add(zl);

    // Compass: N E S W and ticks every 30 degrees on the mathematical horizon.
    for (const [az, name] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W'], [30, '30'], [60, '60'], [120, '120'], [150, '150'], [210, '210'], [240, '240'], [300, '300'], [330, '330']]) {
      const s = textSprite(name, name.length === 1 ? '#e6e6e6' : '#8d8d8d'), d = dirOf(0, az);
      s.position.set(d.x * LABEL_M, obs + LABEL_M * Math.tan(THREE.MathUtils.degToRad(-1.6)), d.z * LABEL_M);
      s.scale.set(name.length === 1 ? 3600 : 3000, name.length === 1 ? 900 : 750, 1);
      this.far.add(s);
    }
    this.buildOverview();
    this.yaw = 0; this.pitch = 0;
    this.orbit = { yaw: 0, pitch: THREE.MathUtils.degToRad(OVERVIEW.pitchDeg), dist: OVERVIEW.distM };
    this.applyMode();
    this.resize();
  }

  // Overview extras: a site marker, compass letters at the edge of the terrain patch, and one arrow
  // per body in its true direction (altitude and azimuth from /api/instant).
  buildOverview() {
    const fixed = (text, color, w, h) => {
      const s = textSprite(text, color); s.material.sizeAttenuation = false; s.scale.set(w, h, 1); return s;
    };
    const mark = discSprite(0xffffff, false); mark.material.sizeAttenuation = false; mark.material.depthTest = false;
    mark.scale.set(0.035, 0.035, 1); mark.renderOrder = 9; this.near.add(mark);
    const pin = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 900, 0)]),
      new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false }));
    pin.renderOrder = 9; this.near.add(pin);
    const tag = fixed('SITE', '#ffffff', 0.3, 0.075); tag.position.set(0, 1250, 0); this.near.add(tag);
    for (const [az, name] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W']]) {
      const d = dirOf(0, az), s = fixed(name, '#e6e6e6', 0.15, 0.0375);
      s.position.set(d.x * OVERVIEW.edgeM, 0, d.z * OVERVIEW.edgeM); this.near.add(s);
    }
    for (const k of ['sun', 'earth']) {
      const b = this.bodies[k], color = COLORS[k];
      b.arrow = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0), OVERVIEW.arrowM, color, 700, 380);
      for (const m of [b.arrow.line.material, b.arrow.cone.material]) { m.depthTest = false; m.transparent = true; }
      b.arrow.renderOrder = 8; b.arrow.line.renderOrder = b.arrow.cone.renderOrder = 8;
      b.arrow.visible = false;
      b.tag = fixed(k === 'sun' ? 'SUN' : 'EARTH', k === 'sun' ? '#d8bb79' : '#82b2cc', 0.28, 0.07);
      b.tag.visible = false;
      this.near.add(b.arrow, b.tag);
    }
    if (this.instant) this.placeArrows(this.instant);
  }

  placeArrows(g) {
    for (const k of ['sun', 'earth']) {
      const s = g[k], b = this.bodies[k];
      if (!b.arrow) continue;
      const d = dirOf(s.alt_deg, s.az_deg), op = s.visible ? 1 : 0.4;
      b.arrow.setDirection(d);
      b.arrow.line.material.opacity = b.arrow.cone.material.opacity = b.tag.material.opacity = op;
      b.tag.position.copy(d.clone().multiplyScalar(OVERVIEW.arrowM + 900));
      b.arrow.visible = b.tag.visible = true;
    }
  }

  async setTime(iso, force) {
    if (!this.site || !iso) return;
    if (iso === this.time && !force) return;
    this.time = iso;
    this.q('time').textContent = ' · ' + iso.slice(0, 16).replace('T', ' ') + ' UTC';
    clearTimeout(this.timer);
    await new Promise(r => { this.timer = setTimeout(r, force ? 0 : 120); });
    if (this.time !== iso) return;
    if (this.ctl) this.ctl.abort();
    const ctl = this.ctl = new AbortController(), o = this.site;
    try {
      const g = await Api.instant({ lat: o.lat, lon: o.lon, time: iso, height: o.height_m }, ctl.signal);
      if (ctl.signal.aborted || this.time !== iso || g.data_status !== 'ok') return;
      const firstLook = !this.bodies.sun.state;
      this.placeBodies(g);
      if (firstLook) this.faceFirstBody();
    } catch (e) {
      if (e.name !== 'AbortError') this.status('Sun and Earth positions unavailable: ' + e.message);
    }
  }

  placeBodies(g) {
    const obs = this.eyeY;
    this.instant = g;
    for (const k of ['sun', 'earth']) {
      const s = g[k], b = this.bodies[k], d = dirOf(s.alt_deg, s.az_deg);
      b.state = s;
      b.disc.position.set(d.x * SKY_M, obs + d.y * SKY_M, d.z * SKY_M);
      const l = dirOf(s.alt_deg + (k === 'sun' ? 3.2 : 3.6), s.az_deg);
      b.label.position.set(l.x * SKY_M, obs + l.y * SKY_M, l.z * SKY_M);
      b.disc.visible = b.label.visible = this.mode === 'observer';
      b.label.material.opacity = s.visible ? 1 : 0.55;
      this.q(k).innerHTML = `<i class="s3d-key" data-body="${k}"></i><b>${k === 'sun' ? 'Sun' : 'Earth'}</b> ${s.visible ? 'VISIBLE' : 'BLOCKED'}`
        + ` · alt ${fmt(s.alt_deg)}° · terrain ${fmt(s.horizon_deg)}° · clearance ${s.margin_deg >= 0 ? '+' : ''}${fmt(s.margin_deg)}° · az ${fmt(s.az_deg)}°`;
    }
    this.placeArrows(g);
    this.shade(g);
  }

  // Relief shading from the Sun direction; very dim when the Sun is below the mathematical horizon
  // (Overview keeps a higher floor so the terrain stays readable).
  shade(g) {
    const sd = dirOf(g.sun.alt_deg, g.sun.az_deg);
    this.sunLight.position.copy(sd.clone().multiplyScalar(20000)); this.sunLight.target.position.set(0, 0, 0);
    this.sunLight.intensity = g.sun.alt_deg > -1 ? 2.2 : (this.mode === 'overview' ? 0.5 : 0.15);
    this.request();
  }
}

let view = null;
if (webglAvailable() && Api) {
  window.Site3D = {
    mount(container) { if (!view) view = new SiteView(container); },
    show(o) { if (view) view.show(o); },
    setTime(iso) { if (view) view.setTime(iso); },
    resize() { if (view) view.resize(); },
  };
  document.dispatchEvent(new Event('lucy:site3d-ready'));
}
// Without WebGL window.Site3D stays undefined and the page keeps the 2D map only.
