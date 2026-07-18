"""Persistenza osservazioni — SQLite (spec §6.1, §9).

Il bot registra lat/lon grezzi + timestamp + specie/fase; i cell_id si assegnano
dopo (quando la griglia comune esiste). Nessuna dipendenza dal GIS in cattura.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "observations.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    conn = connect(db_path)
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()
    conn.close()


# Campi accettati da insert_observation (allineati allo schema)
_FIELDS = (
    "ts_submit", "user_id", "lat", "lon", "species", "target_species", "is_blank",
    "phase", "old_reason", "abundance", "weight_g", "effort_min",
    "photo_file_id", "id_verified", "static_cell_id", "meteo_cell_id",
)


def insert_observation(obs: dict, db_path: Path | str = DB_PATH) -> int:
    """Inserisce un'osservazione (dict con un sottoinsieme di _FIELDS). Ritorna l'id."""
    cols = [k for k in _FIELDS if k in obs]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO observations ({', '.join(cols)}) VALUES ({placeholders})"
    conn = connect(db_path)
    cur = conn.execute(sql, [obs[c] for c in cols])
    conn.commit()
    obs_id = cur.lastrowid
    conn.close()
    return obs_id


def all_observations(db_path: Path | str = DB_PATH) -> list[dict]:
    """Tutte le osservazioni con coordinate — per la mappa a pin (v1)."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT * FROM observations WHERE lat IS NOT NULL ORDER BY ts_submit DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"DB inizializzato: {DB_PATH}")
