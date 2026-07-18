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


def generate_region(bbox, dt: str = DT, step: float = 0.20, out_dir: Path = CANOPY_DIR) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    lon0, lat0, lon1, lat1 = bbox
    done = 0
    lon = lon0
    while lon < lon1:
        lat = lat0
        while lat < lat1:
            tag = f"{lon:.2f}_{lat:.2f}".replace("-", "m").replace(".", "p")
            out = out_dir / f"canopy_{tag}.tif"
            if out.exists():
                print(f"  già presente: {out.name}"); done += 1
            else:
                tb = [lon, lat, min(lon + step, lon1), min(lat + step, lat1)]
                print(f"  compongo {out.name} {tb} …", flush=True)
                try:
                    n = generate_tile(tb, out)
                    print(f"    {'ok ('+str(n)+' scene)' if n else 'nessuna scena — saltato'}")
                    done += 1 if n else 0
                except Exception as e:
                    print(f"    errore: {e}")
            lat += step
        lon += step
    return done


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"canopy_alive Sentinel-2 → {CANOPY_DIR} (core conifere TN orientale)")
    n = generate_region(CORE_BBOX)
    print(f"tile canopy disponibili: {n}")
