-- ============================================================
-- index.db schema — rebuildable cache of DB catalog
-- Not source-of-truth. Can be deleted and recreated.
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS db_catalog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    db_type     TEXT    NOT NULL,
    db_name     TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'ok',
    error_msg   TEXT,
    scanned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(db_type, db_name)
);

CREATE INDEX IF NOT EXISTS idx_catalog_type   ON db_catalog(db_type);
CREATE INDEX IF NOT EXISTS idx_catalog_status ON db_catalog(status);
