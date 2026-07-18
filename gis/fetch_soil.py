"""Scarica il pH del suolo per il bbox — SoilGrids (ISRIC), spec §3.1.

Sorgente uniforme globale (come il DEM): phh2o (pH in H2O ×10) a 250 m, via WCS.
CRS nativo: Interrupted Goode Homolosine (SoilGrids). Il GeoTIFF WCS porta il
geotransform ma NON la stringa CRS → la assegna il SoilProvider a lettura.

    python -m gis.fetch_soil

NB (spec §2): l'asse acido/calcareo NON separa le specie in due gruppi (quasi tutte
acidofile) → è un discriminatore debole, ma completa il fattore soil_ph dei profili.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import yaml
from pyproj import Transformer

SOIL_DIR = Path(__file__).resolve().parent.parent / "data" / "soil"
CONFIG = Path(__file__).resolve().parent.parent / "config" / "grid.yaml"
IGH = "+proj=igh +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
WCS = ("https://maps.isric.org/mapserv?map=/map/phh2o.map&SERVICE=WCS&VERSION=2.0.1"
       "&REQUEST=GetCoverage&COVERAGEID=phh2o_0-5cm_mean&FORMAT=image/tiff"
       "&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/152160")
COVERAGE = "phh2o_0-5cm_mean"
DEST = SOIL_DIR / "phh2o_0-5cm.tif"


def igh_bbox(cfg: dict | None = None, margin: float = 3000.0) -> tuple[int, int, int, int]:
    cfg = cfg or yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bb = cfg["bbox_wgs84"]
    t = Transformer.from_crs("EPSG:4326", IGH, always_xy=True)
    xs, ys = [], []
    for lon, lat in [(bb["lon_min"], bb["lat_min"]), (bb["lon_min"], bb["lat_max"]),
                     (bb["lon_max"], bb["lat_min"]), (bb["lon_max"], bb["lat_max"])]:
        x, y = t.transform(lon, lat)
        xs.append(x); ys.append(y)
    return (int(min(xs) - margin), int(max(xs) + margin),
            int(min(ys) - margin), int(max(ys) + margin))


def fetch(dest: Path = DEST) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  già presente: {dest.name}")
        return dest
    x0, x1, y0, y1 = igh_bbox()
    url = f"{WCS}&SUBSET=X({x0},{x1})&SUBSET=Y({y0},{y1})"
    print(f"  scarico {COVERAGE} IGH X({x0},{x1}) Y({y0},{y1}) …", flush=True)
    urllib.request.urlretrieve(url, dest)
    print(f"    ok ({dest.stat().st_size // 1024} KB)")
    return dest


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"SoilGrids phh2o 0-5cm → {SOIL_DIR}")
    fetch()
