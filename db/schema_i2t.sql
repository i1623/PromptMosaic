-- ============================================================
-- i2t.db schema — image-to-text analysis history
-- Cross-DB references use (history_db_name, history_id).
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS i2t_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path      TEXT    NOT NULL,
    image_thumb     BLOB,
    result_text     TEXT    NOT NULL DEFAULT '',
    template_name   TEXT    DEFAULT '',
    model_name      TEXT    DEFAULT '',
    history_db_name TEXT,
    history_id      INTEGER,
    sent_at         TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_i2t_history_created ON i2t_history(created_at);
