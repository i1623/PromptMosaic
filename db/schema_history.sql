-- ============================================================
-- history_*.db schema — one file per history folder
-- Generations are snapshots; display/reuse does not require the
-- current library DB or environment DB to match.
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS generation_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    parent_id   INTEGER REFERENCES generation_groups(id) ON DELETE CASCADE,
    sort_order  INTEGER DEFAULT 0,
    folder_path TEXT,
    is_nsfw     BOOLEAN DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generation_groups_parent ON generation_groups(parent_id);

CREATE TABLE IF NOT EXISTS generations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path              TEXT,
    thumbnail_path          TEXT,
    thumbnail_data          BLOB,
    sent_positive_prompt    TEXT,
    sent_negative_prompt    TEXT,
    structured_prompt       TEXT,
    structured_negative     TEXT,
    invoke_key              TEXT,
    model_name              TEXT,
    model_base              TEXT,
    model_hash              TEXT,
    cfg_scale               REAL,
    cfg_rescale_multiplier  REAL,
    steps                   INTEGER,
    scheduler               TEXT,
    seed                    INTEGER,
    width                   INTEGER,
    height                  INTEGER,
    rand_device             TEXT,
    seamless_x              BOOLEAN DEFAULT 0,
    seamless_y              BOOLEAN DEFAULT 0,
    generation_mode         TEXT,
    app_version             TEXT,
    invoke_image_name       TEXT,
    board_id                TEXT,
    group_id                INTEGER REFERENCES generation_groups(id) ON DELETE SET NULL,
    local_path              TEXT,
    loras_json              TEXT,
    image_count             INTEGER DEFAULT 1,
    invoke_queue_item_ids   TEXT,
    template_id             INTEGER,
    page_id                 INTEGER REFERENCES pages(id) ON DELETE SET NULL,
    cut_number              INTEGER,
    scene_description       TEXT,
    dialogue                TEXT,
    dialogue_char_id        INTEGER,
    cut_status              TEXT    DEFAULT '未着手',
    deleted_at              TIMESTAMP,
    trashed_at              TIMESTAMP,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generations_created_at ON generations(created_at);
CREATE INDEX IF NOT EXISTS idx_generations_model_name ON generations(model_name);
CREATE INDEX IF NOT EXISTS idx_generations_seed       ON generations(seed);
CREATE INDEX IF NOT EXISTS idx_generations_group_id   ON generations(group_id);
CREATE INDEX IF NOT EXISTS idx_generations_trashed    ON generations(trashed_at);

CREATE TABLE IF NOT EXISTS generation_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id     INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    sort_order        INTEGER DEFAULT 0,
    invoke_item_id    INTEGER,
    invoke_image_name TEXT,
    local_path        TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generation_images_gen_id ON generation_images(generation_id);

-- Center-pane snapshot tiles per generation
CREATE TABLE IF NOT EXISTS tiles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id    INTEGER REFERENCES generations(id) ON DELETE CASCADE,
    block_type       TEXT    DEFAULT 'positive',
    block_position   TEXT    DEFAULT 'middle',
    order_index      INTEGER DEFAULT 0,
    tile_type        TEXT    NOT NULL,
    tag_name         TEXT,
    tag_local        TEXT,
    tag_category     TEXT,
    emphasis         REAL    DEFAULT 1.0,
    natural_text     TEXT,
    natural_language TEXT    DEFAULT 'en',
    is_locked        BOOLEAN DEFAULT 0,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_blocks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id  INTEGER REFERENCES generations(id) ON DELETE CASCADE,
    block_type     TEXT    DEFAULT 'positive',
    block_position TEXT    NOT NULL,
    randomize      BOOLEAN DEFAULT 0,
    label          TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- External tool inbox
CREATE TABLE IF NOT EXISTS external_inbox (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app               TEXT    NOT NULL DEFAULT 'ExternalTool',
    source_item_id           TEXT,
    history_name             TEXT    NOT NULL,
    group_path_json          TEXT,
    page_name                TEXT,
    page_number              INTEGER,
    cut_name                 TEXT,
    cut_number               INTEGER,
    title                    TEXT,
    save_folder_path         TEXT,
    payload_json             TEXT    NOT NULL DEFAULT '{}',
    status                   TEXT    NOT NULL DEFAULT 'pending',
    imported_generation_id   INTEGER REFERENCES generations(id) ON DELETE SET NULL,
    error_message            TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    imported_at              TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_external_inbox_status ON external_inbox(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_external_inbox_source
    ON external_inbox(source_app, source_item_id)
    WHERE source_item_id IS NOT NULL AND source_item_id != '';

-- Image reviews
CREATE TABLE IF NOT EXISTS image_reviews (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id       INTEGER NOT NULL UNIQUE REFERENCES generations(id) ON DELETE CASCADE,
    rating              INTEGER,
    rating_10           INTEGER,
    thumbs              INTEGER,
    status              TEXT    DEFAULT 'draft',
    is_favorite         BOOLEAN DEFAULT 0,
    is_sellable         BOOLEAN DEFAULT 0,
    is_reference        BOOLEAN DEFAULT 0,
    is_nsfw             BOOLEAN,
    review_text         TEXT,
    title               TEXT,
    quality_score       REAL,
    artistic_score      REAL,
    reproduction_score  REAL,
    needs_retake        BOOLEAN DEFAULT 0,
    retake_reason       TEXT,
    retake_priority     INTEGER,
    parent_image_id     INTEGER REFERENCES generations(id) ON DELETE SET NULL,
    series_id           INTEGER,
    used_in             TEXT,
    sold_count          INTEGER DEFAULT 0,
    color_label         TEXT,
    custom_field_1      TEXT,
    custom_field_2      TEXT,
    custom_field_3      TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at         TIMESTAMP,
    review_count        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_image_reviews_status      ON image_reviews(status);
CREATE INDEX IF NOT EXISTS idx_image_reviews_rating      ON image_reviews(rating);
CREATE INDEX IF NOT EXISTS idx_image_reviews_is_sellable ON image_reviews(is_sellable);

CREATE TABLE IF NOT EXISTS image_review_tags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    tag_name      TEXT    NOT NULL,
    tag_category  TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS image_review_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    field_name    TEXT    NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    changed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Works/pages hierarchy (for external tool integration)
CREATE TABLE IF NOT EXISTS works (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    status      TEXT    DEFAULT '未着手',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id     INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL DEFAULT 1,
    status      TEXT    DEFAULT '未完成',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pages_work_id ON pages(work_id);

CREATE TABLE IF NOT EXISTS sub_cuts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    sub_number    INTEGER NOT NULL DEFAULT 1,
    description   TEXT,
    memo          TEXT,
    status        TEXT    DEFAULT '未着手',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sub_cuts_generation_id ON sub_cuts(generation_id);

