"""Poller meteo ICON-D2 (spec §3.2, §9) — archivia in avanti per le celle candidate.

In produzione gira ogni giorno (systemd timer su VPS): niente backfill possibile per
ICON-D2, l'archivio si costruisce in avanti → un poll perso = buco. In dev si fa un
backfill iniziale con past_days (Open-Meteo lo permette). Gap-detector incluso (§9).

    python -m gis.fetch_meteo                 # backfill iniziale sulle celle candidate edulis
    python -m gis.fetch_meteo --daily         # poll giornaliero (past_days=3, per il VPS)
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

from . import grid, meteo

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"

# Batching: Open-Meteo accetta più località per richiesta → ~45 richieste invece di
# ~4500 (una per cella). BATCH = località/richiesta; PACE_S = pausa fra richieste
# (cortesia anti-burst). Con questi valori il poll dell'intera union chiude in ~1 min.
BATCH = int(os.environ.get("PILZE_METEO_BATCH", "100"))
PACE_S = float(os.environ.get("PILZE_METEO_PACE_S", "1.0"))


def all_species() -> list[str]:
    """Specie con una mappa idoneità presente. Enumerarle così = una nuova specie
    entra nel poll da sola appena la sua mappa è generata (niente lista hardcoded)."""
    return sorted(p.stem.replace("idoneita_", "") for p in MAPS_DIR.glob("idoneita_*.tif"))


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


def _fetch_with_backoff(coords, past_days: int, tries: int = 6):
    """fetch_series_batch con retry esponenziale sul 429. Open-Meteo pesa il rate limit
    per località×giorni: il backfill (35 gg × 100 loc) lo prende. Onora Retry-After se
    presente. Ritorna la lista di serie, o None se esaurisce i tentativi."""
    delay = 5.0
    for attempt in range(tries):
        try:
            return meteo.fetch_series_batch(coords, past_days=past_days, forecast_days=1)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                print(f"    blocco fallito: {e}")
                return None
            ra = e.headers.get("Retry-After") if e.headers else None
            wait = float(ra) if (ra and str(ra).isdigit()) else delay
            print(f"    429 rate-limit: attendo {wait:.0f}s (tentativo {attempt + 1}/{tries})", flush=True)
            time.sleep(wait)
            delay = min(delay * 2, 120)
        except Exception as e:
            print(f"    errore blocco: {e}")
            return None
    return None


def poll(cells, past_days: int = 3, batch: int = BATCH, pace_s: float = 0.0) -> int:
    """Archivia le celle in blocchi: una richiesta Open-Meteo multi-località per blocco,
    con retry/backoff sul 429 (il backfill a 35 gg è pesante e lo prende)."""
    conn = meteo.connect()
    n = 0
    total = len(cells)
    for start in range(0, total, batch):
        chunk = cells[start:start + batch]
        series_list = _fetch_with_backoff([(la, lo) for _, la, lo in chunk], past_days)
        if series_list is None:
            print(f"  blocco {start}-{start + len(chunk)} saltato")
        else:
            if len(series_list) != len(chunk):
                print(f"  ⚠ blocco {start}: attese {len(chunk)}, ricevute {len(series_list)}")
            for (cid, _, _), series in zip(chunk, series_list):
                meteo.upsert_daily(cid, meteo._daily(series), conn)
                n += 1
        print(f"  {min(start + batch, total)}/{total} celle…", flush=True)
        if pace_s and start + batch < total:
            time.sleep(pace_s)                 # cortesia anti-burst fra richieste
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
    sp = [a for a in argv if not a.startswith("-")] or all_species()
    tifs = [MAPS_DIR / f"idoneita_{s}.tif" for s in sp]
    cells_map = {}
    for tif in tifs:
        if not tif.exists():
            raise SystemExit(f"Manca {tif.name} (python -m gis.make_map).")
        # union su TUTTE le specie: cella meteo candidata se una qualsiasi specie ha
        # idoneità > 0.4 lì. Nessun cap numerico → tutto il bosco d'interesse VE+TN.
        for cid, la, lo in candidate_meteo_cells(tif):
            cells_map[cid] = (la, lo)                           # dedup fra specie
    cells = [(cid, la, lo) for cid, (la, lo) in cells_map.items()]
    n_batches = (len(cells) + BATCH - 1) // BATCH
    print(f"specie: {', '.join(sp)}")
    print(f"celle meteo candidate: {len(cells)}  |  {'poll giornaliero' if daily else 'backfill iniziale'}"
          f"  |  batch {BATCH} → {n_batches} richieste")
    n = poll(cells, past_days=3 if daily else 35, pace_s=PACE_S)
    print(f"archiviate: {n}/{len(cells)} celle")
    gap_report(cells)


if __name__ == "__main__":
    main(sys.argv[1:])
