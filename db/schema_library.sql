-- ============================================================
-- library_*.db schema — one file per library
-- No dictionary_key; the file name is the library identifier.
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Tags (no dictionary_key column; belongs to this library implicitly)
CREATE TABLE IF NOT EXISTS tags (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en                 TEXT    NOT NULL UNIQUE,
    name_local              TEXT,
    category                TEXT,
    subcategory             TEXT,
    parent_id               INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    description             TEXT,
    popularity              INTEGER DEFAULT 0,
    emphasis_recommended    REAL    DEFAULT 1.0,
    is_nav_only             INTEGER DEFAULT 0,
    is_nsfw                 INTEGER DEFAULT 0,
    genre                   TEXT    NOT NULL DEFAULT 'mixed_unsorted',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tags_category  ON tags(category);
CREATE INDEX IF NOT EXISTS idx_tags_parent_id ON tags(parent_id);
CREATE INDEX IF NOT EXISTS idx_tags_name_en   ON tags(name_en);
CREATE INDEX IF NOT EXISTS idx_tags_genre     ON tags(genre);

-- Tag categories (no dictionary_key; belongs to this library)
CREATE TABLE IF NOT EXISTS tag_categories (
    key            TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    bg_color       TEXT NOT NULL DEFAULT '#2a2a3a',
    fg_color       TEXT NOT NULL DEFAULT '#a6adc8',
    bg_color_light TEXT,
    fg_color_light TEXT,
    bg_color_dark  TEXT,
    fg_color_dark  TEXT,
    sort_order     INTEGER DEFAULT 100,
    is_tag_genre   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tag_labels (
    tag_name   TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tag_thumbnails (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id       INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    image_path   TEXT    NOT NULL,
    image_data   BLOB,
    model_used   TEXT,
    base_prompt  TEXT,
    seed         INTEGER,
    is_default   BOOLEAN DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tag_registration_queue (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name              TEXT    NOT NULL,
    source_image_path     TEXT,
    suggested_category    TEXT,
    suggested_subcategory TEXT,
    suggested_ja          TEXT,
    suggested_en          TEXT,
    suggested_description TEXT,
    confidence            INTEGER,
    final_category        TEXT,
    final_ja              TEXT,
    final_en              TEXT,
    final_description     TEXT,
    status                TEXT    DEFAULT 'pending',
    notes                 TEXT,
    queued_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at           TIMESTAMP
);

-- Standalone registered tiles (library source-of-truth)
-- Tiles are snapshots: carry all display/prompt data.
CREATE TABLE IF NOT EXISTS tiles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tile_type        TEXT    NOT NULL,
    tag_name         TEXT,
    tag_local        TEXT,
    tag_category     TEXT,
    emphasis         REAL    DEFAULT 1.0,
    natural_text     TEXT,
    natural_language TEXT    DEFAULT 'en',
    is_locked        BOOLEAN DEFAULT 0,
    block_type       TEXT    DEFAULT 'positive',
    block_position   TEXT    DEFAULT 'middle',
    order_index      INTEGER DEFAULT 0,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tile groups (group_presets)
CREATE TABLE IF NOT EXISTS group_presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    group_json    TEXT    NOT NULL,
    sort_order    INTEGER DEFAULT 0,
    display_label TEXT,
    category      TEXT,
    memo          TEXT,
    rating        INTEGER,
    is_nsfw       BOOLEAN DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_group_presets_category ON group_presets(category);

CREATE TABLE IF NOT EXISTS group_categories (
    key            TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    bg_color       TEXT,
    fg_color       TEXT,
    bg_color_light TEXT,
    fg_color_light TEXT,
    bg_color_dark  TEXT,
    fg_color_dark  TEXT,
    sort_order     INTEGER DEFAULT 100,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sentence prompts
CREATE TABLE IF NOT EXISTS prompt_texts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text     TEXT    NOT NULL,
    translated_text TEXT,
    display_label   TEXT,
    thumbnail_path  TEXT,
    thumbnail_data  BLOB,
    category        TEXT,
    genre           TEXT,
    parent_id       INTEGER REFERENCES prompt_texts(id) ON DELETE SET NULL,
    sort_order      INTEGER DEFAULT 0,
    keywords        TEXT,
    language        TEXT    DEFAULT 'ja',
    status          TEXT    DEFAULT 'active',
    rating          INTEGER,
    memo            TEXT,
    is_nsfw         BOOLEAN DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_texts_source   ON prompt_texts(source_text);
CREATE INDEX IF NOT EXISTS idx_prompt_texts_category ON prompt_texts(category, genre);
CREATE INDEX IF NOT EXISTS idx_prompt_texts_parent   ON prompt_texts(parent_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_prompt_texts_status   ON prompt_texts(status);

CREATE TABLE IF NOT EXISTS prompt_text_categories (
    key            TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    bg_color       TEXT,
    fg_color       TEXT,
    bg_color_light TEXT,
    fg_color_light TEXT,
    bg_color_dark  TEXT,
    fg_color_dark  TEXT,
    sort_order     INTEGER DEFAULT 100,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_text_embeddings (
    prompt_text_id  INTEGER PRIMARY KEY REFERENCES prompt_texts(id) ON DELETE CASCADE,
    embedding       BLOB,
    embedding_model TEXT
);

-- Concepts
CREATE TABLE IF NOT EXISTS concepts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    type              TEXT,
    description       TEXT,
    parent_concept_id INTEGER REFERENCES concepts(id) ON DELETE SET NULL,
    thumbnail_path    TEXT,
    thumbnail_data    BLOB,
    usage_count       INTEGER DEFAULT 0,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS concept_tiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id     INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    tile_data      TEXT    NOT NULL,
    order_index    INTEGER DEFAULT 0,
    block_position TEXT    DEFAULT 'middle'
);
