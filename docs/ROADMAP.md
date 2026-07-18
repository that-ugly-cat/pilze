# Roadmap — Mappa Funghi

Ordine di costruzione (spec §8). Stato al kickoff (18 lug 2026).

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
      - [ ] Disturbo Sentinel-2 → canopy_alive (Peccete bellunesi) — CRITICO per edulis/pinophilus. **Ultimo layer statico.**
      - [ ] Opzione futura: CFI2020 nazionale (MASAF) → legenda unica VE+TN al posto del patchwork.
      - [ ] Validazione *dentro il Veneto* + background forestato + CV a blocchi spaziali (§6.3):
            isola il contributo dell'host, oggi confuso dalla copertura parziale.
- [ ] **Mappa a pin** con foto (Leaflet) dalle osservazioni + overlay idoneità statica.

## v2 — pipeline meteo (dopo il cold-start ~2–4 settimane)
- [ ] Poller **ICON-D2** (via Open-Meteo per partire) su [VPS], storage cubi.
      systemd timer + **gap-detector con alert** + poll ≥2×/giorno (una corsa persa = buco permanente).
- [ ] Calcolo **feature** (§4): pioggia cumulata 7/15/30 gg, umidità/temperatura suolo,
      shock termico (sul suolo), giorni-da-trigger, gradi-giorno.
      → produce il dict che `engine.dynamic_scorer.readiness` già consuma.

## v3 — predizione combinata
- [ ] Scorer dinamico in produzione + `predizione = statica × readiness` su tutte le celle/giorno.
- [ ] Temperatura in quota (§5): lapse rate empirico locale, non gradiente fisso.
- [ ] Ancoraggio pluviometrico opzionale (ARPAV/Meteotrentino) sui temporali convettivi.

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
