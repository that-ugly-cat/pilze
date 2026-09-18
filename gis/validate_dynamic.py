"""Metro per l'asse DINAMICO: il modello sa *quando*, non solo *dove*.

`gis.validate` misura il Boyce dell'idoneità statica contro le presenze GBIF: dice se la
mappa sa **dove**. L'asse dinamico non ha mai avuto un metro, e ogni taratura delle sue
soglie è stata finora fatta contro un'intuizione. Qui c'è la controparte temporale.

**Il disegno che decide tutto è il background.** Se le presenze si confrontano con
celle-giorno pescate ovunque, si finisce per misurare di nuovo l'asse spaziale: il
punteggio sale perché la mappa statica è buona, non perché il timing sia indovinato. È la
stessa trappola del «disponibile» già pagata sul Boyce statico (COME-FUNZIONA, «La
trappola del Boyce»), in versione temporale. Quindi il background è **la stessa cella in
date diverse della stessa stagione**: la posizione è tenuta costante per costruzione, e
l'unica cosa che varia è il giorno. Le date entro ±`GUARD_DAYS` dal ritrovamento sono
escluse dal background, altrimenti la buttata del ritrovamento stesso vi rientrerebbe.

**La sorgente meteo non è la stessa della produzione, e va saputo.** In produzione gira
ICON-D2 a ~2 km, che esiste solo in avanti dal giorno in cui è partito il poller; i
ritrovamenti GBIF vanno indietro di decenni, quindi qui si usa ERA5 (archivio Open-Meteo),
~25 km. Misurato su celle e giorni in comune (18 set 2026, 6 celle): la pioggia è
compatibile (Δ medio fra −2.0 e +1.8 mm), la temperatura del suolo è più calda di
0.3–2.4 °C, e **l'umidità del suolo è più secca di 0.04–0.11 m³/m³** — ICON legge 0.24
dove ERA5 legge 0.14–0.20, perché lo strato è 0–7 cm invece di 3–9 cm.

**Il gate `moisture_floor` resta ACCESO, contro l'intuizione.** Lo scarto qui sopra
faceva temere che un pavimento tarato su ICON non fosse trasportabile su ERA5, e il
primo disegno lo spegneva. Misurato sul finferlo (255 presenze, 3060 giorni di
background), a parità di tutto il resto:

    gate spento                        Boyce +0.096   mediana pres 0.974  bg 0.827
    gate assoluto (floor ICON su ERA5) Boyce +0.488   mediana pres 0.925  bg 0.726
    gate relativo al p82 della cella   Boyce +0.119   mediana pres 0.160  bg 0.027

Col gate spento la readiness **satura** — quasi tutti i giorni valgono quasi uno — e
nessuna metrica puo' ordinarli. Acceso, il Boyce sale a +0.49, dello stesso ordine del
Boyce statico della stessa specie (+0.65): **il grosso del segnale temporale sta nel gate
dell'umidita'**. Lo scarto fra le due sorgenti gioca a favore per caso — ERA5 e' piu'
umido in mediana (0.315 contro 0.204), quindi lo stesso 0.25 vi cade a un percentile piu'
basso e veta meno — e questo va saputo prima di concludere che il numero e' trasferibile
alla produzione, dove gira ICON.

La versione **relativa** alla storia della cella, che le docs prescrivono fra i difetti
noti, al percentile provato e' troppo severa e peggiora. E' un risultato sulla scelta del
percentile e non sull'idea: resta da cercare quello giusto, adesso che c'e' un metro per
farlo. Con `--senza-umidita` si spegne il gate, per rifare il confronto.

    python -m gis.validate_dynamic                  # tutte le specie con abbastanza punti
    python -m gis.validate_dynamic boletus_edulis   # una sola
    python -m gis.validate_dynamic --senza-umidita  # gate umidità spento (vedi sopra)
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from engine.dynamic_scorer import readiness
from engine.profiles import load_profiles

from . import boyce, grid, meteo

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
# Aggregati GIORNALIERI, non orari: sono le tre quantita' che `features_from_daily` usa
# davvero (somma pioggia, media temperatura e umidita' del suolo), e pesano ~30 volte meno.
# Con gli orari una stagione di 50 celle e' ~12 MB e la risposta arrivava TRONCATA a meta'
# JSON — un guasto che non e' un errore HTTP e si presenta come un bug di parsing.
DAILY = ["precipitation_sum", "soil_temperature_0_to_7cm_mean", "soil_moisture_0_to_7cm_mean"]
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "era5.db"
GEOJSON = Path(__file__).resolve().parent.parent / "data" / "gbif_occurrences.geojson"

GUARD_DAYS = 7        # date escluse dal background attorno al ritrovamento
BG_PER_PRESENCE = 12  # giorni di background campionati per ogni presenza
SEASON = (6, 11)      # giugno–novembre: la finestra fetchata per ogni cella-anno
MIN_PRESENCES = 25    # sotto questa soglia il Boyce è rumore (lezione dell'aereus)
BATCH = 50            # località per richiesta all'archivio
PACE_S = 6            # pausa fra richieste: il limite dell'archivio è al minuto
ERA5_START = date(1940, 1, 1)   # inizio della rianalisi
ERA5_LAG_DAYS = 6               # l'archivio arriva a ieri meno qualche giorno


def connect(db_path=DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS era5 (
        cell_id TEXT, date TEXT, precip_mm REAL, soil_temp_c REAL, soil_moist REAL,
        PRIMARY KEY (cell_id, date))""")
    conn.execute("CREATE TABLE IF NOT EXISTS fetched (cell_id TEXT, year INT, "
                 "PRIMARY KEY (cell_id, year))")
    return conn


def _cell_center(cid: str) -> tuple[float, float]:
    ring = grid.cell_polygon(cid)
    return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))


def load_presences(reg) -> dict[str, list[tuple[str, date]]]:
    """{species: [(meteo_cell_id, data)]} per le presenze datate nei mesi della specie.

    Il filtro AOI non serve qui: il background è la stessa cella, quindi una presenza
    fuori area non gonfia niente — al più aggiunge rumore su celle che la mappa statica
    non scora comunque.
    """
    gj = json.loads(GEOJSON.read_text(encoding="utf-8"))
    out: dict[str, list[tuple[str, date]]] = defaultdict(list)
    for f in gj["features"]:
        p = f["properties"]
        sid, d = p.get("species"), p.get("date")
        if not d or sid not in reg:
            continue
        try:
            dt = date.fromisoformat(d)
        except ValueError:
            continue
        months = reg[sid].phenology_months or []
        if months and dt.month not in months:
            continue
        if not (SEASON[0] <= dt.month <= SEASON[1]):
            continue
        lon, lat = f["geometry"]["coordinates"]
        out[sid].append((grid.assign(lat, lon)[1], dt))
    return out


def _finestra(year: int) -> tuple[date | None, date | None]:
    """Finestra fetchabile per l'anno, clampata alla copertura dell'archivio ERA5.

    L'archivio arriva a qualche giorno fa, non a fine stagione: chiedere il 30 novembre
    dell'anno in corso e' un 400 con la ragione scritta nel corpo, non una risposta vuota.
    """
    inizio = date(year, SEASON[0] - 2, 1)
    fine = date(year, SEASON[1], 30)
    ultimo = date.today() - timedelta(days=ERA5_LAG_DAYS)
    if inizio < ERA5_START or inizio > ultimo:
        return None, None
    return inizio, min(fine, ultimo)


def _get_json(url: str, year: int, tries: int = 6):
    """GET con backoff sul 429. L'archivio ha un limite al minuto, e una stagione di
    50 celle e' ~12 MB: il limite si prende a occhi chiusi. L'errore viaggia come CODICE
    piu' la ragione che l'API scrive nel corpo — senza, un 400 su una data fuori
    intervallo arriva come JSONDecodeError e sembra un bug nostro.
    """
    attesa = 20.0
    for tentativo in range(tries):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=300).read())
        except urllib.error.HTTPError as e:
            corpo = e.read()[:200].decode("utf-8", "replace")
            if e.code != 429 or tentativo == tries - 1:
                print(f"    {year}: HTTP {e.code} — {corpo}")
                return None
            print(f"    {year}: 429, attendo {attesa:.0f}s ({tentativo + 1}/{tries})", flush=True)
            time.sleep(attesa)
            attesa = min(attesa * 1.6, 90)
    return None


def _fetch_year(cells: list[str], year: int, conn: sqlite3.Connection) -> int:
    """Serie ERA5 giornaliere per `cells` nella stagione di `year`. Ritorna i giorni scritti."""
    todo = [c for c in cells if not conn.execute(
        "SELECT 1 FROM fetched WHERE cell_id=? AND year=?", (c, year)).fetchone()]
    if not todo:
        return 0
    inizio, fine = _finestra(year)
    if inizio is None:
        print(f"    {year}: fuori dalla copertura ERA5, saltato")
        return 0
    written = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        centers = [_cell_center(c) for c in chunk]
        # la finestra parte prima della stagione: la carica guarda indietro fino a
        # rain_window_days dall'innesco, e l'innesco fino a 2*lag_max dal ritrovamento
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{la:.4f}" for la, lo in centers),
            "longitude": ",".join(f"{lo:.4f}" for la, lo in centers),
            "start_date": inizio.isoformat(), "end_date": fine.isoformat(),
            "daily": ",".join(DAILY), "timezone": "Europe/Rome"})
        raw = _get_json(f"{ARCHIVE_API}?{q}", year)
        if raw is None:
            return written
        items = raw if isinstance(raw, list) else [raw]
        for cid, item in zip(chunk, items):
            d = item["daily"]
            rows = [(cid, giorno, pioggia or 0.0, temp, umid) for giorno, pioggia, temp, umid
                    in zip(d["time"], d["precipitation_sum"],
                           d["soil_temperature_0_to_7cm_mean"],
                           d["soil_moisture_0_to_7cm_mean"])]
            conn.executemany("INSERT OR REPLACE INTO era5 VALUES (?,?,?,?,?)", rows)
            conn.execute("INSERT OR REPLACE INTO fetched VALUES (?,?)", (cid, year))
            written += len(rows)
        conn.commit()
        print(f"    {year}: {min(i + BATCH, len(todo))}/{len(todo)} celle", flush=True)
        time.sleep(PACE_S)
    return written


def read_series(cid: str, year: int, conn: sqlite3.Connection) -> list:
    cur = conn.execute("SELECT date, precip_mm, soil_temp_c, soil_moist FROM era5 "
                       "WHERE cell_id=? AND date LIKE ? ORDER BY date", (cid, f"{year}-%"))
    return [(date.fromisoformat(d), r, t, m) for d, r, t, m in cur.fetchall()]


def score_at(profile, daily: list, upto: date, con_umidita: bool) -> float | None:
    """Readiness della cella a `upto`: la migliore fra le buttate vive (come la mappa)."""
    window = [row for row in daily if row[0] <= upto]
    if len(window) < 20:
        return None
    feats = meteo.features_per_flush(profile, window)
    if not feats:
        return 0.0
    if not con_umidita:
        feats = [{**f, "soil_moisture": None} for f in feats]
    return max(readiness(profile, f) for f in feats)


def validate_species(profile, presences: list[tuple[str, date]], conn,
                     con_umidita: bool, rng: random.Random) -> dict:
    by_year: dict[int, set[str]] = defaultdict(set)
    for cid, d in presences:
        by_year[d.year].add(cid)
    for year in sorted(by_year):
        _fetch_year(sorted(by_year[year]), year, conn)

    months = profile.phenology_months or list(range(SEASON[0], SEASON[1] + 1))
    pres_scores, bg_scores = [], []
    cache: dict[tuple[str, int], list] = {}
    for cid, d in presences:
        daily = cache.setdefault((cid, d.year), read_series(cid, d.year, conn))
        if not daily:
            continue
        s = score_at(profile, daily, d, con_umidita)
        if s is None:
            continue
        pres_scores.append(s)
        candidati = [row[0] for row in daily
                     if row[0].month in months and abs((row[0] - d).days) > GUARD_DAYS
                     and row[0] >= daily[0][0] + timedelta(days=20)]
        for bd in rng.sample(candidati, min(BG_PER_PRESENCE, len(candidati))):
            b = score_at(profile, daily, bd, con_umidita)
            if b is not None:
                bg_scores.append(b)
    if len(pres_scores) < 2 or len(bg_scores) < 2:
        return {"n_pres": len(pres_scores), "n_bg": len(bg_scores), "boyce": float("nan")}
    r = boyce.continuous_boyce(pres_scores, bg_scores)
    med_p = sorted(pres_scores)[len(pres_scores) // 2]
    med_b = sorted(bg_scores)[len(bg_scores) // 2]
    return {"n_pres": len(pres_scores), "n_bg": len(bg_scores), "boyce": r["boyce"],
            "mediana_pres": med_p, "mediana_bg": med_b}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    con_umidita = "--senza-umidita" not in sys.argv
    reg = load_profiles()
    pres = load_presences(reg)
    conn = connect()
    rng = random.Random(0)

    sids = args or sorted(s for s in pres if len(pres[s]) >= MIN_PRESENCES)
    print("Asse DINAMICO — Boyce temporale (background = stessa cella, altre date)")
    print(f"meteo: ERA5 ~25 km (la produzione gira ICON-D2 ~2 km) | gate umidità: "
          f"{'ACCESO' if con_umidita else 'spento, vedi docstring'}\n")
    print(f"{'specie':22s} {'presenze':>9s} {'background':>11s} {'Boyce':>7s} "
          f"{'med.pres':>9s} {'med.bg':>7s}")
    for sid in sids:
        if sid not in pres:
            print(f"{sid:22s} nessuna presenza datata")
            continue
        print(f"  … {sid}", flush=True)
        r = validate_species(reg[sid], pres[sid], conn, con_umidita, rng)
        nota = "" if r["n_pres"] >= MIN_PRESENCES else "  (pochi punti: rumore)"
        mp = f"{r.get('mediana_pres', float('nan')):9.3f}"
        mb = f"{r.get('mediana_bg', float('nan')):7.3f}"
        print(f"{sid:22s} {r['n_pres']:9d} {r['n_bg']:11d} {r['boyce']:7.3f} {mp} {mb}{nota}")
    conn.close()


if __name__ == "__main__":
    main()
