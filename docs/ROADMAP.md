# Roadmap — Mappa Funghi

Ordine di costruzione (spec §8). Stato: **MVP end-to-end** (18 lug 2026).

## v1 — subito, senza attesa di storico
- [x] **Motore generico** + profili delle 6 specie (scoring §7.5): membership sfumate,
      scorer statico (host-gate × media geometrica), scorer dinamico, combiner. *Testato.*
- [x] **Bot Telegram** di cattura (ritrovamenti + zeri + mirato + fase + foto) → SQLite. *Runnable con token.*
- [~] **Traccia A GIS** — acquisizione layer VE+TN → mappa idoneità statica (vedi `gis/README.md`).
      - [x] Ossatura: griglia (`grid.py`), GBIF (`occurrences.py`), Boyce (`boyce.py`), driver + `FeatureProvider`.
      - [x] **DEM** (`DEMProvider`, Copernicus GLO-30, `fetch_dem.py`): quota/pendenza/esposizione.
      - [x] **Forestale Veneto + Trentino** (`ForestProvider.veneto()/.trentino()`, `fetch_forest.py`): host via crosswalk.
            VE = Carta Tipi Forestali (38k poligoni); TN = SIGFAT (174k unità, copertura parziale piani di gestione).
            Boyce DEM+VE+TN: edulis +0.71 (↑ monotòno), pinophilus +0.87, cibarius +0.78 (`python -m gis.validate`).
      - [~] Suolo → soil_ph: due fonti. **SoilGrids** (`SoilProvider`) degrada (edulis +0.71→+0.62) → scartato (`--soil`).
            **CARG/substrato** (`GeologyProvider`, ArcGIS REST PAT) MIGLIORA (edulis +0.74→+0.80, cibarius +0.78→+0.88)
            anche col 25% di copertura → preferito (`--geology`). Da indurire: quaternario fallback, Veneto, bulk-download.
      - [x] **WorldCover** (`WorldCoverProvider`, `fetch_worldcover.py`): gate "è bosco?" completo → `forest_fraction` moltiplica l'idoneità (fuori-bosco = 0), senza bucare il TN.
      - [x] **Disturbo Sentinel-2 → canopy_alive** (`canopy.py`, `fetch_canopy.py`, `CanopyProvider`): "chioma viva oggi" agnostico, tarato su Paneveggio.
      - [x] **Mappa statica generata** (`make_map.py`, 500 m per specie, GeoTIFF + top-K GeoJSON).
      - [~] CFI2020 (MASAF, **mail inviata**) → chiude l'host-sconosciuto TN. Validazione background forestato + CV a blocchi (§6.3): TODO.
- [x] **Mappa a pin + overlay** — nella web app (`webapp/`), non più solo v1.

## v2 — pipeline meteo — FATTO
- [x] Client **ICON-D2 via Open-Meteo** (`meteo.py`, no key; `past_days` bypassa il cold-start in dev)
      + **poller + archivio SQLite** (`fetch_meteo.py`) + **gap-detector** (§9). Su VPS gira nel container `poller`.
- [x] Calcolo **feature** (§4): pioggia cumulata sulla finestra, umidità/temperatura suolo,
      **shock termico sul suolo** (§5), giorni-da-trigger → dict per `engine.dynamic_scorer.readiness`.

## v3 — predizione combinata — FATTO (base)
- [x] `predizione = statica × readiness` → celle **"pronte oggi"** per specie (`predict_today.py`).
      Verificato su dati reali (metà-luglio: estatino/finferlo sì, edulis no). Docker + deploy borant (`DEPLOY.md`).
- [ ] (opz.) Temperatura in quota (lapse rate locale, §5); ancoraggio pluviometrico ARPAV/Meteotrentino.
- [ ] **Notifiche via bot** (trigger: readiness che scatta) — active learning §6.3.

## v4 — apprendimento
- [ ] **Learner statico**: presenza+zeri → pesi statici, update **grossolano** (sposta il profilo,
      non i singoli fattori — credit assignment impossibile con poche decine di punti). Online/bayesiano.
- [ ] **Learner dinamico**: fase × meteo antecedente → soglie di trigger e **lag**.
- [ ] Metriche (§6.3): Boyce, precision@k, errore di lag; sempre come skill sopra baseline; CV a blocchi spaziali.
- [ ] **Active learning**: il sistema propone le celle a più alta probabilità oggi → vai, logghi, massimizzi l'informazione.

## ongoing
- [ ] Nuove specie via profili (§7). Nuove modalità trofiche: morchelle (ramo primaverile,
      `hydrography_distance`/`burn_areas`), *Coprinus* (logica invertita bosco↔prato).

---
**Assi di apprendimento SEPARATI** (§6.2): non mescolare le feature statiche di un ritrovamento
col meteo di quel giorno, o si cementa il bel tempo di un giorno fortunato nella suitability permanente.
