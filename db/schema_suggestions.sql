-- ============================================================
-- suggestions.db schema — cross-library suggestion cache
-- Not source-of-truth. Can be deleted and recreated.
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS suggestions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_library_db     TEXT    NOT NULL,
    source_table          TEXT    NOT NULL DEFAULT 'tags',
    source_id             INTEGER NOT NULL,
    kind                  TEXT    NOT NULL DEFAULT 'tag',
    text                  TEXT    NOT NULL,
    translated_text       TEXT,
    display_label         TEXT,
    normalized_text       TEXT,
    sort_key              TEXT,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_library_db, source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_suggestions_text       ON suggestions(normalized_text);
CREATE INDEX IF NOT EXISTS idx_suggestions_library    ON suggestions(source_library_db);
CREATE INDEX IF NOT EXISTS idx_suggestions_kind       ON suggestions(kind);
CREATE INDEX IF NOT EXISTS idx_suggestions_sort       ON suggestions(sort_key);
