// Pin di casa: stessa meccanica del form di log, su campi diversi.
const latEl = document.getElementById('home_lat');
const lonEl = document.getElementById('home_lon');
const coords = document.getElementById('home-coords');

const has = latEl.value && lonEl.value;
const map = L.map('home-map').setView(
  has ? [parseFloat(latEl.value), parseFloat(lonEl.value)] : [46.1, 11.4], has ? 12 : 8);
L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  maxZoom: 17, attribution: '© OpenTopoMap, OpenStreetMap contributors'
}).addTo(map);

let pin = null;
function setHome(lat, lon, zoom) {
  latEl.value = lat.toFixed(6);
  lonEl.value = lon.toFixed(6);
  coords.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  if (pin) pin.setLatLng([lat, lon]);
  else {
    pin = L.marker([lat, lon], { draggable: true }).addTo(map);
    pin.on('dragend', () => { const p = pin.getLatLng(); setHome(p.lat, p.lng); });
  }
  if (zoom) map.setView([lat, lon], zoom);
}
if (has) setHome(parseFloat(latEl.value), parseFloat(lonEl.value));
map.on('click', (e) => setHome(e.latlng.lat, e.latlng.lng));

document.getElementById('home-geo').addEventListener('click', () => {
  if (!navigator.geolocation) { coords.textContent = 'geolocalizzazione non disponibile'; return; }
  coords.textContent = 'cerco la posizione…';
  navigator.geolocation.getCurrentPosition(
    (p) => setHome(p.coords.latitude, p.coords.longitude, 14),
    () => { coords.textContent = 'posizione negata o non disponibile — usa la mappa'; },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
});
