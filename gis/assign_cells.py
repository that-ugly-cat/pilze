"""Backfill dei cell_id sulle osservazioni del bot (spec §6.1, §9).

Il bot registra lat/lon grezzi; static_cell_id e meteo_cell_id restano NULL alla
cattura e si assegnano qui, quando la griglia comune esiste. Idempotente.

    python -m gis.assign_cells
"""

from __future__ import annotations

from bot import db
from . import grid


def backfill(db_path=db.DB_PATH) -> int:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, lat, lon FROM observations "
        "WHERE lat IS NOT NULL AND (static_cell_id IS NULL OR meteo_cell_id IS NULL)"
    ).fetchall()
    n = 0
    for r in rows:
        s, m = grid.assign(r["lat"], r["lon"])
        conn.execute("UPDATE observations SET static_cell_id=?, meteo_cell_id=? WHERE id=?",
                     (s, m, r["id"]))
        n += 1
    conn.commit()
    conn.close()
    return n


if __name__ == "__main__":
    print(f"Celle assegnate a {backfill()} osservazioni.")
