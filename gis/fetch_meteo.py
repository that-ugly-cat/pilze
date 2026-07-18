"""Poller meteo ICON-D2 (spec §3.2, §9) — archivia in avanti per le celle candidate.

In produzione gira ogni giorno (systemd timer su VPS): niente backfill possibile per
ICON-D2, l'archivio si costruisce in avanti → un poll perso = buco. In dev si fa un
backfill iniziale con past_days (Open-Meteo lo permette). Gap-detector incluso (§9).

    python -m gis.fetch_meteo                 # backfill iniziale sulle celle candidate edulis
    python -m gis.fetch_meteo --daily         # poll giornaliero (past_days=3, per il VPS)
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

from . import grid, meteo

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"


def candidate_meteo_cells(species_tif: Path, threshold: float = 0.4,
                          max_cells: int | None = None) -> list[tuple[str, float, float]]:
    """Celle meteo (~2.2 km) distinte che contengono celle statiche candidate (>threshold)."""
    with rasterio.open(species_tif) as ds:
        a = ds.read(1); inv = ~ds.transform
        crs = ds.crs
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    ny, nx = a.shape
    fwd = rasterio.open(species_tif).transform
    seen: dict[str, tuple[float, float]] = {}
    rows, cols = np.where(a > threshold)
    order = np.argsort(a[rows, cols])[::-1]           # dai candidati migliori
    for k in order:
        r, c = int(rows[k]), int(cols[k])
        x = fwd.c + (c + 0.5) * fwd.a; y = fwd.f + (r + 0.5) * fwd.e
        lon, lat = to_wgs.transform(x, y)
        mcid = grid.assign(lat, lon)[1]
        if mcid not in seen:
            clat, clon = grid.cell_center(mcid)
            seen[mcid] = (clat, clon)
            if max_cells and len(seen) >= max_cells:
                break
    return [(cid, la, lo) for cid, (la, lo) in seen.items()]


def poll(cells, past_days: int = 3) -> int:
    conn = meteo.connect()
    n = 0
    for cid, lat, lon in cells:
        try:
            series = meteo.fetch_series(lat, lon, past_days=past_days, forecast_days=1)
            meteo.upsert_daily(cid, meteo._daily(series), conn)
            n += 1
        except Exception as e:
            print(f"  cella {cid} fallita: {e}")
    conn.close()
    return n


def gap_report(cells) -> None:
    """Alert §9: celle senza dati recenti (poll perso = buco permanente)."""
    conn = meteo.connect()
    yesterday = date.today() - timedelta(days=1)
    stale = []
    for cid, _, _ in cells:
        rows = meteo.read_daily(cid, conn)
        if not rows or rows[-1][0] < yesterday:
            stale.append(cid)
    conn.close()
    if stale:
        print(f"  ⚠ GAP: {len(stale)} celle senza dati fino a ieri (poll perso?)")
    else:
        print("  archivio aggiornato, nessun buco")


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    daily = "--daily" in argv
    sp = [a for a in argv if not a.startswith("-")] or ["boletus_edulis"]
    tifs = [MAPS_DIR / f"idoneita_{s}.tif" for s in sp]
    cells_map = {}
    for tif in tifs:
        if not tif.exists():
            raise SystemExit(f"Manca {tif.name} (python -m gis.make_map).")
        for cid, la, lo in candidate_meteo_cells(tif, max_cells=120):
            cells_map[cid] = (la, lo)                           # union fra specie, dedup
    cells = [(cid, la, lo) for cid, (la, lo) in cells_map.items()]
    print(f"celle meteo candidate: {len(cells)}  |  {'poll giornaliero' if daily else 'backfill iniziale'}")
    n = poll(cells, past_days=3 if daily else 35)
    print(f"archiviate: {n}/{len(cells)} celle")
    gap_report(cells)


if __name__ == "__main__":
    main(sys.argv[1:])
