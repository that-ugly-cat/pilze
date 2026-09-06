// Form di log: pin trascinabile + geolocalizzazione + campi che compaiono col tipo.
// Il pin batte la posizione nativa di Telegram in un caso che conta: la sera, a casa,
// quando il posto lo ricordi ma non ci sei più.

const latEl = document.getElementById('lat');
const lonEl = document.getElementById('lon');
const coords = document.getElementById('coords');

const start = (latEl.value && lonEl.value)
  ? [parseFloat(latEl.value), parseFloat(lonEl.value)] : [46.1, 11.4];
const map = L.map('pin-map').setView(start, latEl.value ? 14 : 8);
L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  maxZoom: 17, attribution: '© OpenTopoMap, OpenStreetMap contributors'
}).addTo(map);

let pin = null;
function setPoint(lat, lon, zoom) {
  latEl.value = lat.toFixed(6);
  lonEl.value = lon.toFixed(6);
  coords.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  if (pin) { pin.setLatLng([lat, lon]); }
  else {
    pin = L.marker([lat, lon], { draggable: true }).addTo(map);
    pin.on('dragend', () => { const p = pin.getLatLng(); setPoint(p.lat, p.lng); });
  }
  if (zoom) map.setView([lat, lon], zoom);
}
if (latEl.value) setPoint(parseFloat(latEl.value), parseFloat(lonEl.value));
map.on('click', (e) => setPoint(e.latlng.lat, e.latlng.lng));

document.getElementById('geo').addEventListener('click', () => {
  if (!navigator.geolocation) { coords.textContent = 'geolocalizzazione non disponibile'; return; }
  coords.textContent = 'cerco la posizione…';
  navigator.geolocation.getCurrentPosition(
    (p) => setPoint(p.coords.latitude, p.coords.longitude, 15),
    (err) => { coords.textContent = 'posizione negata o non disponibile — usa la mappa'; console.warn(err); },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
});

// --- campi condizionali ---------------------------------------------------- //
const foundOnly = document.getElementById('found-only');
const fldEffort = document.getElementById('fld-effort');
const fldSpecies = document.getElementById('fld-species');
const fldOld = document.getElementById('fld-oldreason');

function applyKind() {
  const kind = document.querySelector('input[name=kind]:checked').value;
  foundOnly.classList.toggle('off', kind !== 'found');
  fldEffort.classList.toggle('off', kind === 'found');
  // il vuoto generico non ha specie; quello mirato sì (ed è il suo valore)
  fldSpecies.classList.toggle('off', kind === 'blank');
  fldSpecies.firstChild.textContent = kind === 'target' ? 'Specie che cercavi' : 'Specie';
}
document.querySelectorAll('input[name=kind]').forEach(r => r.addEventListener('change', applyKind));

function applyPhase() {
  fldOld.classList.toggle('off', document.querySelector('select[name=phase]').value !== 'vecchio');
}
document.querySelector('select[name=phase]').addEventListener('change', applyPhase);

applyKind();
applyPhase();

document.getElementById('logform').addEventListener('submit', (e) => {
  if (!latEl.value || !lonEl.value) {
    e.preventDefault();
    coords.textContent = 'manca la posizione: tocca la mappa o usa il bottone';
  }
});
