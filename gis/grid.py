"""Griglia comune (spec §9): CRS + passo unici, ancorati a UTM assoluto.

Ogni punto (lat, lon) → due cell_id deterministici:
  - statico fine (passo config, default 100 m) — per i pesi di idoneità
  - meteo (~2.2 km, maglia ICON-D2)          — per la serie dinamica
Sono geometrie diverse (spec §6.1): il doppio cell_id le tiene entrambe.

La griglia è ancorata all'origine UTM (0,0), non al bbox: due run danno gli stessi
id. Il bot registra lat/lon grezzi; qui si assegnano le celle a posteriori.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml
from pyproj import Transformer

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "grid.yaml"


@lru_cache(maxsize=1)
def _config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=2)
def _transformers(crs: str):
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)   # lon,lat -> x,y
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)   # x,y -> lon,lat
    return fwd, inv


def _cell_id(x: float, y: float, step: float, prefix: str) -> str:
    return f"{prefix}{int(step)}_{math.floor(x / step)}_{math.floor(y / step)}"


def assign(lat: float, lon: float, cfg: dict | None = None) -> tuple[str, str]:
    """(static_cell_id, meteo_cell_id) per un punto WGS84."""
    cfg = cfg or _config()
    fwd, _ = _transformers(cfg["crs"])
    x, y = fwd.transform(lon, lat)
    return (
        _cell_id(x, y, float(cfg["static_step_m"]), "s"),
        _cell_id(x, y, float(cfg["meteo_step_m"]), "m"),
    )


def cell_center(cell_id: str, cfg: dict | None = None) -> tuple[float, float]:
    """Centro (lat, lon) di una cella dal suo id (prefisso s/m + step + col + row)."""
    cfg = cfg or _config()
    _, inv = _transformers(cfg["crs"])
    prefix = cell_id[0]
    step_s, col_s, row_s = cell_id[1:].split("_")
    step, col, row = float(step_s), int(col_s), int(row_s)
    x = (col + 0.5) * step
    y = (row + 0.5) * step
    lon, lat = inv.transform(x, y)
    return lat, lon


def iter_static_cells(cfg: dict | None = None):
    """Genera (static_cell_id, lat_centro, lon_centro) su tutto il bbox di interesse.

    Serve al driver della mappa di idoneità (scora ogni cella per ogni specie).
    """
    cfg = cfg or _config()
    fwd, inv = _transformers(cfg["crs"])
    bb = cfg["bbox_wgs84"]
    step = float(cfg["static_step_m"])
    # proietta i 4 angoli e prendi il bounding box in UTM (copre la curvatura)
    xs, ys = [], []
    for lon, lat in [(bb["lon_min"], bb["lat_min"]), (bb["lon_min"], bb["lat_max"]),
                     (bb["lon_max"], bb["lat_min"]), (bb["lon_max"], bb["lat_max"])]:
        x, y = fwd.transform(lon, lat)
        xs.append(x); ys.append(y)
    col0, col1 = math.floor(min(xs) / step), math.floor(max(xs) / step)
    row0, row1 = math.floor(min(ys) / step), math.floor(max(ys) / step)
    for col in range(col0, col1 + 1):
        for row in range(row0, row1 + 1):
            x = (col + 0.5) * step
            y = (row + 0.5) * step
            lon, lat = inv.transform(x, y)
            yield f"s{int(step)}_{col}_{row}", lat, lon


def grid_dimensions(cfg: dict | None = None) -> tuple[int, int, int]:
    """(colonne, righe, celle_totali) della griglia statica sul bbox — per stimare il carico."""
    cfg = cfg or _config()
    fwd, _ = _transformers(cfg["crs"])
    bb = cfg["bbox_wgs84"]
    step = float(cfg["static_step_m"])
    xs, ys = [], []
    for lon, lat in [(bb["lon_min"], bb["lat_min"]), (bb["lon_max"], bb["lat_max"])]:
        x, y = fwd.transform(lon, lat)
        xs.append(x); ys.append(y)
    ncol = int(abs(xs[1] - xs[0]) / step) + 1
    nrow = int(abs(ys[1] - ys[0]) / step) + 1
    return ncol, nrow, ncol * nrow


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ncol, nrow, tot = grid_dimensions()
    print(f"Griglia statica sul bbox: {ncol} × {nrow} = {tot:,} celle "
          f"(passo {_config()['static_step_m']} m, CRS {_config()['crs']})")
    for lat, lon, label in [(45.86, 11.77, "Colli Berici ~"), (46.30, 11.60, "Fiemme ~")]:
        s, m = assign(lat, lon)
        clat, clon = cell_center(s)
        print(f"  ({lat},{lon}) {label:14s} -> static={s} meteo={m}; centro=({clat:.4f},{clon:.4f})")
