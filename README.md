# Pilze 🍄

Sistema per **mappare le aree produttive** per specie di funghi, **prevedere le buttate**
(idoneità statica dell'habitat × condizioni meteo dinamiche) e **migliorare nel tempo**
tramite i ritrovamenti sul campo. Ambito: **Bolzano + Trento + Veneto**, cioè dove
arrivano i layer tematici. 9 profili versionati: sette micorriziche di bosco e due
saprotrofe di **ecotono**. Dal 7 set 2026 git e volume del VPS coincidono — due profili
vivevano solo nel volume, quindi nessun `validate` li aveva mai visti, e uno dei due era
anti-predittivo senza che nessuno potesse accorgersene.

**Come funziona, e da dove vengono i dati: [docs/COME-FUNZIONA.md](docs/COME-FUNZIONA.md).**
Spec completa: `../ono-wiki/raw/strumenti/mappa-funghi-spec.md`. Deploy: `DEPLOY.md`.
Piano di lavoro: `docs/ROADMAP.md`.

```
predizione(cella, specie, giorno) = idoneità_statica(cella, specie) × readiness_dinamica(meteo, specie)
```

Tutto è **per singola specie**: aggiungere una specie = aggiungere un profilo YAML in `profiles/`
(o crearlo dall'editor web). Doc dei campi: pagina **Doc** nell'admin (`webapp/templates/docs.html`).

## Struttura
```
profiles/     9 profili (YAML) — il cuore dichiarativo (§7.1)
config/       grid.yaml (griglia comune, passo 200 m) · crosswalk.yaml (Ct_CFI → 20 classi host)
engine/       motore species-agnostic: membership · static_scorer (gate host + habitat) ·
                dynamic_scorer (readiness + fasi) · combiner · profiles
gis/          layer + pipeline:
                fetch_dem/forest/geology/geology_bz/worldcover/canopy   acquisizione layer grezzi
                fetch_boundaries.py                                     AOI: confini ISTAT BZ+TN+VE
                make_tpi.py                                             drenaggio: posizione topografica dal DEM
                providers.py   AOI(area con dati tematici) · DEM(+drenaggio) · Forest(CFI2020 VE+TN+BZ) ·
                               WorldCover(gate a pesi sulle 10 classi + edge_density) ·
                               Geology(soil_ph) · Canopy(chioma viva)
                occurrences.py (GBIF) · boyce.py · validate.py          validazione
                grid.py · make_map.py                                    mappa statica (200 m)
                meteo.py · fetch_meteo.py · predict_today.py             asse dinamico + top_spots
                replay.py                                                falsificazione dell'asse dinamico
bot/          persistenza osservazioni (SQLite, §6.1). Nome storico: la cattura era un
                bot Telegram, ora è il form della web app
webapp/       web app FastAPI + Leaflet: auth · admin (utenti · editor profili + rigenerazione ·
                doc) · mappa (idoneità statica/dinamica/ritrovamenti · trova-spot · mobile) ·
                /log (cattura) · /me (scheda: casa, password, storico modificabile)
tests/        40 test (motore · provider · gate a pesi · pavimento host · bordo · TPI ·
                AOI · intorno · account · osservazioni)
Dockerfile · docker-compose.yml · DEPLOY.md
```

## Stato — MVP end-to-end
- **Statico (DOVE):** mappa idoneità a **200 m** da dati reali — DEM (Copernicus) + forestale
  **CFI2020** (VE+Trento+Bolzano) + **WorldCover** (gate copertura) + geologia CARG (soil_ph) +
  canopy Sentinel-2 (disturbo Vaia/bostrico) + **drenaggio** dalla posizione topografica
  (`make_tpi`, dal DEM stesso). Host = **20 classi = i tipi forestali CFI**
  (pecceta, faggeta, mugheta…), non 8 generi, con `host_floor` per specie a decidere quanto
  vale l'ospite sbagliato. Gate **habitat** a pesi sulle 10 classi WorldCover — una classe
  sola, o una combinazione per le specie di ecotono — più `edge_density` (quota di confine
  bosco/prato) come fattore opt-in.
  Ritagliata sull'**AOI** (BZ+TN+VE): fuori i layer tematici non arrivano.
  Validazione (Boyce vs GBIF, 7 set 2026 sera, dentro l'AOI, background 5.000,
  **intorno 250 m**): procera +0.98, muscaria +0.86, edulis +0.79, coprinus +0.76,
  aestivalis +0.65, cibarius +0.65, caesarea +0.45, pinophilus +0.16 (aereus +0.85 ma con
  4 punti, cioè rumore). Sul **pixel esatto** gli stessi profili danno numeri molto più
  bassi, e il divario fra le due colonne è informativo di suo: dice quanto il modello
  sbaglia di **posto** invece che di specie. Due valori sono confrontabili solo a parità di
  background **e** di colonna — e nessun numero anteriore al 7 set 2026 lo è, perché il
  drenaggio e i gate sono cambiati. Dettaglio in `docs/COME-FUNZIONA.md`.
- **Dinamico (QUANDO):** meteo **ICON-D2 via Open-Meteo** (batching multi-località) → feature §4 →
  readiness; poller notturno + archivio SQLite (**backfill incrementale** + gap-detector). La fase
  della buttata per cella meteo: **in fieri / pronto / tardi** (da days_since_trigger vs lag_days).
- **Interfaccia:** web app — mappa topo con **idoneità statica** (fucsia), **idoneità dinamica**
  (quadrati per fase), **ritrovamenti**, **confini area dati** (BZ+TN+VE, spiega dove si ferma
  l'idoneità), e **"trova spot migliori"** (top-50 per specie: statica /
  dinamica / prodotto, secondo i layer attivi; con **"vicino a me"** i migliori N entro
  un raggio dal punto di casa, filtrando *prima* della classifica) e **"condividi un
  punto"** (link che porta punto, zoom, specie, soglie e layer accesi: chi lo apre vede
  la stessa identica cosa, dopo il login). Editor profili online + rigenerazione mappe
  on-demand, pagina Doc, mobile (tooltip al tap). **Cattura:** form `/log` — pin su mappa
  o GPS del telefono, ritrovamenti/vuoti/foto. **Deploy:** Docker (web+poller) → borant.
- **Da fare:** **taratura host/gate** — dentro l'AOI il 25–47% dei punti GBIF di presenza
  scora esattamente 0 (host noto e incompatibile, oppure `forest_fraction` = 0 su un punto
  con coordinate imprecise). Prima di dare la colpa ai pesi, separare le due cause e valutare
  se sia host o gate — l'intorno in validazione è ora il default e ha già mostrato che
  buona parte di quegli zeri era imprecisione delle coordinate. **La carica va valutata alla data dell'innesco, non a oggi**: oggi le due cose
  guardano tempi diversi e si escludono a vicenda (`docs/COME-FUNZIONA.md`), ed è il
  difetto strutturale dell'asse dinamico. Poi: **notifiche** al trigger di readiness — era il
  canale di consegna dell'active learning e con l'uscita del bot resta scoperto: servirà
  un mittente Telegram in sola uscita, oppure web push; learner (v4); **CORINE Land Cover** (sottotipi di prato/pascolo) + cablaggio hook
  `extra_static_layers`; saprotrofi del legno (chiodini, canopy invertito); profili di specie di
  prato.

## Uso
```bash
python -m gis.fetch_boundaries         # AOI: confini ISTAT BZ+TN+VE (versionata, si rifà solo se cambia)
python -m gis.make_map                 # genera le mappe statiche a 200 m (richiede i layer grezzi, locali)
python -m gis.fetch_meteo              # backfill/poll archivio meteo, tutte le specie (incrementale)
python -m gis.predict_today            # fasi "idoneità dinamica" per specie → GeoJSON
python -m gis.validate                 # Boyce vs GBIF (AOI + intorno 250 m; --pixel, --no-aoi per i vecchi riferimenti)
python -m gis.replay boletus_edulis    # replay dell'archivio: quante volte sarebbe stato "pronto", e chi veta
python -m gis.replay --cell m2200_..   # timeline giorno per giorno di una cella sola
pytest                                 # test
# web app (cattura inclusa, /log): uvicorn webapp.app:app
```
I **layer grezzi** (DEM/forestale/geologia/WorldCover/canopy, ~1.7 GB) sono gitignorati e servono
solo a *generare* le mappe (`make_map`, ora eseguibile anche sul VPS via il bottone Rigenera).
A runtime servono: le mappe (`data/maps/idoneita_*.tif`), l'archivio meteo (`data/meteo.db`,
costruito dal poller) e i profili vivi (`data/profiles/`, editabili online — fonte di verità sul VPS).

## Prima verità di campo — 6 set 2026, Cima d'Asta (46.13515, 11.68636)

Raccolto reale di porcini, abbondante e fresco. La mappa non segnalava nulla lì. La cella
meteo (`m2200_321_2323`) **era** in archivio ed **era** candidata (41/121 celle statiche
sopra 0.4, max 0.844): il buco non è di copertura, è di punteggio. `readiness = 0.000`.

| fattore | osservato | soglia profilo | membership |
|---|---|---|---|
| fenologia | settembre | [8,9,10,11] | 1.000 |
| **umidità suolo** | **0.149** | **floor 0.22** | **0.000 — gate duro** |
| **pioggia 15 gg** | **31.1 mm** | **45 mm** | **0.073** |
| **shock termico** | **2.7 °C** | **5 °C** | **0.233** |
| temp. suolo | 14.8 °C | [8, 18] | 1.000 |
| lag | dst = 12 gg | opt [10, 20] | 1.000 |

Il **lag ha avuto ragione**: la pioggia di innesco (25 ago, 19.1 mm) cade 12 giorni prima
del raccolto, in mezzo alla finestra. È il parametro che la spec dà per più incerto, e in
questa osservazione è l'unico che ha funzionato. A vetare sono le tre soglie di intensità.

Il `moisture_floor` è **falsificato** per quella cella, non solo stretto: in 48 giorni di
archivio l'umidità lì ha toccato 0.223 una volta sola, quindi il gate è di fatto sempre
chiuso. Sull'intero archivio la soglia sta sopra la mediana (0.182) e il 64% delle celle la
manca oggi; i 45 mm/15 gg li raggiunge il 22% delle celle. Le soglie stanno in cima alla
distribuzione osservata, non dentro.

Sensibilità sulla stessa cella (dst già in finestra): `moisture_floor` 0.14 da solo non
basta (charge 0.258 < `CHARGE_THR` 0.5); con anche pioggia 30 mm diventa **pronto**
(readiness 0.695); con anche shock 2 °C va a 1.000.

**Cross-check statico nello stesso punto** (idoneità 0.435): host `pecceta` 1.000, quota
1588 m 1.000, `soil_ph` acido 1.000, gate habitat 0.954 — e a tirare giù sono `aspect`
0.100 (versante caldo contro un profilo che chiede `cool`) e `slope` 0.280 (37° contro opt
[5,30]). Il commento nel profilo dice già «fresca; anche assolata in quota», ma la codifica
non modula per quota: a 1590 m a settembre l'esposizione a sud-est è un pregio.

### Il replay dell'archivio, e la taratura che ne è uscita

Una sola osservazione non tara un modello. Ma l'archivio meteo permette una falsificazione
**senza etichette**: `python -m gis.replay` rigira `readiness_state` su ogni giorno passato
di ogni cella e conta quante volte una cella sarebbe mai stata "pronto", attribuendo a
ciascun blocco il primo vincolo che scatta. Col profilo di luglio, su 198.347 celle-giorno
dentro l'AOI:

| stato | celle-giorno | quota |
|---|---|---|
| pronto | 678 | **0.34%** |
| in fieri | 13.084 | 6.60% |
| tardi | 14 | 0.01% |
| niente | 184.571 | 93.05% |

A vetare: **gate umidità 38.7%** e **carica sotto soglia col peggiore che è lo shock
termico 30.9%**. Membership media dello shock: **0.175**. Le due soglie stavano in cima
alla distribuzione osservata (moisture_floor 0.22 = p63 dell'umidità; shock 5 °C = p93),
non dentro.

E c'era un'incoerenza interna al profilo: `rain_window_days` 15 < `lag_days.opt` max 20.
Una buttata valutata a dst = 12–20 giorni viene misurata con una finestra di pioggia che
la pioggia d'innesco l'ha già persa. Il 6 set la cella di raccolta vedeva 31 mm su 15
giorni e 78 su 25.

Valori nuovi in `profiles/boletus_edulis.yaml`, e cosa cambiano al replay:

| | prima | dopo |
|---|---|---|
| pronto | 0.34% | **4.28%** |
| giorni con almeno una cella pronto (su 64) | 26 | 37 |
| picco celle pronto in un giorno (su 3.294) | 121 | 763 |
| cella del ritrovamento, 6 set | nessuno stato | **pronto**, readiness 1.000 |

**I limiti di questa taratura, detti chiaramente.** Il positivo noto è uno, e dopo la
taratura tutti e sei i suoi fattori valgono 1.000: quei valori sono abbastanza permissivi
da farlo passare, non dimostrati giusti. Di negativi non ce n'è nemmeno uno
(`observations.db` è vuoto), quindi niente trattiene il modello dall'essere troppo largo:
il 4.28% è un controllo di plausibilità, non una validazione. L'unica modifica che
difenderei a prescindere dall'osservazione è la finestra di pioggia, che è un vincolo di
coerenza fra due parametri dello stesso profilo. Le altre tre sono ancoraggi a percentili,
in attesa che il *learner dinamico* (v4) le sostituisca con dati.

Il profilo **vivo** sul VPS sta nel volume e si cambia dall'editor online: questa modifica
tocca solo il default versionato, la produzione resta com'è finché non la si incolla lì.

## La cattura, e perché è fatta così

Si logga dal **form della web app** (`/log`). Prima era un bot Telegram: il form vince
sul caso che conta davvero — la sera, a casa, quando il posto lo ricordi ma non ci sei
più — perché un pin trascinabile su mappa topografica è più preciso di una posizione
condivisa a memoria, e perché chi logga non ha bisogno di un account Telegram. Si perde
la coda offline di Telegram: senza campo non si salva, e si scrive al rientro. Per questo
il **giorno** è un campo e non l'ora di invio.

Tre tipi di uscita: **🍄 Trovato**, **🚫 Vuoto**, **🎯 Mirato a vuoto**. La
distinzione fra vuoto generico e vuoto mirato non è cosmetica: è la semantica degli zeri
del §6.1, dove un vuoto conta come negativo solo per le specie che lì potevano esserci, e
un vuoto mirato sul proprio posto buono è uno zero forte sul *timing*. Collassarla in
«positivo/negativo» la perderebbe al momento della cattura, e dopo non si recupera.

Quattro scelte che vale la pena non disfare:

- **Si chiede il giorno dell'uscita** (`obs_date`), separato da `ts_submit`. Il learner
  legge lo stato del meteo a quella data, e la finestra del lag è di dieci giorni:
  loggare la sera dopo sposterebbe `days_since_trigger` di un decimo della finestra.
- **Fasce con gli estremi scritti**, non aggettivi: «16+» lo leggono tutti uguale,
  «molti» no. Vale per l'abbondanza e per il tempo di ricerca.
- **`effort_min` anche sui vuoti.** Un vuoto informa in proporzione a quanto hai cercato:
  dieci minuti e tre ore non sono lo stesso zero, e pesarli uguale diluisce i vuoti veri.
- **«Altra specie, stesso punto»** dopo il salvataggio, che riusa pin e giorno: in
  un'uscita trovi porcini e finferli, e le etichette raddoppiano a costo quasi nullo.
- **Lo storico si corregge** (`/me`): un'osservazione sbagliata pesa più di una mancante,
  perché il learner la prende per buona. Cambiando il tipo di uscita i campi non più
  pertinenti vengono azzerati, altrimenti un vuoto si porterebbe dietro la fase di quando
  era un ritrovamento; e se si sposta il pin, i `cell_id` tornano NULL e li riassegna il
  poller, invece di restare legati alla cella sbagliata.
- **La casa** è sull'utente (`/me`), non una impostazione globale: serve alle distanze
  e ognuno parte da casa sua. La usa «vicino a me» nella ricerca degli spot, dove il
  raggio si applica **prima** della classifica: prendere i migliori della regione e poi
  tenere quelli vicini darebbe quasi sempre una lista vuota. Il punto di casa non passa
  mai dal client: sta sull'account, e il browser manda solo il raggio.

E un buco chiuso lato pipeline: il poller archiviava il meteo **solo** per le celle che il
modello statico giudica già buone (idoneità > 0.4). Un ritrovamento in una cella mediocre
restava senza storia meteo, quindi inutilizzabile — e sono proprio le osservazioni più
capaci di correggere il modello. Ora `fetch_meteo` unisce alle candidate le celle di ogni
osservazione loggata, e assegna i `cell_id` rimasti NULL alla cattura.

## Note di design (dalla spec)
- **Niente ML all'avvio**: idoneità = MCE a pesi esperti; i ritrovamenti aggiornano priori (bayesiano).
- **Assi di apprendimento separati** (§6.2); **trigger sulla temperatura del SUOLO** (§5).
- **host-sconosciuto ≠ assente**; il gate **habitat** (WorldCover, completo — bosco o prato) è separato
  dal "che tipo di bosco?" (forestale CFI). I saprotrofi saltano il gate host.
- **L'AOI è il confine di validità di "unknown ≠ absent".** I layer tematici (forestale CFI,
  geologia) coprono tre unità amministrative; DEM, WorldCover e canopy coprono tutto il bbox,
  che è più largo (sponda lombarda, Friuli, Polesine, Tirolo). Fuori dalle tre, host sconosciuto
  = 1.0 promuoveva il fuori-copertura come se l'ospite fosse quello giusto: edulis dava 0.74 in
  Adamello ovest e 0.45 in Carnia, dove non c'è un dato forestale. `AOIProvider` (`is_required`)
  taglia lì. Estendere invece che tagliare è possibile: la CFI2020 è nazionale.
- `dynamic_triggers` oltre *aereus* = priori di prima passata **da rivedere**.
