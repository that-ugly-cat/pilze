# Come funziona Pilze

Spiegone dei due assi del modello e delle fonti da cui vengono i dati. È il documento da
leggere prima di toccare un profilo o discutere un numero: qui c'è il *perché*, il
riferimento campo per campo dei profili sta subito dopo, e il piano di lavoro in
`ROADMAP.md`.

Si legge da due parti, ed è lo stesso file: su GitHub come markdown, e nell'app alla
pagina **Doc**, che lo rende a runtime. Due copie divergerebbero, e la copia sbagliata
sarebbe sempre quella che qualcuno sta leggendo.

Tutti i numeri di questa pagina sono verificati sui dati che stanno in `data/`, non ripresi
dalla documentazione delle fonti.

---

## L'idea in una riga

```
predizione(cella, specie, giorno) = idoneità_statica(cella, specie) × readiness(meteo, specie)
```

Due domande separate, e tenerle separate è una scelta, non una comodità:

- **DOVE** possono crescere → fattori che cambiano poco (suolo, orografia, bosco). È una
  mappa, si calcola una volta e vale per mesi.
- **QUANDO** fruttificano → fattori che cambiano ogni giorno (pioggia, temperatura del
  suolo). È una serie temporale, si ricalcola ogni notte.

Il **prodotto** è voluto: uno zero in un asse azzera tutto. Niente ospite, niente funghi,
per quanto piova; e nel deserto climatico non serve il bosco perfetto.

Tenerli separati serve anche all'apprendimento: se si mescolassero, una giornata fortunata
verrebbe cementata dentro l'idoneità permanente del posto (spec §6.2).

---

## Il DOVE — idoneità statica

Sta in `engine/static_scorer.py`. Ogni cella della griglia (200 m, UTM 32N) riceve un
punteggio in [0,1] per ciascuna specie, e la formula è:

```
idoneità = gate_AOI × gate_host × gate_habitat × media_geometrica_pesata(quota, pendenza,
                                                                        esposizione, pH, drenaggio)
```

### I gate, che azzerano

**AOI — l'area con dati tematici.** Bolzano, Trento, Veneto. Fuori si ritorna `None` e la
cella non viene nemmeno scorata. Non è pignoleria amministrativa: il motore tratta l'host
*sconosciuto* come neutro (§7.5, «unknown ≠ absent»), regola giusta dentro un'area
rilevata — dove un buco è un piano di gestione mancante — e ribaltata fuori, dove
promuoverebbe il fuori-copertura come se l'ospite fosse quello giusto. Prima del ritaglio
la mappa dava 0.74 all'Adamello occidentale, dove non esiste un dato forestale. L'AOI è il
confine di validità di quella regola, e togliendolo si tolgono il 47% delle celle sopra 0.4.

**Host — l'ospite micorrizico.** Dal profilo: un peso [0,1] per ciascuna delle 20 classi
forestali. Ospite noto e incompatibile → 0 (per l'edulis, una larice-cembreta vale zero, e
la spec lo dice esplicito: mai larice). Ospite **sconosciuto** → 1.0, neutro. I saprotrofi
saltano il gate: per loro l'albero non è il partner.

**Habitat — «è il posto giusto?».** Da WorldCover, che ha copertura completa: la frazione
di *tree cover* per le specie di bosco, quella di *grassland* per i saprotrofi di prato.
Fuori dall'habitat → 0. È separato apposta dal «che tipo di bosco?»: la copertura è
completa e affidabile, la composizione no.

**La canopia entra qui, di traverso.** Per le classi di conifera il peso dell'host viene
moltiplicato per `canopy_alive`: dove la chioma è morta — Vaia, bostrico — la pecceta
mappata nel 2020 non è più un ospite. È un reality-check agnostico alla causa, non un
change-detection di un evento specifico.

### I fattori graduati, che smussano

Quota, pendenza, esposizione, pH del suolo e drenaggio si combinano in **media geometrica
pesata**: un fattore mediocre abbassa, non azzera. Le funzioni di appartenenza sono
trapezoidali (`engine/membership.py`), quindi non c'è nessun salto artificiale ai bordi —
una faggeta a 1001 m con `max: 1000` non crolla a zero.

I pesi di default (`static_scorer.DEFAULT_WEIGHTS`): quota 1.0, esposizione 0.8, pH 0.8,
pendenza 0.5, drenaggio 0.5.

> **Il drenaggio oggi non fa niente, e va detto.** Nessun provider produce la chiave
> `drainage`: il valore è sempre «non misurato», che vale 0.5 per convenzione. Con peso 0.5
> su 3.6 totali, ogni cella prende una decurtazione costante di circa il 9% che non
> discrimina nulla. Non falsa le classifiche, perché è la stessa per tutti, ma i punteggi
> assoluti sono depressi — e le soglie dell'interfaccia (0.3, 0.4) vanno lette sapendolo.
> O si trova una fonte di drenaggio, o il fattore va tolto dai pesi.

---

## Il QUANDO — readiness dinamica

Sta in `engine/dynamic_scorer.py`, e lavora su una griglia più grossolana: la **cella meteo
di 2.2 km**, che è la maglia nativa di ICON-D2. Non ha senso pretendere più risoluzione di
quella che ha il modello meteo.

```
readiness = gate_fenologia × gate_umidità × media_geometrica(pioggia, temp_suolo, shock, lag)
```

### Le feature, e da cosa si ricavano

Dalla serie giornaliera archiviata (`gis/meteo.py`, `features_from_daily`):

| feature | come si calcola |
|---|---|
| `cumulative_rain_mm` | somma della pioggia sulla finestra `rain_window_days` del profilo |
| `soil_moisture` | ultima umidità del suolo disponibile (3–9 cm) |
| `soil_temp_c` | ultima temperatura del suolo (6 cm) |
| `thermal_shock_c` | calo dal massimo degli ultimi 10 giorni al valore di ora, **sul suolo** |
| `days_since_trigger` | giorni dall'ultima pioggia ≥ 10 mm in un giorno |
| `month` | mese, per la fenologia |

**Perché il suolo e non l'aria.** Il suolo ha inerzia termica: è robusto alle inversioni
notturne, e soprattutto è quello che il micelio sente davvero (§5). Lo shock termico si
calcola lì per la stessa ragione.

**L'umidità del suolo è anche la scorciatoia sul cold-start.** ICON-D2 non ha archivio
storico — i dati durano circa 24 ore — quindi l'archivio ce lo costruiamo in avanti col
poller, e le somme di pioggia mobili impiegano settimane a riempirsi. L'umidità del suolo
dell'analisi però integra già l'antecedente: dà segnale dal giorno uno.

### I gate e le fasi

`fenologia` azzera fuori dai mesi della specie, con una rampa morbida ai mesi adiacenti
(0.15) per non spaccare al cambio di mese. `moisture_floor` azzera sotto una soglia di
umidità.

Poi c'è un terzo filtro che **non sta in nessun profilo** e va conosciuto: la **carica**,
cioè la readiness senza il fattore di timing. Dice se le condizioni ci sono, a prescindere
da quando. Sotto `CHARGE_THR = 0.5` la cella non viene mostrata. Chi tara un profilo lo
dimentica, e poi non capisce perché la mappa resta spenta.

Se la carica passa, la **fase** viene dal confronto fra `days_since_trigger` e la finestra
`lag_days.opt = [Lmin, Lmax]`:

- `dst < Lmin` → **in fieri** (la buttata sta arrivando, con una stima di quanti giorni)
- `Lmin ≤ dst ≤ Lmax` → **pronto**
- `Lmax < dst ≤ 2·Lmax` → **tardi**

Il lag è, per ammissione della spec, il parametro più incerto del modello. È anche l'unico
che alla prima verifica di campo ha avuto ragione (vedi il README).

---

## Le fonti dei dati

Tutte pubbliche e senza chiave, tranne le due carte forestali/geologiche regionali che si
scaricano a mano dai rispettivi geoportali.

### Layer statici

| layer | fonte | come arriva | copertura verificata |
|---|---|---|---|
| **DEM** (quota, pendenza, esposizione) | Copernicus **GLO-30** | `fetch_dem.py`, AWS open data, no auth | 12 tile, 1 arcsec (~31 m), EPSG:4326 |
| **Forestale** (host) | **CFI2020** — Carta Forestale d'Italia, MASAF | shapefile a mano, campo `Ct_CFI` | 138.512 poligoni (Bolzano 92.432 · Trento 20.603 · Veneto 25.477), UTM 33N |
| **Gate habitat** | **ESA WorldCover** 10 m, v200 2021 | `fetch_worldcover.py`, AWS, no auth | 2 tile 3°×3°, ~9 m effettivi |
| **Geologia → `soil_ph`** | CARG / substrato, tre servizi regionali | shapefile e gpkg locali (`fetch_geology*.py`) | Trento 121.443 poligoni / 411 formazioni · Bolzano 66.133 / 465 · Veneto 5.048 / 54 litologie |
| **Canopia viva** | **Sentinel-2 L2A** via STAC Earth Search (COG su AWS) | `fetch_canopy.py`, composito mediano estivo | 140 tile da 0.2°; ne mancano 10, tutti fra laguna e mare aperto |
| **AOI** | **ISTAT**, limiti delle unità amministrative 2025 (generalizzati) | `fetch_boundaries.py` | Bolzano 7.399 km² · Trento 6.207 km² · Veneto 18.354 km² |

**Il crosswalk forestale** (`config/crosswalk.yaml`) traduce i 20 codici `Ct_CFI` nelle 20
classi host usate dai profili — pecceta, faggeta, mugheta, castagneto, querceto… È l'unico
modo di far girare un modello unico a cavallo di tre amministrazioni con legende diverse.
La CFI2020 ha risolto proprio quello: prima c'era un patchwork Veneto/Trentino con due
legende e copertura parziale.

**La geologia si classifica per parole chiave** sul nome della formazione: Dolomia e
Calcare → calcareo, graniti, porfidi e filladi → acido, il resto neutro. I set di parole
sono per regione, perché i vulcanici trentini (porfidi permiani, riolitici) sono acidi
mentre quelli veneti (Euganei, Lessini, cenozoici basaltico-trachitici) danno suoli neutri.
Dove il substrato non affiora (coperture quaternarie) si prende il poligono più vicino
entro 2 km, e la cella lo dichiara con `geology_fallback`.

**La canopia** è un composito mediano dell'ultima estate: NDVI e NBR mascherati dalle nuvole
via SCL, combinati in un indice «verde × struttura». Le soglie (NDVI 0.45–0.78, NBR
0.20–0.55) sono tarate su Paneveggio incrociando 23.004 pixel di conifera mappata. Punto
cieco accettato: l'abete morto **in piedi**, che da satellite somiglia ancora a un bosco.

### Layer dinamico

**ICON-D2** del DWD (2.2 km, convection-permitting), preso via **Open-Meteo**
(`api.open-meteo.com/v1/dwd-icon`, nessuna chiave). Variabili orarie: `precipitation`,
`soil_temperature_6cm`, `soil_moisture_3_to_9cm`, `temperature_2m`.

Sorgente **unica** per scelta: niente ERA5-Land, niente stack multi-sorgente, così non c'è
discontinuità di giunzione fra bias diversi. Il prezzo è il cold-start.

| | |
|---|---|
| **Da quando** | **20 giugno 2026** — la prima data in archivio. Prima non esiste, e non è recuperabile: ICON-D2 tiene le corse circa 24 h, l'archivio ce lo costruiamo in avanti noi. |
| **Quanto** | 79 giorni, 422.801 righe, **7.206 celle** (al 6 set 2026). Le celle più vecchie hanno la serie intera; quelle entrate dopo partono dal loro backfill. |
| **Risoluzione spaziale** | 2.2 km, la maglia nativa di ICON-D2. Le celle dell'archivio sono i quadrati da 2200 m della griglia comune. Più fine non avrebbe senso: sarebbe interpolazione, non informazione. |
| **Risoluzione temporale** | orario alla fonte, **aggregato a giornaliero** in archivio: pioggia sommata, temperatura e umidità del suolo mediate sul giorno. Una riga per cella-giorno. |
| **Frequenza** | una volta al giorno, **a mezzanotte**, poi il ricalcolo di «pronte oggi». |

<!--ARCHIVIO-->

Ogni poll chiede gli **ultimi 3 giorni**, non solo quello appena chiuso: una corsa persa si
richiude da sola entro tre giorni, e solo un'interruzione più lunga lascia un buco vero. È
il motivo per cui c'è comunque un **gap-detector** — quello che manca non torna più.

Una cella nuova (specie nuova, o un'osservazione loggata fuori dalle candidate) entra con
un **backfill di 21 giorni**, incrementale: chi ha già abbastanza storia recente viene
saltato, così un run interrotto riprende dalle mancanti senza riscaricare tutto.

Il poller (`fetch_meteo.py`) usa batching multi-località — circa 45 richieste per l'intera
regione invece di una per cella, un poll in circa un minuto — con retry e backoff sul 429.
L'archivio è un SQLite (`data/meteo.db`).

Le celle da pollare sono quelle **candidate** (almeno una cella statica sopra 0.4 dentro i
2.2 km) **più quelle di ogni osservazione loggata**. La seconda parte non è un dettaglio:
senza, il sistema archivierebbe il meteo solo dove il modello statico è già d'accordo con
sé stesso, e le osservazioni più capaci di correggerlo resterebbero senza storia.

### Validazione

**GBIF** per le presenze (`occurrences.py`), confrontate col **Continuous Boyce Index**
contro un background casuale dentro l'AOI. Attenzione a una trappola risolta: *aestivalis*
va cercato con la chiave accettata (*Boletus reticulatus*), altrimenti dà zero punti.

**L'archivio meteo si può validare senza etichette**: `gis/replay.py` rigira lo scorer su
ogni giorno passato di ogni cella e conta quante volte una cella sarebbe mai stata
«pronto», attribuendo a ciascun blocco il primo vincolo che ha vetato. È così che si è
scoperto che le soglie di luglio stavano in cima alla distribuzione osservata invece che
dentro.

---

## Che modello è, e come si valida

### No, non è bayesiano

Domanda ricorrente, e la risposta onesta è **non ancora**. Oggi Pilze è una **valutazione
multi-criterio sfumata** (fuzzy MCE), che è lo strumento classico delle carte di idoneità
dell'habitat, non un modello statistico.

Cosa manca per essere bayesiano: non c'è una distribuzione a priori sui parametri, non c'è
una verosimiglianza, non c'è un posteriore. Quando la documentazione dice «priori esperti»
usa la parola in senso lato — sono **valori** decisi da una persona, non distribuzioni. E
non esce nessuna misura di incertezza: il modello dà un numero, non un intervallo.

Il bayesiano è il piano della **v4**: ritrovamenti e vuoti aggiornano i parametri come si
aggiorna un priore, e per scelta l'aggiornamento sarà **grossolano** — sposta il profilo
intero, non i singoli fattori. Con poche decine di punti l'assegnazione del credito fra
sei fattori correlati è impossibile, e fingere di poterla fare produrrebbe numeri precisi
e sbagliati.

Quello che abbiamo fatto finora non è nemmeno inferenza automatica: è **falsificazione a
mano**. Si prende un'osservazione di campo, si guarda quale soglia l'ha vetata, si verifica
dove sta quella soglia nella distribuzione osservata, e se sta al percentile 100 la si
dichiara falsificata. È un metodo legittimo e trasparente, ma da poche osservazioni, e va
sostituito appena i punti diventano decine.

### Il punteggio non è una probabilità

`idoneità = 0.7` **non** significa «70% di probabilità di trovare funghi». Significa che
quella cella sta in alto nella scala interna del modello, e nient'altro. La scala non è
calibrata contro nessuna frequenza osservata, e i valori assoluti dipendono anche da
scelte arbitrarie — per esempio `drainage`, che non essendo misurato da nessun layer
abbassa ogni cella di circa il 9% senza distinguere niente.

Quello che il punteggio fa bene è **ordinare**: fra due celle, quella col punteggio più
alto è quella che il modello preferisce. Per questo le metriche che usiamo sono tutte di
ordinamento, non di calibrazione.

### Perché quelle formule

**I gate moltiplicano** perché codificano condizioni necessarie: senza ospite non ci sono
funghi, per quanto piova. Uno zero in un fattore necessario deve azzerare il prodotto, ed
è esattamente ciò che fa la moltiplicazione.

**I fattori graduati si combinano in media geometrica pesata**, non aritmetica. La
geometrica penalizza lo squilibrio: una cella con quota perfetta e pH pessimo vale meno di
una mediocre in entrambi, mentre l'aritmetica le pareggerebbe. È anche la media naturale
per grandezze che si moltiplicano, e in scala logaritmica è semplicemente una media
pesata. I pesi: quota 1.0, esposizione 0.8, pH 0.8, pendenza 0.5, drenaggio 0.5.

**Le appartenenze sono trapezoidali**, non a gradino: una faggeta a 1001 m con `max: 1000`
non deve crollare a zero. I bordi netti sono quasi sempre un artefatto di come è scritta la
regola, non un fatto biologico.

### Il Continuous Boyce Index

È la metrica principale, e la ragione è che i dati di validazione sono **presence-only**:
GBIF dice dove qualcuno ha visto un fungo, mai dove ha guardato e non c'era. Senza vere
assenze, AUC e compagnia non si calcolano onestamente.

Il Boyce (Hirzel et al., 2006) confronta due frequenze lungo la scala di idoneità: **F**,
la quota di presenze che cade in una finestra di punteggio, ed **E**, la quota di area
*disponibile* nella stessa finestra. Il rapporto **P/E** dice se quella fascia è
frequentata più di quanto sarebbe per caso; poi si correla (Spearman) P/E con l'idoneità,
e se il modello è buono P/E cresce in modo monotòno. L'implementazione (`gis/boyce.py`) usa
100 finestre mobili larghe il 10% della scala.

Si legge così: **+1** ordina perfettamente, **0** non fa meglio del caso, **negativo** è
peggio del caso — le presenze stanno dove il modello dice di no.

Stato al 7 set 2026, dentro l'AOI, **scorando il pixel esatto** — che è il modo severo,
e la sezione qui sotto spiega perché non è quello giusto:

| specie | punti GBIF | Boyce |
|---|---|---|
| porcino | 379 | +0.462 |
| finferlo | 119 | +0.238 |
| porcino rosso | 24 | +0.138 |
| porcino nero | 4 | +0.091 |
| ovolo | 17 | +0.014 |
| estatino | 32 | −0.367 |

### Il caso dell'estatino: quando è il metro a non reggere

L'estatino è a −0.367 sul pixel esatto (**+0.564 sull'intorno di 250 m**), e la lettura
ovvia — «il modello lo manda dove non è» — non regge all'esame. Il punteggio medio alle presenze (0.195) è *più alto* di quello del background
(0.077): il modello non è anti-predittivo, è **non monotòno**, e il Boyce misura la
monotonia. Le presenze si affollano a metà scala, mentre il vertice resta vuoto.

Il perché sta in due cose, e nessuna delle due è un difetto dei parametri.

**Le etichette sono contaminate.** Dei 32 punti GBIF, il 25% sta sopra i 1200 m e il 16%
sopra i 1500, che è il massimo assoluto del profilo; cinque cadono in larici-cembreto, un
bosco che per una specie termofila di latifoglie non ha senso. Gli stessi punti, letti col
profilo del **porcino**, prendono 0.301 di media contro lo 0.195 del loro: somigliano a
porcini più che a estatini. *B. reticulatus* si confonde con *B. edulis* facilmente, e in
un archivio di segnalazioni non verificate quella confusione finisce nei dati.

**E il vertice del modello non è campionato.** Il profilo dà il massimo ai castagneti e
querceti collinari, ma nell'AOI il bosco sotto gli 800 m è il **28% del disponibile** e
solo l'**8.3%** dei punti GBIF: sotto i 600 m, 19% del bosco contro 4.5% dei punti. In
tutta l'area, sotto i 900 m in latifoglie termofile, ci sono **tredici** segnalazioni di
tutte e sei le specie messe insieme. Nessuno raccoglie dati lì, quindi la fascia dove il
modello si sbilancia di più non ha modo di essere confermata.

La conclusione è che **per l'estatino il Boyce oggi non è una metrica utilizzabile**, e
quel −0.367 non va letto come un giudizio sul profilo. Rifare i parametri per far salire
quel numero significherebbe insegnare al modello a trovare porcini e chiamarli estatini.
Serve un'osservazione di campo: un solo estatino loggato, con la sua data e il suo bosco,
vale più di trentadue segnalazioni di provenienza ignota.

### La seconda trappola: un punto GBIF non è un pixel

Le celle sono da 200 m; una segnalazione GBIF ha coordinate che spesso valgono qualche
centinaio di metri, quando non è il centro del paese o l'inizio del sentiero. Scorare il
**pixel esatto** significa allora chiedere al modello di indovinare un posto dove il fungo
non era.

Si vede nei numeri: dal 25% al 47% delle presenze prende esattamente zero, a seconda della
specie. Rifacendo la misura sul **massimo di un intorno di 250 m** — l'ordine di grandezza
dell'incertezza — gli zeri quasi spariscono e il Boyce cambia di segno per due specie.
L'operatore va applicato anche al background, altrimenti si gonfia soltanto il numeratore:

| specie | zeri sul pixel | zeri sull'intorno | Boyce sul pixel | Boyce sull'intorno |
|---|---|---|---|---|
| porcino | 28% | 8% | +0.377 | **+0.572** |
| finferlo | 6% | 2% | +0.209 | **+0.668** |
| estatino | 25% | 6% | −0.373 | **+0.564** |
| ovolo | 47% | 12% | −0.001 | **+0.362** |
| porcino rosso | 4% | 0% | +0.146 | +0.202 |

Applicando l'intorno alle sole presenze si arriva a +0.78/+0.95, ed è il modo sbagliato di
farlo: quel numero non misura il modello, misura l'operatore.

La lettura giusta è che **il modello discrimina meglio di quanto il pixel-per-pixel
lasciasse credere**, e che l'estatino e l'ovolo non erano rotti: erano misurati con un
metro più fine della precisione del dato. La validazione dovrebbe adottare l'intorno come
default — è già fra le cose da fare, ora con dei numeri dietro.

### La trappola del Boyce: il «disponibile» decide il risultato

`E` dipende da cosa si considera disponibile, e questo cambia il numero più di quanto lo
cambi il modello. È successo davvero: a luglio l'edulis dava **+0.71**, oggi dà +0.46, e nel
frattempo il modello è **migliorato**. Il vecchio numero era misurato senza il ritaglio
sull'AOI, cioè con presenze fuori dall'area dei dati tematici che prendevano `host = 1.0`
gratis e finivano in cima alla scala. Non è un peggioramento: è che il metro di prima era
truccato a favore.

Regola pratica: **due valori di Boyce sono confrontabili solo se hanno lo stesso
background**. Cambiando l'area, la soglia o i layer attivi, il confronto salta.

Limite ancora aperto: il background è campionato uniformemente dentro l'AOI, quindi mescola
pianura e montagna, e una parte della «discriminazione» è in realtà «montagna contro
pianura». La cura è un background ristretto al bosco più una cross-validation a blocchi
spaziali (§6.3), che non è ancora fatta.

### Il replay: falsificare senza etichette

`gis/replay.py` non usa nessun ritrovamento. Rigira lo scorer dinamico su ogni giorno
passato di ogni cella dell'archivio — circa 200.000 celle-giorno — e conta quante volte una
cella sarebbe mai stata «pronto», attribuendo a ciascun blocco il **primo vincolo che
scatta**.

È un test di plausibilità, non di verità: non può dire se il modello ha ragione, ma può
dire che è **impossibile** che ce l'abbia. Se in tutta la stagione nessuna cella arriva mai
a «pronto» mentre i funghi ci sono stati, il modello è falsificato senza bisogno di una
sola etichetta. È così che si è scoperto che il `thermal_shock_c` del porcino rosso stava
al **percentile 100** delle osservazioni: non una soglia severa, un veto permanente.

### I dati di campo, e cosa si può concludere da cinque punti

I ritrovamenti loggati sono **presenze e assenze vere, con lo sforzo**: esattamente ciò che
a GBIF manca. Un vuoto **mirato** — cercavi quella specie, dove sai che c'è — è uno zero
forte sul *quando*; un vuoto generico dice molto meno; e senza il tempo di ricerca un vuoto
non è pesabile, perché dieci minuti e tre ore non sono lo stesso zero.

Ma cinque osservazioni sono cinque. L'esempio dell'esposizione mostra il ragionamento e il
suo limite: quattro ritrovamenti su cinque stanno su versante caldo mentre i profili
chiedono *fresco*. Il tasso di base, misurato su 1500 celle boscate dell'AOI, è warm 36% /
cool 34% / neutral 30%: il terreno non è sbilanciato, e vedere quattro caldi su cinque ha
probabilità di circa il **6%**. Suggestivo, non dimostrativo.

Il parametro è stato ammorbidito lo stesso — da 0.1 a 0.35 — e la ragione non è statistica
ma **asimmetrica**: un fattore dieci codifica una quasi-certezza che quel prior non ha, e
sbagliare per eccesso di permissività costa una camminata, mentre sbagliare per eccesso di
severità costa un posto che non vedrai mai. La conferma è poi arrivata da una fonte
indipendente dai ritrovamenti: il Boyce su GBIF è migliorato su cinque specie su sei.

### Cosa renderebbe questo modello statistico

- Una **verosimiglianza** che leghi i ritrovamenti (con lo sforzo) al punteggio, così da
  aggiornare i parametri invece di spostarli a mano.
- **Calibrazione**: verificare che le celle a 0.7 producano davvero più spesso di quelle a
  0.4, e in che rapporto.
- Metriche di **skill sopra una baseline ingenua**, non numeri grezzi: precision@k sugli
  spot proposti, errore sul lag in giorni, Boyce con cross-validation a blocchi.
- Trattare la **dipendenza spaziale**: due celle vicine non sono osservazioni indipendenti,
  e ignorarlo gonfia ogni intervallo di confidenza che calcoleremo.

## Cosa sappiamo che non va

Onestà prima di eleganza: queste sono le cose che il modello, oggi, sbaglia o non sa.

- **I priori sono priori.** Nessuno di questi numeri è stato addestrato. Sono conoscenza
  esperta scritta in YAML, e la prima verifica sul campo (6 set 2026) ne ha falsificato uno
  in modo netto. Il dettaglio sta nel README.
- **`moisture_floor` è un parametro del tipo sbagliato.** Confronta un contenuto d'acqua
  volumetrico assoluto fra celle con suoli diversi, ma l'umidità di ICON-D2 dipende dalla
  tessitura assegnata al box: 0.149 su un podsol non è la stessa siccità che su un'argilla.
  Va sostituito da un indice **relativo** alla storia della cella.
- **Il drenaggio è un fattore costante** (vedi sopra).
- **Dentro l'AOI, il 25–47% dei punti GBIF di presenza scora esattamente 0.** O l'host noto
  è incompatibile, o il gate WorldCover azzera un punto con coordinate imprecise. Le due
  cause vanno separate prima di dare la colpa ai pesi.
- **La geologia non copre uniformemente.** Il fallback a 2 km tappa i buchi quaternari, ma
  una cella su substrato dedotto non vale una su substrato affiorante, e oggi il punteggio
  non distingue i due casi.
- **La validazione GBIF ha un bias di quota misurato.** Il bosco sotto gli 800 m è il 28%
  del disponibile ma solo l'8.3% delle segnalazioni: chi registra funghi lo fa in montagna.
  Ogni Boyce di una specie di bassa quota va letto sapendolo, e l'estatino ne è il caso
  limite (vedi sopra).
- **`RAIN_TRIGGER_MM = 10` è una costante di modulo**, non un campo di profilo: la
  definizione stessa di «innesco» non è tarabile per specie, mentre tutto il resto lo è.
