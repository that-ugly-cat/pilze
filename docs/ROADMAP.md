# Roadmap — Pilze

Ordine di costruzione (spec §8). Stato: **MVP end-to-end**, ritagliato sull'AOI
(agg. 6 set 2026). Per come funziona il modello e da dove vengono i dati:
[COME-FUNZIONA.md](COME-FUNZIONA.md).

## v1 — subito, senza attesa di storico
- [x] **Motore generico** + profili delle 6 specie (scoring §7.5): membership sfumate,
      scorer statico (host-gate × media geometrica), scorer dinamico, combiner. *Testato.*
- [x] **Cattura** (ritrovamenti + zeri + mirato + fase + foto + giorno dell'uscita) → SQLite.
      Era un bot Telegram; dal 6 set 2026 è il form `/log` della web app, più la scheda `/me`
      (casa, password, storico modificabile). Il servizio `bot` è fuori dal compose.
- [~] **Traccia A GIS** — acquisizione layer VE+TN → mappa idoneità statica (vedi `gis/README.md`).
      - [x] Ossatura: griglia (`grid.py`), GBIF (`occurrences.py`), Boyce (`boyce.py`), driver + `FeatureProvider`.
      - [x] **DEM** (`DEMProvider`, Copernicus GLO-30, `fetch_dem.py`): quota/pendenza/esposizione.
      - [x] **Forestale Veneto + Trentino** (`ForestProvider.veneto()/.trentino()`, `fetch_forest.py`): host via crosswalk.
            VE = Carta Tipi Forestali (38k poligoni); TN = SIGFAT (174k unità, copertura parziale piani di gestione).
            Boyce DEM+VE+TN (lug 2026): edulis +0.71, pinophilus +0.87, cibarius +0.78.
            **Superati**: misurati senza ritaglio AOI e su un altro set GBIF, quindi non
            confrontabili coi numeri di oggi (vedi README). `python -m gis.validate`.
      - [~] Suolo → soil_ph: due fonti. **SoilGrids** (`SoilProvider`) degrada (edulis +0.71→+0.62) → scartato (`--soil`).
            **CARG/substrato** (`GeologyProvider`, ArcGIS REST PAT) MIGLIORA (edulis +0.74→+0.80, cibarius +0.78→+0.88)
            anche col 25% di copertura → preferito (`--geology`). Da indurire: quaternario fallback, Veneto, bulk-download.
      - [x] **WorldCover** (`WorldCoverProvider`, `fetch_worldcover.py`): gate "è bosco?" completo → `forest_fraction` moltiplica l'idoneità (fuori-bosco = 0), senza bucare il TN.
      - [x] **Disturbo Sentinel-2 → canopy_alive** (`canopy.py`, `fetch_canopy.py`, `CanopyProvider`): "chioma viva oggi" agnostico, tarato su Paneveggio.
      - [x] **Mappa statica generata** (`make_map.py`, 500 m per specie, GeoTIFF + top-K GeoJSON).
      - [x] **CFI2020** (MASAF, `ForestProvider.cfi()`, campo Ct_CFI): forestale genere **completo** VE+Trento+Bolzano
            → sostituisce il patchwork VE/TN, chiude l'host-sconosciuto TN (copertura bosco **39%→84%**). Legenda unica.
      - [ ] Validazione background forestato + CV a blocchi (§6.3): TODO.
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
- [ ] **Notifiche** al trigger di readiness — active learning §6.3. Con l'uscita del bot il
      canale di consegna è scoperto: serve un mittente Telegram in sola uscita (il token
      resta in `.env`) oppure web push dalla web app.

## Ritaglio e taratura (6 set 2026)
- [x] **AOI** (`fetch_boundaries.py`, `AOIProvider`): le mappe si fermano dove finiscono i layer
      tematici (Bolzano, Trento, Veneto). Fuori, host sconosciuto = neutro promuoveva il
      fuori-copertura: −47% di celle sopra 0.4, e il Boyce si ri-taglia di conseguenza.
- [x] **`gis/replay.py`**: falsificazione dell'asse dinamico senza etichette, rigirando lo scorer
      su ogni giorno passato dell'archivio. Col profilo di luglio: 0.34% di celle-giorno "pronto".
- [x] **Prima verità di campo** + ritaratura di `boletus_edulis` (README).
- [ ] Tarare gli altri profili con lo stesso metodo; il replay c'è, i dati di campo no.

## Taratura e metrologia (6-7 set 2026) — vedi README e COME-FUNZIONA
- [x] Prime **cinque osservazioni di campo** (Spit e Angela) → tarati porcino, finferlo,
      porcino rosso e ovolo; esposizione ammorbidita nel motore (0.1 → 0.35), confermata
      dal Boyce su GBIF su 5 specie su 6.
- [x] **Coerenza finestra/lag nel validatore dei profili**: `rain_window_days` deve
      superare `lag_days.opt` max, altrimenti l'innesco esce dalla finestra proprio quando
      la specie sarebbe pronta. Ha trovato quattro profili sbagliati.
- [x] **Intorno di 250 m in validazione** (default): un punto GBIF non ha la precisione di
      una cella da 200 m. Due specie cambiano segno.
- [ ] **La carica va valutata alla data dell'innesco**, non a oggi: oggi carica e lag
      guardano tempi diversi e si escludono, ed è il difetto strutturale dell'asse
      dinamico. Non è un parametro, è la forma della domanda.
- [ ] Background forestato + CV a blocchi spaziali (§6.3): resta il limite noto del Boyce.

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
