const map = L.map('map').setView([46.1, 11.4], 8);
window.pilzeMap = map;   // handle per debug in console
L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  maxZoom: 17, attribution: '© OpenTopoMap, OpenStreetMap contributors'
}).addTo(map);

const sel = document.getElementById('species');
const status = document.getElementById('status');
let pronteLayer = null, pinsLayer = null;

function setStatus(t) { status.textContent = t; }

// --- overlay idoneità: griglia fucsia su canvas ---------------------------- //
// Il server serve i punteggi per cella (griglia 4326 quantizzata a uint8). Il
// canvas li disegna: colore per punteggio, soglia (cutoff) e opacità applicate
// client-side (istantanee), bordi cella opzionali, hover → punteggio della cella.
const GridLayer = L.Layer.extend({
  initialize() { this._grid = null; this._cutoff = 0.3; this._showGrid = false; },

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
    map.off('moveend zoomend resize viewreset', this._reset, this);
    if (this._canvas) L.DomUtil.remove(this._canvas);
    this._canvas = null;
  },

  setGrid(g) { this._grid = g; this._reset(); },
  setCutoff(v) { this._cutoff = v; this._draw(); },
  setShowGrid(b) { this._showGrid = b; this._draw(); },
  setOpacity(v) { if (this._canvas) this._canvas.style.opacity = v; },

  // punteggio della cella sotto il cursore, o null (fuori bosco / sotto soglia)
  scoreAt(ll) {
    const g = this._grid; if (!g) return null;
    const [s, w, n, e] = g.bounds;
    if (ll.lat < s || ll.lat > n || ll.lng < w || ll.lng > e) return null;
    const j = Math.min(g.nx - 1, Math.floor((ll.lng - w) / (e - w) * g.nx));
    const i = Math.min(g.ny - 1, Math.floor((n - ll.lat) / (n - s) * g.ny));
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

    const map = this._map, [s, w, n, e] = g.bounds;
    const dLon = (e - w) / g.nx, dLat = (n - s) / g.ny;
    // Web Mercator: x dipende solo da lon, y solo da lat → una proiezione per bordo.
    const xs = new Float64Array(g.nx + 1), ys = new Float64Array(g.ny + 1);
    for (let j = 0; j <= g.nx; j++) xs[j] = map.latLngToContainerPoint([s, w + j * dLon]).x;
    for (let i = 0; i <= g.ny; i++) ys[i] = map.latLngToContainerPoint([n - i * dLat, w]).y;

    const thr = this._cutoff / g.score_max * 255;
    const W = c.width, H = c.height, grid = this._showGrid;
    ctx.strokeStyle = 'rgba(70,12,55,0.35)'; ctx.lineWidth = 0.5;

    for (let i = 0; i < g.ny; i++) {
      const y0 = ys[i], y1 = ys[i + 1];
      if (y1 < 0 || y0 > H) continue;
      const row = i * g.nx, dy = y1 - y0;
      for (let j = 0; j < g.nx; j++) {
        const v = g.data[row + j];
        if (v === 0 || v < thr) continue;
        const x0 = xs[j], x1 = xs[j + 1];
        if (x1 < 0 || x0 > W) continue;
        const light = 88 - (v / 255) * 48;             // fucsia: 88%→40% con il punteggio
        ctx.fillStyle = `hsl(320, 92%, ${light}%)`;
        ctx.fillRect(x0, y0, x1 - x0 + 0.6, dy + 0.6); // +0.6 evita cuciture subpixel
        if (grid && (x1 - x0) >= 3) ctx.strokeRect(x0 + 0.25, y0 + 0.25, x1 - x0, dy);
      }
    }
  }
});
const gridLayer = new GridLayer();

const cutoff = document.getElementById('cutoff'), cutVal = document.getElementById('cut-val');
const opacity = document.getElementById('opacity'), opVal = document.getElementById('op-val');
const gridToggle = document.getElementById('l-grid');

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
  gridLayer._showGrid = gridToggle.checked;
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
gridToggle.addEventListener('change', () => gridLayer.setShowGrid(gridToggle.checked));

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

// --- pronte oggi ----------------------------------------------------------- //
async function loadPronte() {
  if (pronteLayer) { map.removeLayer(pronteLayer); pronteLayer = null; }
  if (!document.getElementById('l-pronte').checked) return;
  const gj = await (await fetch(`/api/pronte/${sel.value}`)).json();
  pronteLayer = L.geoJSON(gj, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: 6, color: '#c33', weight: 1, fillColor: '#f55',
      fillOpacity: Math.min(1, 0.3 + f.properties.pred)
    }).bindPopup(`<b>pronta oggi</b><br>pred ${f.properties.pred}<br>idoneità ${f.properties.idoneita} × readiness ${f.properties.readiness}`)
  }).addTo(map);
  if (gj.features.length === 0) setStatus('nessuna cella "pronta oggi" per questa specie');
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
document.getElementById('l-pins').addEventListener('change', loadPins);
reloadAll();
