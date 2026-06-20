-- ============================================================
-- notes.db schema — user knowledge notes (source-of-truth)
-- Cross-DB references use (db_name + id) or text snapshots.
-- No FK dependencies on library_*.db or history_*.db.
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS daily_notes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    date               DATE    NOT NULL UNIQUE,
    title              TEXT,
    content            TEXT,
    mood               TEXT,
    session_duration   INTEGER,
    total_generations  INTEGER,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovery_notes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT    NOT NULL,
    category             TEXT,
    subcategory          TEXT,
    summary              TEXT,
    content              TEXT,
    prompt_example       TEXT,
    negative_example     TEXT,
    model_used           TEXT,
    model_base           TEXT,
    required_tags        TEXT,
    optional_tags        TEXT,
    avoid_tags           TEXT,
    reproducibility      INTEGER,
    importance           INTEGER,
    rating               INTEGER,
    status               TEXT    DEFAULT 'draft',
    source_daily_note_id INTEGER REFERENCES daily_notes(id) ON DELETE SET NULL,
    parent_discovery_id  INTEGER REFERENCES discovery_notes(id) ON DELETE SET NULL,
    protocol_id          INTEGER,
    discovered_at        DATE,
    verified_at          DATE,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS protocols (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    category          TEXT,
    description       TEXT,
    steps             TEXT,
    template_prompt   TEXT,
    ready_to_use      BOOLEAN DEFAULT 0,
    rationale         TEXT,
    principle         TEXT,
    troubleshooting   TEXT,
    applicable_models TEXT,
    prerequisites     TEXT,
    incompatible_with TEXT,
    example_prompts   TEXT,
    example_images    TEXT,
    maturity_level    TEXT    DEFAULT 'experimental',
    usage_count       INTEGER DEFAULT 0,
    success_rate      REAL,
    last_verified_at  DATE,
    tags              TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_note_discoveries (
    daily_note_id     INTEGER NOT NULL REFERENCES daily_notes(id) ON DELETE CASCADE,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    PRIMARY KEY (daily_note_id, discovery_note_id)
);

-- Cross-DB image reference: snapshot or (history_db_name, history_id)
CREATE TABLE IF NOT EXISTS discovery_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    history_db_name   TEXT,
    history_id        INTEGER,
    image_snapshot    TEXT,
    role              TEXT    DEFAULT 'success'
);

-- Cross-DB tag reference: tag text snapshot + optional source library info
CREATE TABLE IF NOT EXISTS discovery_tags (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    library_db_name   TEXT,
    tag_name_en       TEXT    NOT NULL,
    tag_name_local    TEXT,
    role              TEXT    DEFAULT 'required'
);

CREATE TABLE IF NOT EXISTS protocol_discoveries (
    protocol_id       INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    order_index       INTEGER DEFAULT 0,
    PRIMARY KEY (protocol_id, discovery_note_id)
);
