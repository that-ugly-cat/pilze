-- Schema osservazioni (spec §6.1). SQLite (v1); migrabile a PostGIS (§9).
-- I due cell_id (statico fine + meteo 2.2 km) sono NULL alla cattura e assegnati
-- dopo, quando la griglia comune esiste (spec §9): il bot NON dipende dal GIS.

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_submit       TEXT NOT NULL,          -- ISO8601, ora di invio (offline Telegram accoda)
    user_id         INTEGER,                -- chi ha loggato (privacy: coordinate = dati sensibili)
    lat             REAL,                   -- da location share (NON dall'EXIF della foto: strippato)
    lon             REAL,

    species         TEXT,                   -- id profilo; NULL se uscita a vuoto generica
    target_species  TEXT,                   -- specie cercata in un'uscita a vuoto mirata
    is_blank        INTEGER NOT NULL DEFAULT 0,  -- 1 = uscita a vuoto (nessun fungo)

    phase           TEXT,                   -- {primordi, buono, vecchio}
    old_reason      TEXT,                   -- {senescente, abortito} se phase=vecchio
    abundance       TEXT,                   -- {uno, pochi, molti} — per i non raccolti
    weight_g        REAL,                   -- solo raccolti
    effort_min      INTEGER,                -- minuti di ricerca (uscite a vuoto)

    photo_file_id   TEXT,                   -- file_id Telegram (archivio/ricontrollo)
    id_verified     INTEGER NOT NULL DEFAULT 1,   -- ID manuale sul campo = affidabile

    static_cell_id  TEXT,                   -- assegnato a posteriori
    meteo_cell_id   TEXT                    -- assegnato a posteriori
);

CREATE INDEX IF NOT EXISTS idx_obs_species ON observations(species);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts_submit);
