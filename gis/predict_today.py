"""Fase della buttata per cella meteo (viz "pronte oggi").

Per una specie, per ogni cella meteo (~2.2 km) che contiene habitat idoneo (idoneità
statica > soglia) calcola la FASE dal readiness dell'archivio poller: in_fieri / pronto /
tardi (engine.readiness_state, da days_since_trigger vs lag_days). Output: GeoJSON di
QUADRATI 2.2 km con lo stato — overlay del "quando" sopra l'idoneità (il "dove").

    python -m gis.predict_today            # tutte le specie con una mappa
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

from engine.dynamic_scorer import readiness, readiness_state
from engine.profiles import load_profiles

from . import grid, meteo

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"


def predict(species: str, static_thr: float = 0.4):
    reg = load_profiles()
    prof = reg[species]
    tif = MAPS_DIR / f"idoneita_{species}.tif"
    with rasterio.open(tif) as ds:
        a = ds.read(1); tr = ds.transform; crs = ds.crs
    cfg = grid._config()
    mstep = float(cfg["meteo_step_m"])
    # il tif è nel CRS della griglia (UTM 32N) → mcid direttamente dai metri, senza transform
    same = str(crs).upper().endswith(cfg["crs"].split(":")[-1])
    to_grid = None if same else Transformer.from_crs(crs, cfg["crs"], always_xy=True)

    # celle meteo candidate: contengono almeno una cella statica con idoneità > soglia
    cand: set[tuple[int, int]] = set()
    rows, cols = np.where(a > static_thr)
    for r, c in zip(rows.tolist(), cols.tolist()):
        x = tr.c + (c + 0.5) * tr.a; y = tr.f + (r + 0.5) * tr.e
        if to_grid:
            x, y = to_grid.transform(x, y)
        cand.add((math.floor(x / mstep), math.floor(y / mstep)))

    conn = meteo.connect()
    feats = []
    counts = {"in_fieri": 0, "pronto": 0, "tardi": 0}
    for col, row in cand:
        mcid = f"m{int(mstep)}_{col}_{row}"
        daily = meteo.read_daily(mcid, conn)
        if not daily:
            continue                                   # cella non nell'archivio (non pollata)
        st = readiness_state(prof, meteo.features_from_daily(prof, daily))
        if not st["state"]:
            continue
        counts[st["state"]] += 1
        props = {k: st[k] for k in ("state", "readiness", "charge", "dst", "eta", "days_past") if k in st}
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Polygon", "coordinates": [grid.cell_polygon(mcid)]}})
    conn.close()

    out = MAPS_DIR / f"pronte_oggi_{species}.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print(f"{species}: celle meteo candidate {len(cand)}  |  "
          f"in_fieri {counts['in_fieri']} · pronto {counts['pronto']} · tardi {counts['tardi']}")
    return counts


def top_spots(species: str, mode: str = "both", k: int = 50, static_thr: float = 0.4):
    """Top-k spot per una specie. mode: 'static' (idoneità), 'dynamic' (readiness della
    cella meteo), 'both' (prodotto). Ritorna [{lat, lon, idoneita, readiness, score}] ordinati."""
    import heapq
    prof = load_profiles()[species]
    tif = MAPS_DIR / f"idoneita_{species}.tif"
    if not tif.exists():
        return []
    with rasterio.open(tif) as ds:
        a = ds.read(1); tr = ds.transform; crs = ds.crs
    cfg = grid._config(); mstep = float(cfg["meteo_step_m"])
    same = str(crs).upper().endswith(cfg["crs"].split(":")[-1])
    to_grid = None if same else Transformer.from_crs(crs, cfg["crs"], always_xy=True)
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    need_dyn = mode in ("dynamic", "both")
    conn = meteo.connect() if need_dyn else None
    read_cache: dict[str, float] = {}

    def readiness_at(gx, gy):                       # gx,gy già in metri della griglia
        mcid = f"m{int(mstep)}_{math.floor(gx / mstep)}_{math.floor(gy / mstep)}"
        if mcid not in read_cache:
            daily = meteo.read_daily(mcid, conn)
            read_cache[mcid] = readiness(prof, meteo.features_from_daily(prof, daily)) if daily else 0.0
        return read_cache[mcid]

    dedup_m = 1500.0                               # ≤ 1 spot per ~1.5 km → spot distinti
    best: dict = {}                                # coarse-cell → miglior candidato
    rows, cols = np.where(a > static_thr)
    for r, c in zip(rows.tolist(), cols.tolist()):
        ido = float(a[r, c])
        x = tr.c + (c + 0.5) * tr.a; y = tr.f + (r + 0.5) * tr.e
        gx, gy = to_grid.transform(x, y) if to_grid else (x, y)   # metri nella griglia
        rd = readiness_at(gx, gy) if need_dyn else 1.0
        score = ido if mode == "static" else (rd if mode == "dynamic" else ido * rd)
        if score <= 0:
            continue
        key = (math.floor(gx / dedup_m), math.floor(gy / dedup_m))
        item = (score, ido, rd, x, y)
        if key not in best or item > best[key]:
            best[key] = item
    if conn:
        conn.close()

    out = []
    for score, ido, rd, x, y in heapq.nlargest(k, best.values()):
        lon, lat = to_wgs.transform(x, y)
        out.append({"lat": round(lat, 5), "lon": round(lon, 5), "idoneita": round(ido, 3),
                    "readiness": round(rd, 3), "score": round(score, 3)})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sp = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not sp:                       # default: tutte le specie con una mappa presente
        sp = sorted(p.stem.replace("idoneita_", "") for p in MAPS_DIR.glob("idoneita_*.tif"))
    for s in sp:
        predict(s)
