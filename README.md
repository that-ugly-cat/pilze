# Pilze 🍄

Sistema per **mappare le aree produttive** per specie di funghi, **prevedere le buttate**
(idoneità statica dell'habitat × condizioni meteo dinamiche) e **migliorare nel tempo**
tramite i ritrovamenti sul campo. Ambito: 6 specie micorriziche, Veneto + Trentino.

Spec completa: `../ono-wiki/raw/strumenti/mappa-funghi-spec.md`. Deploy: `DEPLOY.md`.

```
predizione(cella, specie, giorno) = idoneità_statica(cella, specie) × readiness_dinamica(meteo, specie)
```

Tutto è **per singola specie**: aggiungere una specie = aggiungere un profilo YAML in `profiles/`.

## Struttura
```
profiles/     6 profili di specie (YAML) — il cuore dichiarativo (§7.1)
config/       grid.yaml (griglia comune) · crosswalk.yaml (forestale + geologia VE/TN)
engine/       motore species-agnostic: membership, static_scorer, dynamic_scorer, combiner
gis/          layer + pipeline:
                fetch_dem/forest/geology/soil/worldcover/canopy   acquisizione layer
                providers.py   DEM · Forest(VE/TN) · WorldCover(gate) · Geology · Canopy
                occurrences.py (GBIF) · boyce.py · validate.py      validazione
                grid.py · make_map.py                                mappa statica
                meteo.py · fetch_meteo.py · predict_today.py         asse dinamico (meteo→pronte oggi)
bot/          bot Telegram di cattura + SQLite (§6.1)
webapp/       web app FastAPI + Leaflet (auth, admin, 3 layer)
tests/        23 test (motore + provider + gate)
Dockerfile · docker-compose.yml · DEPLOY.md
```

## Stato — MVP end-to-end
- **Statico (DOVE):** mappa idoneità 6 specie a 500 m da dati reali — DEM (Copernicus) + forestale
  VE+TN (host) + **WorldCover** (gate "è bosco?") + geologia CARG (soil_ph) + canopy Sentinel-2
  (disturbo Vaia/bostrico). Validata (Boyce vs GBIF: edulis +0.71).
- **Dinamico (QUANDO):** meteo **ICON-D2 via Open-Meteo** → feature §4 → readiness; poller + archivio
  SQLite + gap-detector; **predizione combinata** → celle "pronte oggi" per specie.
- **Cattura:** bot Telegram (ritrovamenti/zeri/foto). **Interfaccia:** web app (mappa topo, selezione
  specie, layer idoneità/pronte-oggi/pin). **Deploy:** Docker (web+bot+poller) → borant.
- **Da fare:** notifiche via bot (trigger readiness); learner (v4); CFI2020 per chiudere l'host TN;
  rifiniture (pesi/soglie, geologia nella mappa, tile canopy mancanti).

## Uso
```bash
python -m gis.make_map                 # genera le mappe statiche (richiede i layer grezzi, locali)
python -m gis.fetch_meteo <specie>     # backfill archivio meteo
python -m gis.predict_today <specie>   # celle "pronte oggi"
python -m gis.validate                 # Boyce vs GBIF
pytest                                 # test
# web app: uvicorn webapp.app:app   ·   bot: python -m bot.bot
```
I **layer grezzi** (DEM/forestale/geologia/WorldCover/canopy, ~1 GB) restano locali (gitignorati),
servono solo a *generare* le mappe. A runtime (VPS) servono solo le mappe (`data/maps/idoneita_*.tif`).

## Note di design (dalla spec)
- **Niente ML all'avvio**: idoneità = MCE a pesi esperti; i ritrovamenti aggiornano priori (bayesiano).
- **Assi di apprendimento separati** (§6.2); **trigger sulla temperatura del SUOLO** (§5).
- **host-sconosciuto ≠ assente**; il gate "è bosco?" (WorldCover, completo) è separato dal "che genere?" (forestale).
- `dynamic_triggers` oltre *aereus* = priori di prima passata **da rivedere**.
