# Traccia A — mappa di idoneità statica (spec §3, §7.5, §8)

Questo modulo costruisce, per ogni cella della griglia comune, il **dict di feature
statiche** che `engine.static_scorer.static_suitability` consuma:

```python
cell = {
    "host": {"querce": 0.6, "castagno": 0.4},  # o "host_class": "querce"
    "canopy_alive": 0.9,      # 1 - frazione chioma morta (Vaia/bostrico)
    "elevation_m": 650, "slope_deg": 15,
    "aspect": "warm", "soil_ph": "acidic", "drainage": "well_drained",
}
```

Il **motore e lo scoring sono già scritti e testati** (feature sintetiche). Manca
l'acquisizione dei layer reali. Ordine di lavoro:

## 1. Griglia comune (`config/grid.yaml`) — PRIMA di tutto
CRS `EPSG:32632` (UTM 32N, metrico, copre VE+TN), passo statico 100 m, maglia meteo 2.2 km.
Tutti i layer si riproiettano qui.

## 2. DEM → quota / esposizione / pendenza (spec §3.1)
- **Veneto:** IDT — DTM 5 m (downloader regione.veneto.it). Esposizione/pendenza le derivi tu dal DTM.
- **Trentino:** SIAT/Portale Geocartografico — LiDAR PAT; isoquote/pendenze/esposizioni **già pronte**.
- Riduci l'esposizione a classe di calore `warm|cool|neutral` (S/SO=warm, N/NE=cool).

## 3. Copertura / specie arboree ospiti (il layer che più discrimina, §3.1)
- **Veneto:** Carta Regionale Categorie Forestali (shapefile per provincia).
- **Trentino:** Tipi forestali del Trentino (Odasso) + Carta uso del suolo (CORINE).
- Applica il **crosswalk** (`config/crosswalk.yaml`) → `genere_ospite_comune`. **Da riempire.**

## 4. Disturbo Vaia + bostrico → `canopy_alive` (CRITICO per edulis/pinophilus, §3.1)
- Meglio di una maschera statica: **NDVI/NBR da Sentinel-2** aggiornato periodicamente.
  Declassa gli ospiti conifera dove la chioma è morta. `canopy_alive ∈ [0,1]`.

## 5. Suolo / substrato → `soil_ph` (§3.1)
- **Trentino:** pedologia (Bruni Acidi/Calcarei, Podzol, Rendzine) = asse acido/calcareo diretto.
- **Veneto:** Carta dei suoli ARPAV; geologia CARG ISPRA.
- NB (§2): l'asse acido/calcareo **non** separa le specie in due gruppi — quasi tutte acidofile.

## 6. Validazione (spec §6.3) — senza storico proprio
Punti di presenza **GBIF / iNaturalist** via API → **Continuous Boyce Index** (SDM presence-only).
Testa l'idoneità dell'habitat, non il timing.

## Stack (§9)
`rasterio`, `geopandas`, `xarray`; storage PostGIS o SQLite+SpatiaLite.
Installabile con l'extra: `pip install -e ".[gis]"`.
