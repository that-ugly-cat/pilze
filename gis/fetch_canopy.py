"""Genera i raster canopy_alive componendo Sentinel-2 (spec §3.1), via gis.canopy.

Per l'AOI conifere (dove la staleness Vaia/bostrico morde) compone l'ultima estate
e scrive tile GeoTIFF canopy_alive che il CanopyProvider legge. Tiling per non far
esplodere la memoria. Resumable: salta i tile già presenti.

    python -m gis.fetch_canopy            # core conifere TN orientale (Fiemme/Fassa/Primiero)
"""

from __future__ import annotations

import sys
import warnings

from pathlib import Path

from . import canopy

CANOPY_DIR = Path(__file__).resolve().parent.parent / "data" / "canopy"
# core conifere TN orientale — cuore Vaia + bostrico (Fiemme, Fassa, Primiero, Lagorai)
CORE_BBOX = [11.35, 46.15, 12.00, 46.55]
DT = "2025-06-15/2025-09-20"


def generate_tile(bbox, out_path: Path, dt: str = DT) -> int:
    ndvi, nbr, n = canopy.composite(bbox, dt, max_cloud=30)
    if n == 0:
        return 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ca = canopy.canopy_alive(ndvi, nbr).rio.write_crs(ndvi.rio.crs)
        ca.rio.to_raster(out_path)
    return n


def _has_forest(tb, wc, n: int = 5) -> bool:
    import numpy as np
    for la in np.linspace(tb[1], tb[3], n):
        for lo in np.linspace(tb[0], tb[2], n):
            f = wc.features(float(la), float(lo))
            if f and f["forest_fraction"] > 0.15:
                return True
    return False


def generate_region(bbox, dt: str = DT, step: float = 0.20, out_dir: Path = CANOPY_DIR) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:                                          # skip dei tile senza bosco (pianura/roccia)
        from .providers import WorldCoverProvider
        wc = WorldCoverProvider()
    except Exception:
        wc = None
    lon0, lat0, lon1, lat1 = bbox
    done = skipped = 0
    lon = lon0
    while lon < lon1:
        lat = lat0
        while lat < lat1:
            tb = [lon, lat, min(lon + step, lon1), min(lat + step, lat1)]
            tag = f"{lon:.2f}_{lat:.2f}".replace("-", "m").replace(".", "p")
            out = out_dir / f"canopy_{tag}.tif"
            if out.exists():
                done += 1
            elif wc is not None and not _has_forest(tb, wc):
                skipped += 1                      # niente bosco → niente conifere da declassare
            else:
                print(f"  compongo {out.name} {tb} …", flush=True)
                try:
                    nn = generate_tile(tb, out)
                    print(f"    {'ok ('+str(nn)+' scene)' if nn else 'nessuna scena'}")
                    done += 1 if nn else 0
                except Exception as e:
                    print(f"    errore: {e}")
            lat += step
        lon += step
    print(f"tile con bosco: {done} | saltati (no bosco): {skipped}")
    return done


if __name__ == "__main__":
    import yaml
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config" / "grid.yaml",
                              encoding="utf-8"))["bbox_wgs84"]
    region = [cfg["lon_min"], cfg["lat_min"], cfg["lon_max"], cfg["lat_max"]]
    print(f"canopy_alive Sentinel-2 → {CANOPY_DIR} (bbox intero {region}, solo tile con bosco)")
    generate_region(region)
