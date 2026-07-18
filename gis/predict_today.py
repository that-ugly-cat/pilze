"""Predizione combinata (v3, spec §1): predizione = idoneità_statica × readiness_dinamica.

Per una specie, incrocia la mappa statica (celle candidate) con la readiness meteo
(dall'archivio del poller) della rispettiva cella meteo → celle "pronte oggi". È il
cuore del prodotto: DOVE (statico) × QUANDO (meteo). Output GeoJSON + statistiche.

    python -m gis.predict_today boletus_edulis
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

from engine.dynamic_scorer import readiness
from engine.profiles import load_profiles

from . import grid, meteo

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"


def predict(species: str, static_thr: float = 0.4, ready_thr: float = 0.3):
    reg = load_profiles()
    prof = reg[species]
    tif = MAPS_DIR / f"idoneita_{species}.tif"
    with rasterio.open(tif) as ds:
        a = ds.read(1); tr = ds.transform; crs = ds.crs
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    conn = meteo.connect()
    read_cache: dict[str, float] = {}                  # readiness per cella meteo (per specie)

    feats = []
    n_cand = n_ready = 0
    rows, cols = np.where(a > static_thr)
    for r, c in zip(rows.tolist(), cols.tolist()):
        n_cand += 1
        x = tr.c + (c + 0.5) * tr.a; y = tr.f + (r + 0.5) * tr.e
        lon, lat = to_wgs.transform(x, y)
        mcid = grid.assign(lat, lon)[1]
        if mcid not in read_cache:
            daily = meteo.read_daily(mcid, conn)
            read_cache[mcid] = readiness(prof, meteo.features_from_daily(prof, daily)) if daily else 0.0
        pred = float(a[r, c]) * read_cache[mcid]
        if pred > ready_thr:
            n_ready += 1
            feats.append({"type": "Feature",
                          "properties": {"pred": round(pred, 3), "idoneita": round(float(a[r, c]), 3),
                                         "readiness": round(read_cache[mcid], 3)},
                          "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}})
    conn.close()

    feats.sort(key=lambda f: -f["properties"]["pred"])
    out = MAPS_DIR / f"pronte_oggi_{species}.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats[:500]}), encoding="utf-8")
    maxr = max(read_cache.values()) if read_cache else 0.0
    print(f"{species}: candidate statiche {n_cand} | readiness max nelle celle meteo {maxr:.2f} | "
          f"PRONTE OGGI (pred>{ready_thr}) {n_ready}")
    if feats:
        top = feats[0]["properties"]
        print(f"  migliore: pred {top['pred']} (idoneità {top['idoneita']} × readiness {top['readiness']})")
    return n_ready


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sp = [a for a in sys.argv[1:] if not a.startswith("-")] or ["boletus_edulis"]
    for s in sp:
        predict(s)
