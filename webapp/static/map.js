const map = L.map('map').setView([46.1, 11.4], 8);
window.pilzeMap = map;   // handle per debug in console
L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  maxZoom: 17, attribution: '© OpenTopoMap, OpenStreetMap contributors'
}).addTo(map);

// --- pannello collassabile + mobile ---------------------------------------- //
map.zoomControl.setPosition('topright');            // libera il top-left per il toggle
document.getElementById('panel-toggle').addEventListener('click', () => {
  document.body.classList.toggle('nav-collapsed');
  setTimeout(() => map.invalidateSize(), 260);      // ridisegna dopo la transizione
});
if (window.innerWidth <= 700) document.body.classList.add('nav-collapsed');
const isTouch = window.matchMedia('(hover: none)').matches;   // niente hover (telefono) → tap

const sel = document.getElementById('species');
const status = document.getElementById('status');
let pronteLayer = null, pinsLayer = null;

function setStatus(t) { status.textContent = t; }

// --- overlay idoneità: griglia fucsia su canvas ---------------------------- //
// Il server serve i punteggi per cella (griglia in EPSG:3857, come la mappa base,
// quantizzata a uint8). Il canvas li disegna: colore per punteggio, soglia (cutoff)
// e opacità client-side (istantanee), hover → punteggio della cella. La griglia in
// 3857 rende le celle quadrate a schermo e allineate ai tile.
const GridLayer = L.Layer.extend({
  initialize() { this._grid = null; this._cutoff = 0.3; },

  onAdd(map) {
    this._map = map;
    const c = this._canvas = L.DomUtil.create('canvas', 'pilze-grid leaflet-zoom-hide');
    map.getPanes().overlayPane.appendChild(c);
    map.on('moveend zoomend resize viewreset load', this._reset, this);
    this._reset();
    return this;
  },

  // dimensioni correnti dal container (map.getSize() può restare in cache a 0×0)
  _size() { const el = this._map.getContainer(); return { x: el.clientWidth, y: el.clientHeight }; },

  onRemove(map) {
    map.off('moveend zoomend resize viewreset load', this._reset, this);
    if (this._canvas) L.DomUtil.remove(this._canvas);
    this._canvas = null;
  },

  setGrid(g) { this._grid = g; this._reset(); },
  setCutoff(v) { this._cutoff = v; this._draw(); },
  setOpacity(v) { if (this._canvas) this._canvas.style.opacity = v; },

  // punteggio della cella sotto il cursore, o null (fuori bosco / sotto soglia)
  scoreAt(ll) {
    const g = this._grid; if (!g) return null;
    const [minX, minY, maxX, maxY] = g.bbox;
    const p = this._map.options.crs.project(ll);       // lat/lon → metri 3857
    const j = Math.floor((p.x - minX) / ((maxX - minX) / g.nx));
    const i = Math.floor((maxY - p.y) / ((maxY - minY) / g.ny));
    if (i < 0 || i >= g.ny || j < 0 || j >= g.nx) return null;
    const v = g.data[i * g.nx + j];
    if (!v) return null;
    const score = v / 255 * g.score_max;
    return score >= this._cutoff ? score : null;
  },

  _reset() {
    const c = this._canvas; if (!c) return;
    const size = this._size();
    if (c.width !== size.x) c.width = size.x;
    if (c.height !== size.y) c.height = size.y;
    L.DomUtil.setPosition(c, this._map.containerPointToLayerPoint([0, 0]));
    this._draw();
  },

  _draw() {
    const c = this._canvas; if (!c) return;
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, c.width, c.height);
    const g = this._grid; if (!g) return;

    const map = this._map, crs = map.options.crs, [minX, minY, maxX, maxY] = g.bbox;
    const dx = (maxX - minX) / g.nx, dy = (maxY - minY) / g.ny;
    // 3857 → schermo è lineare e separabile: x dipende solo da mercX, y solo da mercY.
    const xs = new Float64Array(g.nx + 1), ys = new Float64Array(g.ny + 1);
    for (let j = 0; j <= g.nx; j++)
      xs[j] = map.latLngToContainerPoint(crs.unproject(L.point(minX + j * dx, maxY))).x;
    for (let i = 0; i <= g.ny; i++)
      ys[i] = map.latLngToContainerPoint(crs.unproject(L.point(minX, maxY - i * dy))).y;

    const thr = this._cutoff / g.score_max * 255;
    const W = c.width, H = c.height;
    for (let i = 0; i < g.ny; i++) {
      const y0 = ys[i], y1 = ys[i + 1];
      if (y1 < 0 || y0 > H) continue;
      const row = i * g.nx, ch = y1 - y0;
      for (let j = 0; j < g.nx; j++) {
        const v = g.data[row + j];
        if (v === 0 || v < thr) continue;
        const x0 = xs[j], x1 = xs[j + 1];
        if (x1 < 0 || x0 > W) continue;
        const light = 88 - (v / 255) * 48;             // fucsia: 88%→40% con il punteggio
        ctx.fillStyle = `hsl(320, 92%, ${light}%)`;
        ctx.fillRect(x0, y0, x1 - x0 + 0.6, ch + 0.6); // +0.6 evita cuciture subpixel
      }
    }
  }
});
const gridLayer = new GridLayer();

const cutoff = document.getElementById('cutoff'), cutVal = document.getElementById('cut-val');
const opacity = document.getElementById('opacity'), opVal = document.getElementById('op-val');

function b64ToBytes(b64) {
  const bin = atob(b64), a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  return a;
}

async function loadStatic() {
  const on = document.getElementById('l-static').checked;
  document.getElementById('static-ctl').classList.toggle('off', !on);
  if (!on) { if (map.hasLayer(gridLayer)) map.removeLayer(gridLayer); gridLayer.setGrid(null); return; }
  setStatus('carico idoneità…');
  const r = await fetch(`/api/suitability/${sel.value}/grid`);
  if (!r.ok) { gridLayer.setGrid(null); setStatus('nessuna mappa statica per questa specie'); return; }
  const g = await r.json();
  g.data = b64ToBytes(g.cells); delete g.cells;
  if (!map.hasLayer(gridLayer)) gridLayer.addTo(map);
  gridLayer.setOpacity(parseFloat(opacity.value));
  gridLayer._cutoff = parseFloat(cutoff.value);
  gridLayer.setGrid(g);
  setStatus('');
}

cutoff.addEventListener('input', () => {
  cutVal.textContent = parseFloat(cutoff.value).toFixed(2);
  gridLayer.setCutoff(parseFloat(cutoff.value));
});
opacity.addEventListener('input', () => {
  opVal.textContent = Math.round(opacity.value * 100) + '%';
  gridLayer.setOpacity(parseFloat(opacity.value));
});

// hover → tooltip col punteggio della cella
const tip = L.tooltip({ className: 'grid-tip', direction: 'top', offset: [0, -2], opacity: 0.95 });
function hideTip() { if (tip._map) map.removeLayer(tip); }
map.on('mousemove', (ev) => {
  if (!document.getElementById('l-static').checked) return hideTip();
  const sc = gridLayer.scoreAt(ev.latlng);
  if (sc == null) return hideTip();
  tip.setLatLng(ev.latlng).setContent(`idoneità <b>${sc.toFixed(2)}</b>`);
  if (!tip._map) tip.addTo(map);
});
map.on('mouseout', hideTip);
// mobile: al tap fuori da una cella pronte → mostra l'idoneità (o chiude tutto)
if (isTouch) map.on('click', (ev) => {
  const sc = document.getElementById('l-static').checked ? gridLayer.scoreAt(ev.latlng) : null;
  if (sc == null) return hideTip();
  tip.setLatLng(ev.latlng).setContent(`idoneità <b>${sc.toFixed(2)}</b>`);
  if (!tip._map) tip.addTo(map);
});

// --- pronte oggi: fase della buttata per cella meteo (quadrati 2.2km) ------- //
const STATE_COLOR = { in_fieri: '#8ecae6', pronto: '#0077cc', tardi: '#2a3a5c' };
const STATE_LABEL = { in_fieri: 'in fieri', pronto: 'pronto', tardi: 'tardi' };
map.createPane('pronte');
map.getPane('pronte').style.zIndex = 450;                 // sopra il fucsia, sotto i pin
const pronteRenderer = L.canvas({ pane: 'pronte' });
let pronteReq = 0;

function applyPronteOpacity() {
  map.getPane('pronte').style.opacity = document.getElementById('pronte-op').value;
}

async function loadPronte() {
  const myReq = ++pronteReq;
  if (pronteLayer) { map.removeLayer(pronteLayer); pronteLayer = null; }
  const on = document.getElementById('l-pronte').checked;
  document.getElementById('pronte-ctl').classList.toggle('off', !on);
  if (!on) return;
  const gj = await (await fetch(`/api/pronte/${sel.value}`)).json();
  if (myReq !== pronteReq) return;            // superata da una chiamata più recente (no layer doppi)
  if (!gj.features.length) { setStatus('nessuna cella in stato per questa specie'); return; }
  setStatus('');
  pronteLayer = L.geoJSON(gj, {
    pane: 'pronte', renderer: pronteRenderer,
    style: f => ({ stroke: false, fillColor: STATE_COLOR[f.properties.state] || '#888', fillOpacity: 1 }),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      let html = `<b>${STATE_LABEL[p.state] || p.state}</b> · readiness ${p.readiness}`;
      if (p.eta != null) html += `<br>pronto fra ~${p.eta} gg`;
      if (p.days_past != null) html += `<br>buttata ~${p.days_past} gg fa`;
      if (isTouch) {                                          // tap → popup nativo (singolo, si chiude da solo)
        layer.bindPopup(html, { className: 'pronte-pop' });
        layer.on('click', (e) => { L.DomEvent.stopPropagation(e); hideTip(); });
      } else {
        layer.bindTooltip(html, { sticky: true, className: 'pronte-tip' });   // hover desktop
      }
    }
  }).addTo(map);
  applyPronteOpacity();
}

async function loadPins() {
  if (pinsLayer) { map.removeLayer(pinsLayer); pinsLayer = null; }
  if (!document.getElementById('l-pins').checked) return;
  const gj = await (await fetch('/api/pins')).json();
  pinsLayer = L.geoJSON(gj, {
    pointToLayer: (f, ll) => {
      const p = f.properties;
      const color = p.is_blank ? '#888' : '#2a7';
      const m = L.circleMarker(ll, { radius: 5, color: '#134', weight: 1, fillColor: color, fillOpacity: 0.9 });
      let html = p.is_blank ? '<b>uscita a vuoto</b>' : `<b>${p.species || '?'}</b>`;
      if (p.phase) html += `<br>fase: ${p.phase}`;
      if (p.weight_g) html += `<br>${p.weight_g} g`;
      if (p.abundance) html += `<br>${p.abundance}`;
      html += `<br><small>${p.ts || ''}</small>`;
      if (p.photo) html += `<br><img src="/photo/${p.id}" style="max-width:180px;margin-top:4px;border-radius:4px">`;
      return m.bindPopup(html);
    }
  }).addTo(map);
}

function reloadAll() { loadStatic(); loadPronte(); loadPins(); }
sel.addEventListener('change', () => { loadStatic(); loadPronte(); });
document.getElementById('l-static').addEventListener('change', loadStatic);
document.getElementById('l-pronte').addEventListener('change', loadPronte);
document.getElementById('pronte-op').addEventListener('input', () => {
  document.getElementById('pronte-op-val').textContent = Math.round(document.getElementById('pronte-op').value * 100) + '%';
  applyPronteOpacity();
});
document.getElementById('l-pins').addEventListener('change', loadPins);
reloadAll();
