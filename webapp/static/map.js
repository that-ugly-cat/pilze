const map = L.map('map').setView([46.1, 11.4], 8);
L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  maxZoom: 17, attribution: '© OpenTopoMap, OpenStreetMap contributors'
}).addTo(map);

const sel = document.getElementById('species');
const status = document.getElementById('status');
let staticOverlay = null, pronteLayer = null, pinsLayer = null;

function setStatus(t) { status.textContent = t; }

async function loadStatic() {
  if (staticOverlay) { map.removeLayer(staticOverlay); staticOverlay = null; }
  if (!document.getElementById('l-static').checked) return;
  const sp = sel.value;
  const r = await fetch(`/api/suitability/${sp}.png`);
  if (!r.ok) { setStatus('nessuna mappa statica per questa specie'); return; }
  const bounds = JSON.parse(r.headers.get('X-Bounds'));
  const url = URL.createObjectURL(await r.blob());
  staticOverlay = L.imageOverlay(url, bounds, { opacity: 0.7 }).addTo(map);
  setStatus('');
}

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
