const map = L.map('map').setView([46.1, 11.4], 8);
window.pilzeMap = map;   // handle per debug in console
L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  maxZoom: 17, attribution: '© OpenTopoMap, OpenStreetMap contributors'
}).addTo(map);

// --- pannello collassabile + mobile ---------------------------------------- //
map.zoomControl.setPosition('topright');            // libera il top-left per il toggle
function togglePanel() {
  document.body.classList.toggle('nav-collapsed');
  setTimeout(() => map.invalidateSize(), 260);      // ridisegna dopo la transizione
}
// due modi di chiudere: il ☰ in alto e la maniglia a metà del bordo, che sul telefono
// cade sotto il pollice invece che nell'angolo opposto.
document.getElementById('panel-toggle').addEventListener('click', togglePanel);
document.getElementById('panel-edge').addEventListener('click', togglePanel);
if (window.innerWidth <= 700) document.body.classList.add('nav-collapsed');
const isTouch = window.matchMedia('(hover: none)').matches;   // niente hover (telefono) → tap

const sel = document.getElementById('species');
const status = document.getElementById('status');

// --- stato della vista: URL condiviso, poi ultima specie vista ------------- //
// L'URL vince sempre: chi apre un link condiviso deve vedere esattamente quel punto
// con quella configurazione, non le sue preferenze di ieri.
const params = new URLSearchParams(location.search);
const SPECIES_KEY = 'pilze.species';

function applyUrlState() {
  const setCheck = (id, key) => {
    if (params.has(key)) document.getElementById(id).checked = params.get(key) === '1';
  };
  if (params.has('sp') && [...sel.options].some(o => o.value === params.get('sp')))
    sel.value = params.get('sp');
  else {
    try {                                   // niente URL → l'ultima specie guardata
      const last = localStorage.getItem(SPECIES_KEY);
      if (last && [...sel.options].some(o => o.value === last)) sel.value = last;
    } catch (e) { /* storage negato: resta la prima della lista */ }
  }
  if (params.has('cut')) {
    cutoff.value = params.get('cut');
    cutVal.textContent = parseFloat(cutoff.value).toFixed(2);
    gridLayer._cutoff = parseFloat(cutoff.value);
  }
  if (params.has('op')) {
    opacity.value = params.get('op');
    opVal.textContent = Math.round(opacity.value * 100) + '%';
  }
  setCheck('l-static', 'st'); setCheck('l-pronte', 'dy');
  setCheck('l-pins', 'pi'); setCheck('l-aoi', 'ao');
  if (params.has('lat') && params.has('lon')) {
    const ll = L.latLng(parseFloat(params.get('lat')), parseFloat(params.get('lon')));
    map.setView(ll, params.has('z') ? parseInt(params.get('z')) : 14);
    return ll;                              // punto condiviso: lo si marca dopo il load
  }
  return null;
}
let pronteLayer = null, pinsLayer = null, topLayer = null, aoiLayer = null;

function setStatus(t) { status.textContent = t; }

// Toast: per le cose che vanno dette adesso e altrove (la riga di stato nel pannello
// non si vede col pannello chiuso, che a mobile e' quasi sempre).
let toastTimer = null;
function toast(msg, ms = 5000) {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.innerHTML = msg;
  el.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), ms);
}

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

// --- tooltip UNICO ------------------------------------------------------- //
// Prima l'idoneità statica e la fase dinamica avevano ognuna il proprio tooltip, e dove
// i due layer si sovrappongono ne comparivano due. Ora ne esiste uno solo, che raccoglie
// quello che c'è sotto il puntatore. Su mobile lo apre il tap, con lo stesso contenuto:
// prima il tap mostrava solo la fase, e l'idoneità del punto non si riusciva a leggere.
const tip = L.tooltip({ className: 'grid-tip', direction: 'top', offset: [0, -2], opacity: 0.95 });
function hideTip() { if (tip._map) map.removeLayer(tip); }

function describeAt(ll) {
  const parts = [];
  if (document.getElementById('l-static').checked) {
    const sc = gridLayer.scoreAt(ll);
    if (sc != null) parts.push(`idoneità <b>${sc.toFixed(2)}</b>`);
  }
  if (document.getElementById('l-pronte').checked) {
    const p = pronteAt(ll);
    if (p) {
      let s = `<b class="dyn">${STATE_LABEL[p.state] || p.state}</b> · readiness ${p.readiness}`;
      if (p.eta != null) s += ` · fra ~${p.eta} gg`;
      if (p.days_past != null) s += ` · ~${p.days_past} gg fa`;
      parts.push(s);
    }
  }
  return parts.length ? parts.join('<br>') : null;
}

function showTipAt(ll) {
  const html = describeAt(ll);
  if (!html) return hideTip();
  tip.setLatLng(ll).setContent(html);
  if (!tip._map) tip.addTo(map);
}

map.on('click', (ev) => {
  if (sharing) return doShare(ev.latlng);
  if (isTouch) showTipAt(ev.latlng);
});
if (!isTouch) {
  map.on('mousemove', (ev) => { if (!sharing) showTipAt(ev.latlng); });
  map.on('mouseout', hideTip);
}

// --- pronte oggi: fase della buttata per cella meteo (quadrati 2.2km) ------- //
const STATE_COLOR = { in_fieri: '#8ecae6', pronto: '#0077cc', tardi: '#2a3a5c' };
const STATE_LABEL = { in_fieri: 'in fieri', pronto: 'pronto', tardi: 'tardi' };
map.createPane('pronte');
map.getPane('pronte').style.zIndex = 450;                 // sopra il fucsia, sotto i pin
const pronteRenderer = L.canvas({ pane: 'pronte' });
map.createPane('topspots');
map.getPane('topspots').style.zIndex = 620;               // spot oro sopra TUTTI i layer
const topRenderer = L.canvas({ pane: 'topspots' });
let pronteReq = 0;
let pronteData = [];        // le celle in stato, per il tooltip unico

// Cella sotto il punto: ray casting sull'anello del quadrato (in lon/lat è un
// quadrilatero lievemente deformato, quindi il bounding box non basterebbe ai bordi).
function pronteAt(ll) {
  for (const f of pronteData) {
    const ring = f.geometry.coordinates[0];
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > ll.lat) !== (yj > ll.lat) &&
          ll.lng < (xj - xi) * (ll.lat - yi) / (yj - yi) + xi) inside = !inside;
    }
    if (inside) return f.properties;
  }
  return null;
}

function applyPronteOpacity() {
  map.getPane('pronte').style.opacity = document.getElementById('pronte-op').value;
}

async function loadPronte() {
  const myReq = ++pronteReq;
  if (pronteLayer) { map.removeLayer(pronteLayer); pronteLayer = null; }
  const on = document.getElementById('l-pronte').checked;
  document.getElementById('pronte-ctl').classList.toggle('off', !on);
  pronteData = [];
  if (!on) return;
  const gj = await (await fetch(`/api/pronte/${sel.value}`)).json();
  if (myReq !== pronteReq) return;            // superata da una chiamata più recente (no layer doppi)
  pronteData = gj.features;
  if (!gj.features.length) { setStatus('nessuna cella in stato per questa specie'); return; }
  setStatus('');
  pronteLayer = L.geoJSON(gj, {
    pane: 'pronte', renderer: pronteRenderer,
    // interactive:false — i quadrati non intercettano più il mouse: il tooltip unico
    // legge la cella da pronteAt(), e i click arrivano alla mappa e ai pin sotto.
    interactive: false,
    style: f => ({ stroke: false, fillColor: STATE_COLOR[f.properties.state] || '#888', fillOpacity: 1 })
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
      // Un fungo per i ritrovamenti, e i due vuoti distinti fra loro: un vuoto mirato
      // dice molto di più di una passeggiata, e sulla mappa deve vedersi.
      const glyph = p.is_blank ? (p.target ? '🎯' : '🚫') : '🍄';
      const icon = L.divIcon({
        className: '', iconSize: [24, 24], iconAnchor: [12, 12], popupAnchor: [0, -10],
        html: `<span class="obs-pin${p.is_blank ? ' blank' : ''}">${glyph}</span>`
      });
      const m = L.marker(ll, { icon });
      let html = p.is_blank ? '<b>uscita a vuoto</b>' : `<b>${p.species || '?'}</b>`;
      if (p.is_blank && p.target) html += `<br>cercavo: ${p.target}`;
      if (p.phase) html += `<br>fase: ${p.phase}`;
      if (p.weight_g) html += `<br>${p.weight_g} g`;
      if (p.abundance) html += `<br>${p.abundance}`;
      if (p.effort_min) html += `<br>ricerca: ~${p.effort_min} min`;
      html += `<br><small>${p.ts || ''}</small>`;
      if (p.photo) html += `<br><img src="/photo/${p.id}" style="max-width:180px;margin-top:4px;border-radius:4px">`;
      return m.bindPopup(html);
    }
  }).addTo(map);
}

// --- confini area dati (BZ + TN + VE) -------------------------------------- //
// Fuori da qui forestale e geologia non arrivano: le mappe si fermano al confine,
// quindi il contorno spiega il bordo dell'idoneità invece di lasciarlo misterioso.
map.createPane('aoi');
map.getPane('aoi').style.zIndex = 410;                    // sopra il topo, sotto i dati

async function loadAoi() {
  const on = document.getElementById('l-aoi').checked;
  if (aoiLayer) { map.removeLayer(aoiLayer); aoiLayer = null; }
  if (!on) return;
  const gj = await (await fetch('/api/aoi')).json();
  if (!gj.features.length) { setStatus('confini non disponibili'); return; }
  // Doppia linea: alone chiaro sotto, tratteggio scuro sopra. Sul topografico (marroni
  // di rilievo, verdi di pianura, gialli di strada) una linea sola sparisce sempre.
  aoiLayer = L.layerGroup([
    L.geoJSON(gj, { pane: 'aoi', interactive: false,
      style: { color: '#fff', weight: 5, opacity: 0.75, fill: false } }),
    L.geoJSON(gj, { pane: 'aoi', interactive: false,
      style: { color: '#1c2b36', weight: 2, dashArray: '7 4', fill: false } })
  ]).addTo(map);
}

// --- vicino a me: raggio da casa ------------------------------------------ //
// Il punto di casa sta sull'account, non nel browser: il client manda solo "near=1" e
// il raggio, il server sa da dove misurare.
const near = document.getElementById('l-near');
const nearK = document.getElementById('near-k'), nearKm = document.getElementById('near-km');

function applyNear() {
  if (near.checked && near.dataset.home !== '1') {
    near.checked = false;
    toast('Per cercare vicino a te serve il punto di casa: impostalo nella '
          + '<a href="/me">tua scheda</a>.');
    return;
  }
  document.getElementById('near-ctl').classList.toggle('off', !near.checked);
  document.getElementById('find-hint').textContent = near.checked
    ? `I migliori ${nearK.value} spot entro ${nearKm.value} km da casa.`
    : 'Top 50 per la specie: statica, dinamica o entrambe secondo i layer attivi.';
  clearTopSpots();
}
near.addEventListener('change', applyNear);
for (const el of [nearK, nearKm]) {
  el.addEventListener('input', () => {
    document.getElementById('near-k-val').textContent = nearK.value;
    document.getElementById('near-km-val').textContent = nearKm.value + ' km';
    if (near.checked) document.getElementById('find-hint').textContent =
      `I migliori ${nearK.value} spot entro ${nearKm.value} km da casa.`;
  });
}

// --- trova spot migliori --------------------------------------------------- //
function clearTopSpots() {
  if (topLayer) { map.removeLayer(topLayer); topLayer = null; }
  document.getElementById('find-spots').textContent = '★ Trova spot migliori';
}
async function findTopSpots() {
  if (topLayer) { clearTopSpots(); setStatus(''); return; }        // toggle: nascondi
  const s = document.getElementById('l-static').checked, d = document.getElementById('l-pronte').checked;
  const mode = (s && d) ? 'both' : s ? 'static' : d ? 'dynamic' : null;
  if (!mode) { setStatus('attiva idoneità statica o dinamica per cercare gli spot'); return; }
  setStatus('cerco gli spot migliori…');
  const q = near.checked ? `&near=1&k=${nearK.value}&km=${nearKm.value}` : '';
  const gj = await (await fetch(`/api/top/${sel.value}?mode=${mode}${q}`)).json();
  if (gj.error === 'home_missing') {
    setStatus('');
    toast('Per cercare vicino a te serve il punto di casa: impostalo nella '
          + '<a href="/me">tua scheda</a>.');
    return;
  }
  if (!gj.features.length) {
    setStatus('');
    toast(near.checked
      ? `Nessuno spot entro ${nearKm.value} km da casa per questa specie.`
      : "Nessuno spot (dinamica: manca l'archivio meteo?)");
    return;
  }
  topLayer = L.geoJSON(gj, {
    pane: 'topspots',
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      renderer: topRenderer, pane: 'topspots',
      radius: 7, color: '#8a5b00', weight: 2, fillColor: '#ffd400', fillOpacity: 0.95
    }).bindPopup(`<b>spot</b> · score ${f.properties.score}`
      + (f.properties.dist_km != null ? ` · ${f.properties.dist_km} km da casa` : '')
      + `<br>idoneità ${f.properties.idoneita} · readiness ${f.properties.readiness}`)
  }).addTo(map);
  const label = { static: 'statica', dynamic: 'dinamica', both: 'statica × dinamica' }[mode];
  setStatus(`${gj.features.length} spot (${label})`
            + (near.checked ? ` entro ${nearKm.value} km` : ''));
  document.getElementById('find-spots').textContent = '✕ Nascondi spot';
}
document.getElementById('find-spots').addEventListener('click', findTopSpots);

// --- condividi un punto ---------------------------------------------------- //
// Il link porta punto, zoom, specie, soglie e layer accesi: chi lo apre vede la stessa
// identica cosa. Non serve renderlo segreto — tutta l'app sta dietro il login, e chi non
// ce l'ha viene mandato ad accedere e poi ributtato qui, parametri compresi.
const shareBtn = document.getElementById('share');
let sharing = false;
let sharedPin = null;

function shareUrl(ll) {
  const p = new URLSearchParams({
    lat: ll.lat.toFixed(5), lon: ll.lng.toFixed(5), z: map.getZoom(),
    sp: sel.value, cut: parseFloat(cutoff.value).toFixed(2),
    op: parseFloat(opacity.value).toFixed(2),
    st: document.getElementById('l-static').checked ? 1 : 0,
    dy: document.getElementById('l-pronte').checked ? 1 : 0,
    pi: document.getElementById('l-pins').checked ? 1 : 0,
    ao: document.getElementById('l-aoi').checked ? 1 : 0,
  });
  return `${location.origin}/?${p}`;
}

function setSharing(on) {
  sharing = on;
  shareBtn.classList.toggle('armed', on);
  shareBtn.textContent = on ? '✕ annulla condivisione' : '🔗 Condividi un punto';
  map.getContainer().style.cursor = on ? 'crosshair' : '';
  if (on) setStatus('tocca il punto da condividere');
  else if (status.textContent === 'tocca il punto da condividere') setStatus('');
}
shareBtn.addEventListener('click', () => setSharing(!sharing));

async function doShare(ll) {
  setSharing(false);
  const url = shareUrl(ll);
  markShared(ll);
  let copiato = false;
  try { await navigator.clipboard.writeText(url); copiato = true; } catch (e) { /* niente permesso */ }
  toast((copiato ? '🔗 Link copiato: ' : '🔗 Link (copialo): ')
        + `<a href="${url}">${url.replace(location.origin, '')}</a>`, 12000);
}

function markShared(ll) {
  if (sharedPin) map.removeLayer(sharedPin);
  sharedPin = L.marker(ll, { icon: L.divIcon({
    className: '', iconSize: [26, 26], iconAnchor: [13, 26], popupAnchor: [0, -24],
    html: '<span class="obs-pin shared">📌</span>' }) }).addTo(map);
  sharedPin.bindPopup('<b>punto condiviso</b>').openPopup();
}

function reloadAll() { loadStatic(); loadPronte(); loadPins(); loadAoi(); }
sel.addEventListener('change', () => {
  try { localStorage.setItem(SPECIES_KEY, sel.value); } catch (e) { /* storage negato */ }
  loadStatic(); loadPronte(); clearTopSpots();
});
document.getElementById('l-static').addEventListener('change', loadStatic);
document.getElementById('l-pronte').addEventListener('change', loadPronte);
document.getElementById('pronte-op').addEventListener('input', () => {
  document.getElementById('pronte-op-val').textContent = Math.round(document.getElementById('pronte-op').value * 100) + '%';
  applyPronteOpacity();
});
document.getElementById('l-pins').addEventListener('change', loadPins);
document.getElementById('l-aoi').addEventListener('change', loadAoi);
const sharedPoint = applyUrlState();
reloadAll();
if (sharedPoint) markShared(sharedPoint);
