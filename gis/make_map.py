"""Mappa di idoneità statica per specie (spec §8, v1) su griglia coarse.

Scora idoneità_statica su una griglia a passo grosso (default 500 m) con i provider
disponibili (DEM + forestale + canopy; **NO geologia REST** — non scala e martella il
server PAT). Efficienza: le feature della cella sono species-agnostic → interrogo i
provider UNA volta per cella e scoro tutte e 6 le specie. Output: un GeoTIFF float32
per specie + un GeoJSON top-K (per Leaflet).

    python -m gis.make_map            # 500 m su tutto il bbox VE+TN
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
import yaml
from pyproj import Transformer
from rasterio.transform import from_origin

from engine.profiles import load_profiles
from engine.static_scorer import static_suitability

from .providers import (CanopyProvider, CompositeFeatureProvider, DEMProvider,
                        ForestProvider, WorldCoverProvider)

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"
CONFIG = Path(__file__).resolve().parent.parent / "config" / "grid.yaml"
CRS = "EPSG:32632"


def build_provider():
    ps, active = [DEMProvider()], ["DEM"]
    try:                                          # CFI2020: forestale genere completo VE+TN+BZ
        ps.append(ForestProvider.cfi()); active.append("forestale-CFI")
    except FileNotFoundError:                     # fallback al patchwork VE/TN
        for name, ctor in [("VE", ForestProvider.veneto), ("TN", ForestProvider.trentino)]:
            try:
                ps.append(ctor()); active.append(f"forestale-{name}")
            except FileNotFoundError:
                pass
    try:
        ps.append(WorldCoverProvider()); active.append("worldcover-gate")
    except FileNotFoundError:
        pass
    try:
        ps.append(CanopyProvider()); active.append("canopy")
    except FileNotFoundError:
        pass
    return CompositeFeatureProvider(ps), active


def main(step_m: float = 500.0, only: list[str] | None = None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bb = cfg["bbox_wgs84"]
    reg = load_profiles()
    if only:
        reg = {k: v for k, v in reg.items() if k in only}
        print(f"solo specie: {', '.join(reg)}")
    provider, active = build_provider()
    print(f"layer attivi: {', '.join(active)}  |  passo {step_m:.0f} m")

    fwd = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    xs, ys = [], []
    for lon, lat in [(bb["lon_min"], bb["lat_min"]), (bb["lon_min"], bb["lat_max"]),
                     (bb["lon_max"], bb["lat_min"]), (bb["lon_max"], bb["lat_max"])]:
        x, y = fwd.transform(lon, lat); xs.append(x); ys.append(y)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    nx = int(math.ceil((x1 - x0) / step_m)); ny = int(math.ceil((y1 - y0) / step_m))
    transform = from_origin(x0, y1, step_m, step_m)
    print(f"griglia {nx} × {ny} = {nx*ny:,} celle")

    arrs = {sid: np.zeros((ny, nx), dtype="float32") for sid in reg}
    scored = 0
    for i in range(ny):
        yc = y1 - (i + 0.5) * step_m
        for j in range(nx):
            xc = x0 + (j + 0.5) * step_m
            lon, lat = inv.transform(xc, yc)
            cell = provider.features(lat, lon)
            if cell is None:
                continue
            scored += 1
            for sid, prof in reg.items():
                arrs[sid][i, j] = static_suitability(prof, cell)
        if (i + 1) % 50 == 0:
            print(f"  riga {i+1}/{ny}  (celle scorate: {scored:,})", flush=True)

    for sid, arr in arrs.items():
        with rasterio.open(OUT_DIR / f"idoneita_{sid}.tif", "w", driver="GTiff",
                           height=ny, width=nx, count=1, dtype="float32",
                           crs=CRS, transform=transform, nodata=0.0) as ds:
            ds.write(arr, 1)
        # top-K celle (per Leaflet)
        flat = arr.ravel()
        k = min(300, int((flat > 0.4).sum()))
        idx = np.argsort(flat)[::-1][:k]
        feats = []
        for f in idx:
            if flat[f] <= 0.4:
                break
            r, c = divmod(int(f), nx)
            lon, lat = inv.transform(x0 + (c + 0.5) * step_m, y1 - (r + 0.5) * step_m)
            feats.append({"type": "Feature", "properties": {"score": round(float(flat[f]), 3)},
                          "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}})
        (OUT_DIR / f"top_{sid}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
        print(f"  {sid:24s} max {float(arr.max()):.2f}  celle>0.4 {(arr>0.4).sum():5d}  top-K {len(feats)}")
    print(f"mappe → {OUT_DIR}")


if __name__ == "__main__":
    species = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(only=species or None)
