"""Test della cattura: parsing della data, migrazione dello schema, giro completo sul DB."""

from datetime import date

import pytest


def test_parse_date_text_formati():
    pytest.importorskip("telegram")
    from bot.bot import parse_date_text
    oggi = date(2026, 9, 6)
    assert parse_date_text("3/9", oggi) == date(2026, 9, 3)
    assert parse_date_text("03/09/2026", oggi) == date(2026, 9, 3)
    assert parse_date_text("2026-09-03", oggi) == date(2026, 9, 3)
    assert parse_date_text("3-9", oggi) == date(2026, 9, 3)


def test_parse_date_text_senza_anno_non_salta_nel_futuro():
    """'28/12' scritto a gennaio è dicembre scorso, non fra undici mesi."""
    pytest.importorskip("telegram")
    from bot.bot import parse_date_text
    assert parse_date_text("28/12", date(2027, 1, 5)) == date(2026, 12, 28)


def test_parse_date_text_rifiuta_futuro_e_spazzatura():
    pytest.importorskip("telegram")
    from bot.bot import parse_date_text
    oggi = date(2026, 9, 6)
    assert parse_date_text("7/9", oggi) is None            # domani
    assert parse_date_text("ieri", oggi) is None
    assert parse_date_text("", oggi) is None
    assert parse_date_text("32/13", oggi) is None


def test_migrate_aggiunge_obs_date_a_un_db_vecchio(tmp_path):
    """Il DB di produzione esiste già: CREATE TABLE IF NOT EXISTS non porta le colonne nuove."""
    import sqlite3

    from bot import db
    p = tmp_path / "old.db"
    sqlite3.connect(p).executescript(
        "CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts_submit TEXT NOT NULL, lat REAL, lon REAL, is_blank INTEGER NOT NULL DEFAULT 0)")
    assert db.migrate(p) == ["obs_date"]
    assert db.migrate(p) == []                              # idempotente
    cols = {r["name"] for r in db.connect(p).execute("PRAGMA table_info(observations)")}
    assert "obs_date" in cols


def test_insert_e_rilettura_con_obs_date(tmp_path):
    from bot import db
    p = tmp_path / "obs.db"
    db.init_db(p)
    oid = db.insert_observation({"ts_submit": "2026-09-07T21:00:00+00:00",
                                 "obs_date": "2026-09-06", "lat": 46.13515, "lon": 11.68636,
                                 "species": "boletus_edulis", "is_blank": 0,
                                 "phase": "buono", "abundance": "tanti"}, p)
    rows = db.all_observations(p)
    assert len(rows) == 1 and rows[0]["id"] == oid
    # il giorno dell'uscita è distinto dall'invio: è quello che legge il learner
    assert rows[0]["obs_date"] == "2026-09-06"
    assert rows[0]["ts_submit"].startswith("2026-09-07")
