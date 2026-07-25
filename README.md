# Pilze 🍄

Sistema per **mappare le aree produttive** per specie di funghi, **prevedere le buttate**
(idoneità statica dell'habitat × condizioni meteo dinamiche) e **migliorare nel tempo**
tramite i ritrovamenti sul campo. Ambito: Veneto + Trentino. 6 profili di bosco
(micorriziche); il motore supporta anche i **saprotrofi di prato** (gate habitat).

Spec completa: `../ono-wiki/raw/strumenti/mappa-funghi-spec.md`. Deploy: `DEPLOY.md`.

```
predizione(cella, specie, giorno) = idoneità_statica(cella, specie) × readiness_dinamica(meteo, specie)
```

Tutto è **per singola specie**: aggiungere una specie = aggiungere un profilo YAML in `profiles/`
(o crearlo dall'editor web). Doc dei campi: pagina **Doc** nell'admin (`webapp/templates/docs.html`).

## Struttura
```
profiles/     6 profili di bosco (YAML) — il cuore dichiarativo (§7.1)
config/       grid.yaml (griglia comune, passo 200 m) · crosswalk.yaml (Ct_CFI → 20 classi host)
engine/       motore species-agnostic: membership · static_scorer (gate host + habitat) ·
                dynamic_scorer (readiness + fasi) · combiner · profiles
gis/          layer + pipeline:
                fetch_dem/forest/geology/geology_bz/worldcover/canopy   acquisizione layer grezzi
                providers.py   DEM · Forest(CFI2020 VE+TN+BZ) · WorldCover(gate forest+grassland) ·
                               Geology(soil_ph) · Canopy(chioma viva)
                occurrences.py (GBIF) · boyce.py · validate.py          validazione
                grid.py · make_map.py                                    mappa statica (200 m)
                meteo.py · fetch_meteo.py · predict_today.py             asse dinamico + top_spots
bot/          bot Telegram di cattura + SQLite (§6.1)
webapp/       web app FastAPI + Leaflet: auth · admin (utenti · editor profili + rigenerazione ·
                doc) · mappa (idoneità statica/dinamica/ritrovamenti · trova-spot · mobile)
tests/        23 test (motore + provider + gate)
Dockerfile · docker-compose.yml · DEPLOY.md
```

## Stato — MVP end-to-end
- **Statico (DOVE):** mappa idoneità a **200 m** da dati reali — DEM (Copernicus) + forestale
  **CFI2020** (VE+Trento+Bolzano) + **WorldCover** (gate copertura) + geologia CARG (soil_ph) +
  canopy Sentinel-2 (disturbo Vaia/bostrico). Host = **20 classi = i tipi forestali CFI**
  (pecceta, faggeta, mugheta…), non 8 generi. Gate **habitat** per-specie: bosco
  (`forest_fraction`) o prato (`grassland_fraction`) → supporta anche i saprotrofi di prato.
  Validata (Boyce vs GBIF: edulis +0.71).
- **Dinamico (QUANDO):** meteo **ICON-D2 via Open-Meteo** (batching multi-località) → feature §4 →
  readiness; poller notturno + archivio SQLite (**backfill incrementale** + gap-detector). La fase
  della buttata per cella meteo: **in fieri / pronto / tardi** (da days_since_trigger vs lag_days).
- **Interfaccia:** web app — mappa topo con **idoneità statica** (fucsia), **idoneità dinamica**
  (quadrati per fase), **ritrovamenti**, e **"trova spot migliori"** (top-50 per specie: statica /
  dinamica / prodotto, secondo i layer attivi). Editor profili online + rigenerazione mappe
  on-demand, pagina Doc, mobile (tooltip al tap). **Cattura:** bot Telegram (ritrovamenti/zeri/foto).
  **Deploy:** Docker (web+bot+poller) → borant.
- **Da fare:** notifiche via bot (trigger readiness); learner (v4); **CORINE Land Cover** (sottotipi
  di prato/pascolo) + cablaggio hook `extra_static_layers`; saprotrofi del legno (chiodini, canopy
  invertito); profili di specie di prato.

## Uso
```bash
python -m gis.make_map                 # genera le mappe statiche a 200 m (richiede i layer grezzi, locali)
python -m gis.fetch_meteo              # backfill/poll archivio meteo, tutte le specie (incrementale)
python -m gis.predict_today            # fasi "idoneità dinamica" per specie → GeoJSON
python -m gis.validate                 # Boyce vs GBIF
pytest                                 # test
# web app: uvicorn webapp.app:app   ·   bot: python -m bot.bot
```
I **layer grezzi** (DEM/forestale/geologia/WorldCover/canopy, ~1.7 GB) sono gitignorati e servono
solo a *generare* le mappe (`make_map`, ora eseguibile anche sul VPS via il bottone Rigenera).
A runtime servono: le mappe (`data/maps/idoneita_*.tif`), l'archivio meteo (`data/meteo.db`,
costruito dal poller) e i profili vivi (`data/profiles/`, editabili online — fonte di verità sul VPS).

## Note di design (dalla spec)
- **Niente ML all'avvio**: idoneità = MCE a pesi esperti; i ritrovamenti aggiornano priori (bayesiano).
- **Assi di apprendimento separati** (§6.2); **trigger sulla temperatura del SUOLO** (§5).
- **host-sconosciuto ≠ assente**; il gate **habitat** (WorldCover, completo — bosco o prato) è separato
  dal "che tipo di bosco?" (forestale CFI). I saprotrofi saltano il gate host.
- `dynamic_triggers` oltre *aereus* = priori di prima passata **da rivedere**.
