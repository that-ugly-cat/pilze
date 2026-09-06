"""Test della persistenza osservazioni: migrazione dello schema e giro completo sul DB.

Il pacchetto si chiama ancora `bot/` per ragioni storiche — la cattura era un bot
Telegram, ora è il form della web app — e qui dentro resta solo il layer di persistenza.
"""


def test_migrate_aggiunge_le_colonne_a_un_db_vecchio(tmp_path):
    """Il DB di produzione esiste già: CREATE TABLE IF NOT EXISTS non porta le colonne nuove."""
    import sqlite3

    from bot import db
    p = tmp_path / "old.db"
    sqlite3.connect(p).executescript(
        "CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts_submit TEXT NOT NULL, lat REAL, lon REAL, is_blank INTEGER NOT NULL DEFAULT 0)")
    assert db.migrate(p) == ["obs_date", "logged_by"]
    assert db.migrate(p) == []                              # idempotente
    cols = {r["name"] for r in db.connect(p).execute("PRAGMA table_info(observations)")}
    assert {"obs_date", "logged_by"} <= cols


def test_insert_e_rilettura_con_obs_date(tmp_path):
    from bot import db
    p = tmp_path / "obs.db"
    db.init_db(p)
    oid = db.insert_observation({"ts_submit": "2026-09-07T21:00:00+00:00",
                                 "obs_date": "2026-09-06", "logged_by": "spit",
                                 "lat": 46.13515, "lon": 11.68636,
                                 "species": "boletus_edulis", "is_blank": 0,
                                 "phase": "buono", "abundance": "tanti"}, p)
    rows = db.all_observations(p)
    assert len(rows) == 1 and rows[0]["id"] == oid
    # il giorno dell'uscita è distinto dall'invio: è quello che legge il learner
    assert rows[0]["obs_date"] == "2026-09-06"
    assert rows[0]["ts_submit"].startswith("2026-09-07")
    assert rows[0]["logged_by"] == "spit"


def test_uscita_a_vuoto_mirata(tmp_path):
    """Il vuoto mirato tiene la specie cercata: è uno zero forte sul timing (§6.1)."""
    from bot import db
    p = tmp_path / "obs.db"
    db.init_db(p)
    db.insert_observation({"ts_submit": "2026-09-06T18:00:00+00:00", "obs_date": "2026-09-06",
                           "lat": 46.1, "lon": 11.6, "is_blank": 1,
                           "target_species": "boletus_edulis", "effort_min": 150}, p)
    row = db.all_observations(p)[0]
    assert row["is_blank"] == 1 and row["species"] is None
    assert row["target_species"] == "boletus_edulis" and row["effort_min"] == 150
