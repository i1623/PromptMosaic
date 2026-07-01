-- ============================================================
-- history_map.db schema — cross-history relationships and
-- center-pane editing lineage (parallel world history)
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Cross-history-DB parent/child relationships.
-- References use history_db_name + history_id (no FK, by design).
CREATE TABLE IF NOT EXISTS history_relationships (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_history_db     TEXT    NOT NULL,
    parent_history_id     INTEGER NOT NULL,
    child_history_db      TEXT    NOT NULL,
    child_history_id      INTEGER NOT NULL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(child_history_db, child_history_id)
);

CREATE INDEX IF NOT EXISTS idx_hrel_parent ON history_relationships(parent_history_db, parent_history_id);
CREATE INDEX IF NOT EXISTS idx_hrel_child  ON history_relationships(child_history_db, child_history_id);

-- Center-pane editing lineage (parallel world / history map).
-- Stores (history_db_name, history_id) pairs; no FK across DBs.
CREATE TABLE IF NOT EXISTS editor_history_nodes (
    history_db      TEXT    NOT NULL,
    history_id      INTEGER NOT NULL,
    parent_db       TEXT,
    parent_id       INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (history_db, history_id)
);

CREATE INDEX IF NOT EXISTS idx_ehn_parent ON editor_history_nodes(parent_db, parent_id);

-- Image-less draft nodes in the editor history map.
-- Draft keys are exposed to the UI as (history_db='draft:' || owner_history_db, history_id=id).
CREATE TABLE IF NOT EXISTS editor_history_draft_nodes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_history_db   TEXT    NOT NULL,
    parent_db          TEXT    NOT NULL,
    parent_id          INTEGER NOT NULL,
    group_id           INTEGER,
    prompt_json        TEXT    NOT NULL,
    memo_text          TEXT,
    title              TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ehdn_parent ON editor_history_draft_nodes(parent_db, parent_id);
CREATE INDEX IF NOT EXISTS idx_ehdn_owner_group ON editor_history_draft_nodes(owner_history_db, group_id);

-- Snapshot of the visible history map at a given generation.
CREATE TABLE IF NOT EXISTS editor_history_snapshots (
    history_db  TEXT    NOT NULL,
    history_id  INTEGER NOT NULL,
    nodes_json  TEXT    NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (history_db, history_id)
);
