"""Asse dinamico (spec §3.2, §4) — meteo ICON-D2 via Open-Meteo → feature → readiness.

Sorgente unica ICON-D2 (DWD), per partire via Open-Meteo (no GRIB, no key). In dev si
usa `past_days` per backfillare subito la storia e testare le feature senza aspettare il
cold-start; in produzione il poller archivia in avanti (fetch_meteo, TODO). Le variabili
prognostiche di **suolo** (umidità/temperatura) integrano già l'antecedente → utili dal
giorno 1. Feature (§4): pioggia cumulata su finestra, umidità/temperatura suolo, shock
termico (sul SUOLO, §5), giorni-dall'ultimo-trigger.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime

import json

API = "https://api.open-meteo.com/v1/dwd-icon"
HOURLY = ["precipitation", "soil_temperature_6cm", "soil_moisture_3_to_9cm", "temperature_2m"]
RAIN_TRIGGER_MM = 10.0        # pioggia giornaliera che conta come "trigger" di flush


def _parse_hourly(h: dict) -> dict:
    return {"time": [datetime.fromisoformat(t) for t in h["time"]],
            "precip": h["precipitation"], "soil_temp": h["soil_temperature_6cm"],
            "soil_moist": h["soil_moisture_3_to_9cm"], "temp": h["temperature_2m"]}


def fetch_series_batch(coords: list[tuple[float, float]], past_days: int = 35,
                       forecast_days: int = 2) -> list[dict]:
    """Serie orarie per N località in UNA richiesta (Open-Meteo accetta coordinate
    separate da virgola e ritorna un array nello stesso ordine). Riduce le richieste
    HTTP di ~100× rispetto a una-per-cella. Ritorna la lista allineata a `coords`."""
    lats = ",".join(f"{la:.5f}" for la, lo in coords)
    lons = ",".join(f"{lo:.5f}" for la, lo in coords)
    p = urllib.parse.urlencode({"latitude": lats, "longitude": lons, "hourly": ",".join(HOURLY),
                                "past_days": past_days, "forecast_days": forecast_days,
                                "timezone": "Europe/Rome"})
    raw = json.loads(urllib.request.urlopen(f"{API}?{p}", timeout=120).read())
    items = raw if isinstance(raw, list) else [raw]    # 1 sola coord → oggetto singolo
    return [_parse_hourly(d["hourly"]) for d in items]


def fetch_series(lat: float, lon: float, past_days: int = 35, forecast_days: int = 2) -> dict:
    """Serie oraria per una località (wrapper su fetch_series_batch)."""
    return fetch_series_batch([(lat, lon)], past_days, forecast_days)[0]


def _daily(series: dict, until: datetime | None = None):
    """Aggrega l'orario in giornaliero fino a `until` (default: ultima ora osservata)."""
    until = until or series["time"][-1]
    rain = defaultdict(float); st = defaultdict(list); sm = defaultdict(list)
    for i, t in enumerate(series["time"]):
        if t > until:
            break
        d = t.date()
        if series["precip"][i] is not None:
            rain[d] += series["precip"][i]
        if series["soil_temp"][i] is not None:
            st[d].append(series["soil_temp"][i])
        if series["soil_moist"][i] is not None:
            sm[d].append(series["soil_moist"][i])
    days = sorted(rain)
    return [(d, rain[d], (sum(st[d]) / len(st[d]) if st[d] else None),
             (sum(sm[d]) / len(sm[d]) if sm[d] else None)) for d in days]


def features_for(profile, series: dict, ref: datetime | None = None) -> dict:
    """Feature §4 dal fetch live (serie oraria) → delega a features_from_daily."""
    return features_from_daily(profile, _daily(series, ref))


def features_from_daily(profile, daily: list) -> dict:
    """Feature §4 per il dynamic_scorer da righe giornaliere (date, rain, soil_temp, soil_moist),
    tarate sulle finestre del profilo. Stessa logica per fetch live e per archivio."""
    if not daily:
        return {}
    now = daily[-1]
    win = int(profile.dynamic_triggers.get("rain_window_days", 15))
    cum_rain = sum(d[1] for d in daily[-win:])
    soil_temp_now = next((d[2] for d in reversed(daily) if d[2] is not None), None)
    soil_moist_now = next((d[3] for d in reversed(daily) if d[3] is not None), None)
    # shock termico sul SUOLO (§5): calo dal massimo recente (10 gg) a ora
    recent_st = [d[2] for d in daily[-10:] if d[2] is not None]
    thermal_shock = max(0.0, max(recent_st) - soil_temp_now) if recent_st and soil_temp_now else 0.0
    # giorni dall'ultimo trigger di pioggia
    dst = None
    for k, d in enumerate(reversed(daily)):
        if d[1] >= RAIN_TRIGGER_MM:
            dst = k; break
    return {"month": now[0].month, "cumulative_rain_mm": round(cum_rain, 1),
            "soil_moisture": round(soil_moist_now, 3) if soil_moist_now is not None else None,
            "soil_temp_c": round(soil_temp_now, 1) if soil_temp_now is not None else None,
            "thermal_shock_c": round(thermal_shock, 1),
            "days_since_trigger": dst}


# --- Archivio (spec §3.2, §9): si costruisce in avanti col poller (fetch_meteo) ---
from pathlib import Path  # noqa: E402
import sqlite3  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "meteo.db"


def connect(db_path=DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS meteo (
        meteo_cell_id TEXT, date TEXT, precip_mm REAL, soil_temp_c REAL, soil_moist REAL,
        PRIMARY KEY (meteo_cell_id, date))""")
    return conn


def upsert_daily(cell_id: str, daily: list, conn: sqlite3.Connection) -> int:
    rows = [(cell_id, d.isoformat(), rain, st, sm) for d, rain, st, sm in daily]
    conn.executemany("INSERT OR REPLACE INTO meteo VALUES (?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def read_daily(cell_id: str, conn: sqlite3.Connection) -> list:
    cur = conn.execute("SELECT date, precip_mm, soil_temp_c, soil_moist FROM meteo "
                       "WHERE meteo_cell_id=? ORDER BY date", (cell_id,))
    return [(date.fromisoformat(d), r, st, sm) for d, r, st, sm in cur.fetchall()]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from engine.profiles import load_profiles
    from engine.dynamic_scorer import readiness

    reg = load_profiles()
    lat, lon = 46.30, 11.76      # Paneveggio
    print(f"ICON-D2 (Open-Meteo) su ({lat},{lon}) — ultima analisi:")
    series = fetch_series(lat, lon)
    for sid in ["boletus_edulis", "boletus_aestivalis", "cantharellus_cibarius"]:
        prof = reg[sid]
        feat = features_for(prof, series)
        r = readiness(prof, feat)
        print(f"  {sid:22s} feat={feat}")
        print(f"  {'':22s} readiness = {r:.2f}")
