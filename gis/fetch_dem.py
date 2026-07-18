"""Scarica il DEM che copre il bbox — Copernicus GLO-30 (AWS open data, no auth).

Scelta: un DEM NAZIONALE/globale uniforme invece di cucire i DTM regionali VE/TN
(specifiche e legende diverse a cavallo del confine). A griglia 100 m i 30 m di
Copernicus bastano; il LiDAR regionale fine resta un upgrade futuro (spec §3.1).

Tile 1°×1° GeoTIFF COG in EPSG:4326. ~30–50 MB l'uno.
    python -m gis.fetch_dem            # scarica tutti i tile del bbox
    python -m gis.fetch_dem N45 E011   # scarica solo i tile indicati
"""

from __future__ import annotations

import math
import sys
import urllib.request
from pathlib import Path

import yaml

DEM_DIR = Path(__file__).resolve().parent.parent / "data" / "dem"
BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
CONFIG = Path(__file__).resolve().parent.parent / "config" / "grid.yaml"


def _tile_name(lat_deg: int, lon_deg: int) -> str:
    ns = f"N{lat_deg:02d}" if lat_deg >= 0 else f"S{-lat_deg:02d}"
    ew = f"E{lon_deg:03d}" if lon_deg >= 0 else f"W{-lon_deg:03d}"
    return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"


def tiles_for_bbox(cfg: dict | None = None) -> list[tuple[int, int]]:
    cfg = cfg or yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bb = cfg["bbox_wgs84"]
    lat0, lat1 = math.floor(bb["lat_min"]), math.floor(bb["lat_max"])
    lon0, lon1 = math.floor(bb["lon_min"]), math.floor(bb["lon_max"])
    return [(la, lo) for la in range(lat0, lat1 + 1) for lo in range(lon0, lon1 + 1)]


def download_tile(lat_deg: int, lon_deg: int, dem_dir: Path = DEM_DIR) -> Path | None:
    dem_dir.mkdir(parents=True, exist_ok=True)
    name = _tile_name(lat_deg, lon_deg)
    dest = dem_dir / f"{name}.tif"
    if dest.exists():
        print(f"  già presente: {dest.name}")
        return dest
    url = f"{BASE}/{name}/{name}.tif"
    try:
        print(f"  scarico {name} …", flush=True)
        urllib.request.urlretrieve(url, dest)
        print(f"    ok ({dest.stat().st_size // (1024*1024)} MB)")
        return dest
    except Exception as e:  # tile mancante (mare) o rete
        print(f"    saltato ({e})")
        if dest.exists():
            dest.unlink()
        return None


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) >= 2:  # coppie es. "N45 E011"
        pairs = []
        for i in range(0, len(argv), 2):
            la = int(argv[i][1:]) * (1 if argv[i][0].upper() == "N" else -1)
            lo = int(argv[i + 1][1:]) * (1 if argv[i + 1][0].upper() == "E" else -1)
            pairs.append((la, lo))
    else:
        pairs = tiles_for_bbox()
    print(f"DEM Copernicus GLO-30 → {DEM_DIR} ({len(pairs)} tile):")
    got = [p for la, lo in pairs if (p := download_tile(la, lo))]
    print(f"Scaricati/presenti: {len(got)}/{len(pairs)} tile.")


if __name__ == "__main__":
    main(sys.argv[1:])
