"""Scarica ESA WorldCover 10 m (AWS open data, no auth) — maschera forestale completa.

Serve come GATE "è bosco?" a copertura completa (§3.1): i layer forestali genere (VE
completo, TN parziale) danno la composizione ma non la presenza ovunque. La classe
10 = *tree cover*. Tile 3°×3° GeoTIFF COG in EPSG:4326.

    python -m gis.fetch_worldcover
"""

from __future__ import annotations

import math
import sys
import urllib.request
from pathlib import Path

import yaml

WC_DIR = Path(__file__).resolve().parent.parent / "data" / "worldcover"
BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
CONFIG = Path(__file__).resolve().parent.parent / "config" / "grid.yaml"


def _tile_name(lat3: int, lon3: int) -> str:
    ns = f"N{lat3:02d}" if lat3 >= 0 else f"S{-lat3:02d}"
    ew = f"E{lon3:03d}" if lon3 >= 0 else f"W{-lon3:03d}"
    return f"ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif"


def tiles_for_bbox(cfg: dict | None = None) -> list[tuple[int, int]]:
    cfg = cfg or yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bb = cfg["bbox_wgs84"]
    lat0 = int(math.floor(bb["lat_min"] / 3) * 3); lat1 = int(math.floor(bb["lat_max"] / 3) * 3)
    lon0 = int(math.floor(bb["lon_min"] / 3) * 3); lon1 = int(math.floor(bb["lon_max"] / 3) * 3)
    return [(la, lo) for la in range(lat0, lat1 + 1, 3) for lo in range(lon0, lon1 + 1, 3)]


def download_tile(lat3: int, lon3: int, wc_dir: Path = WC_DIR) -> Path | None:
    wc_dir.mkdir(parents=True, exist_ok=True)
    name = _tile_name(lat3, lon3)
    dest = wc_dir / name
    if dest.exists():
        print(f"  già presente: {name}"); return dest
    try:
        print(f"  scarico {name} …", flush=True)
        urllib.request.urlretrieve(f"{BASE}/{name}", dest)
        print(f"    ok ({dest.stat().st_size // (1024*1024)} MB)")
        return dest
    except Exception as e:
        print(f"    saltato ({e})")
        if dest.exists():
            dest.unlink()
        return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pairs = tiles_for_bbox()
    print(f"ESA WorldCover → {WC_DIR} ({len(pairs)} tile):")
    got = [p for la, lo in pairs if (p := download_tile(la, lo))]
    print(f"Tile disponibili: {len(got)}/{len(pairs)}")
