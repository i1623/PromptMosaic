-- ============================================================
-- PromptMosaic - SQLite スキーマ定義
-- 設計方針: 最初から最大限のカラムを定義。後からALTER TABLE不要。
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- タグ管理系
-- ============================================================

CREATE TABLE IF NOT EXISTS tags (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    dictionary_key          TEXT    NOT NULL DEFAULT 'default',
    name_en                 TEXT    NOT NULL,
    name_local              TEXT,
    category                TEXT,                        -- object/state/quality/style/composition/lighting/action/scene
    subcategory             TEXT,
    parent_id               INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    description             TEXT,
    popularity              INTEGER DEFAULT 0,
    emphasis_recommended    REAL    DEFAULT 1.0,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dictionary_key, name_en)
);

CREATE TABLE IF NOT EXISTS tag_dictionaries (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 100,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tags_category   ON tags(category);
CREATE INDEX IF NOT EXISTS idx_tags_parent_id  ON tags(parent_id);
CREATE INDEX IF NOT EXISTS idx_tags_name_en    ON tags(name_en);

CREATE TABLE IF NOT EXISTS lora_genres (
    key        TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    parent_id  TEXT REFERENCES lora_genres(key) ON DELETE SET NULL,
    sort_order INTEGER DEFAULT 100,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lora_genres_parent ON lora_genres(parent_id);
CREATE INDEX IF NOT EXISTS idx_lora_genres_sort   ON lora_genres(sort_order, label);

CREATE TABLE IF NOT EXISTS tag_thumbnails (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id       INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    image_path   TEXT    NOT NULL,
    model_used   TEXT,
    base_prompt  TEXT,
    seed         INTEGER,
    is_default   BOOLEAN DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tag_registration_queue (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name              TEXT    NOT NULL,
    source_image_id       INTEGER REFERENCES generations(id) ON DELETE SET NULL,
    source_image_path     TEXT,
    suggested_category    TEXT,
    suggested_subcategory TEXT,
    suggested_ja          TEXT,
    suggested_en          TEXT,
    suggested_description TEXT,
    confidence            INTEGER,                       -- 0-100
    final_category        TEXT,
    final_ja              TEXT,
    final_en              TEXT,
    final_description     TEXT,
    status                TEXT    DEFAULT 'pending',     -- pending/editing/approved/rejected
    notes                 TEXT,
    queued_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at           TIMESTAMP
);

-- ============================================================
-- 概念管理系
-- ============================================================

CREATE TABLE IF NOT EXISTS concepts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    type              TEXT,                              -- character/situation/style/preset
    description       TEXT,
    parent_concept_id INTEGER REFERENCES concepts(id) ON DELETE SET NULL,
    thumbnail_path    TEXT,
    usage_count       INTEGER DEFAULT 0,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS concept_tiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id     INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    tile_data      TEXT    NOT NULL,                    -- JSON: tile情報
    order_index    INTEGER DEFAULT 0,
    block_position TEXT    DEFAULT 'middle'             -- top/middle/bottom
);

-- ============================================================
-- 生成グループ（階層フォルダ）系
-- ============================================================

CREATE TABLE IF NOT EXISTS generation_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    parent_id   INTEGER REFERENCES generation_groups(id) ON DELETE CASCADE,
    sort_order  INTEGER DEFAULT 0,
    folder_path TEXT,                                -- ローカル保存先フォルダ（絶対パス、NULL=親から継承）
    is_nsfw     BOOLEAN DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generation_groups_parent ON generation_groups(parent_id);

-- ============================================================
-- 生成履歴系
-- ============================================================

CREATE TABLE IF NOT EXISTS generations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path              TEXT,
    thumbnail_path          TEXT,
    sent_positive_prompt    TEXT,
    sent_negative_prompt    TEXT,
    structured_prompt       TEXT,                       -- JSON: 表示形式（ポジ）
    structured_negative     TEXT,                       -- JSON: 表示形式（ネガ）
    invoke_key              TEXT,                       -- InvokeのモデルUUID（models.invoke_keyと対応）
    model_name              TEXT,
    model_base              TEXT,                       -- sdxl等
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
    generation_mode         TEXT,                       -- sdxl_txt2img等
    app_version             TEXT,
    invoke_image_name       TEXT,
    board_id                TEXT,
    group_id                INTEGER REFERENCES generation_groups(id) ON DELETE SET NULL,
    local_path              TEXT,                   -- ローカルコピーの絶対パス
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generations_created_at ON generations(created_at);
CREATE INDEX IF NOT EXISTS idx_generations_model_name ON generations(model_name);
CREATE INDEX IF NOT EXISTS idx_generations_seed       ON generations(seed);

-- ============================================================
-- 外部連携受信箱（外部ツール）
-- ============================================================

CREATE TABLE IF NOT EXISTS external_inbox (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app               TEXT    NOT NULL DEFAULT 'ExternalTool',
    source_item_id           TEXT,
    history_name             TEXT    NOT NULL,          -- 履歴ツリーの一階層目（必須）
    group_path_json          TEXT,                      -- JSON配列。指定時はこちらを履歴階層に使う
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

CREATE TABLE IF NOT EXISTS generation_tags (
    generation_id  INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    block_position TEXT,                                -- top/middle/bottom
    emphasis       REAL    DEFAULT 1.0,
    PRIMARY KEY (generation_id, tag_id)
);

CREATE TABLE IF NOT EXISTS generation_concepts (
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    concept_id    INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    PRIMARY KEY (generation_id, concept_id)
);

-- ============================================================
-- タイル・ブロック系
-- ============================================================

CREATE TABLE IF NOT EXISTS tiles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id    INTEGER REFERENCES generations(id) ON DELETE CASCADE,
    block_type       TEXT    DEFAULT 'positive',        -- positive/negative
    block_position   TEXT    DEFAULT 'middle',          -- top/middle/bottom
    order_index      INTEGER DEFAULT 0,
    tile_type        TEXT    NOT NULL,                  -- tag/natural_text
    tag_name         TEXT,
    tag_local        TEXT,
    tag_category     TEXT,
    emphasis         REAL    DEFAULT 1.0,
    natural_text     TEXT,
    natural_language TEXT    DEFAULT 'en',
    is_locked        BOOLEAN DEFAULT 0,                 -- シャッフル除外
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_blocks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id  INTEGER REFERENCES generations(id) ON DELETE CASCADE,
    block_type     TEXT    DEFAULT 'positive',          -- positive/negative
    block_position TEXT    NOT NULL,                    -- top/middle/bottom
    randomize      BOOLEAN DEFAULT 0,
    label          TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 画像レビュー系
-- ============================================================

CREATE TABLE IF NOT EXISTS image_reviews (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id       INTEGER NOT NULL UNIQUE REFERENCES generations(id) ON DELETE CASCADE,
    rating              INTEGER,                        -- 1-5
    rating_10           INTEGER,                        -- 1-10
    thumbs              INTEGER,                        -- 1=👍 / -1=👎 / NULL
    status              TEXT    DEFAULT 'draft',        -- draft/candidate/approved/rejected/archived
    is_favorite         BOOLEAN DEFAULT 0,
    is_sellable         BOOLEAN DEFAULT 0,
    is_reference        BOOLEAN DEFAULT 0,
    is_nsfw             BOOLEAN,
    review_text         TEXT,                           -- Markdown
    title               TEXT,
    quality_score       REAL,                           -- 0-100
    artistic_score      REAL,                           -- 0-100
    reproduction_score  REAL,
    needs_retake        BOOLEAN DEFAULT 0,
    retake_reason       TEXT,
    retake_priority     INTEGER,
    parent_image_id     INTEGER REFERENCES generations(id) ON DELETE SET NULL,
    series_id           INTEGER,
    used_in             TEXT,                           -- FANZA/SNS/personal
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

CREATE INDEX IF NOT EXISTS idx_image_reviews_status     ON image_reviews(status);
CREATE INDEX IF NOT EXISTS idx_image_reviews_rating     ON image_reviews(rating);
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

-- ============================================================
-- 知見ノート系
-- ============================================================

CREATE TABLE IF NOT EXISTS daily_notes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    date               DATE    NOT NULL UNIQUE,
    title              TEXT,
    content            TEXT,                            -- Markdown
    mood               TEXT,
    session_duration   INTEGER,                         -- 分
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
    content              TEXT,                          -- Markdown詳細
    prompt_example       TEXT,
    negative_example     TEXT,
    model_used           TEXT,
    model_base           TEXT,
    required_tags        TEXT,                          -- JSON
    optional_tags        TEXT,                          -- JSON
    avoid_tags           TEXT,                          -- JSON
    reproducibility      INTEGER,                       -- 1-5
    importance           INTEGER,                       -- 1-5
    rating               INTEGER,                       -- 1-5
    status               TEXT    DEFAULT 'draft',       -- draft/verified/archived
    source_daily_note_id INTEGER REFERENCES daily_notes(id) ON DELETE SET NULL,
    parent_discovery_id  INTEGER REFERENCES discovery_notes(id) ON DELETE SET NULL,
    protocol_id          INTEGER,                       -- 昇華先プロトコル（後で外部キー）
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
    steps             TEXT,                             -- Markdown手順書
    template_prompt   TEXT,
    ready_to_use      BOOLEAN DEFAULT 0,
    rationale         TEXT,
    principle         TEXT,
    troubleshooting   TEXT,
    applicable_models TEXT,
    prerequisites     TEXT,
    incompatible_with TEXT,
    example_prompts   TEXT,                             -- JSON
    example_images    TEXT,                             -- JSON: generation IDのリスト
    maturity_level    TEXT    DEFAULT 'experimental',   -- experimental/stable/deprecated
    usage_count       INTEGER DEFAULT 0,
    success_rate      REAL,
    last_verified_at  DATE,
    tags              TEXT,                             -- JSON
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at      TIMESTAMP
);

-- 遅延外部キー: discovery_notes.protocol_id
-- SQLiteはADD CONSTRAINTをサポートしないため、アプリ層で整合性を保つ

-- 関連テーブル
CREATE TABLE IF NOT EXISTS daily_note_discoveries (
    daily_note_id     INTEGER NOT NULL REFERENCES daily_notes(id) ON DELETE CASCADE,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    PRIMARY KEY (daily_note_id, discovery_note_id)
);

CREATE TABLE IF NOT EXISTS discovery_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    generation_id     INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    role              TEXT    DEFAULT 'success'         -- success/failure/comparison
);

CREATE TABLE IF NOT EXISTS discovery_tags (
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    tag_id            INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    role              TEXT    DEFAULT 'required',       -- required/optional/avoid
    PRIMARY KEY (discovery_note_id, tag_id, role)
);

CREATE TABLE IF NOT EXISTS protocol_discoveries (
    protocol_id       INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
    discovery_note_id INTEGER NOT NULL REFERENCES discovery_notes(id) ON DELETE CASCADE,
    order_index       INTEGER DEFAULT 0,
    PRIMARY KEY (protocol_id, discovery_note_id)
);

-- ============================================================
-- LLM設定系
-- ============================================================

CREATE TABLE IF NOT EXISTS llm_settings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    external_enabled      BOOLEAN DEFAULT 0,
    external_endpoint     TEXT,
    external_model        TEXT,
    encrypted_api_key     BLOB,                         -- AES-CTR暗号化済み
    salt                  BLOB,                         -- PBKDF2鍵導出用ソルト
    local_enabled         BOOLEAN DEFAULT 1,
    local_endpoint        TEXT    DEFAULT 'http://localhost:1234/v1',
    local_model           TEXT,
    default_temperature   REAL    DEFAULT 0.7,
    default_max_tokens    INTEGER DEFAULT 2000,
    prefer_local          BOOLEAN DEFAULT 1,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- デフォルト行を1件挿入（アプリ起動時に参照する）
INSERT OR IGNORE INTO llm_settings (id) VALUES (1);

-- ============================================================
-- アプリ設定系
-- ============================================================

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- デフォルト設定
INSERT OR IGNORE INTO app_settings (key, value) VALUES
    ('invoke_endpoint',   'http://localhost:9090'),
    ('invoke_queue_id',   'default'),
    ('category_colors',   '{"object":"#4A90D9","state":"#F5A623","quality":"#7ED321","style":"#9B59B6","composition":"#F39C12","lighting":"#E74C3C","action":"#795548","scene":"#E91E8C"}'),
    ('thumbnail_size',    '128'),
    ('auto_save_prompt',  '1'),
    ('lm_translate_prompt_reverse', 'あなたはStable Diffusion画像生成プロンプトの逆翻訳者です。
英語のDanbooruタグ、カンマ区切りタグ、または英語の自然文プロンプトを、現在のUI言語で使いやすい短い表現に翻訳してください。
出力は逆翻訳結果のみとし、説明文、ラベル、Markdown、引用符は含めないでください。'),
    ('theme',             'dark');

-- ============================================================
-- 作品階層系（外部ツール連携）物理4層構造
-- 作品(works) > ページ(pages) > コマ(generations) > 予備(sub_cuts)
-- ※シーケンスは概念層のため外部ツール側で管理
-- ============================================================

CREATE TABLE IF NOT EXISTS works (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    status      TEXT    DEFAULT '未着手',  -- 未着手/執筆中/完成
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id     INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL DEFAULT 1,
    status      TEXT    DEFAULT '未完成',  -- 未完成/部分完成/全コマ確定/レイアウト済
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- idx_pages_work_id はマイグレーションで作成（既存DBのテーブル再構築後）

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

-- ============================================================
-- 分類エンジン系（将来実装 - テーブルだけ作成）
-- ============================================================

CREATE TABLE IF NOT EXISTS image_index (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id  INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    phash          TEXT,                                -- 知覚的ハッシュ
    clip_vector_id TEXT,                               -- ChromaDB側のID
    face_vector_id TEXT,
    pose_keypoints TEXT,                               -- JSON
    scene_features TEXT,                               -- JSON
    indexed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS similarity_presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    phash_weight  REAL    DEFAULT 0.3,
    clip_weight   REAL    DEFAULT 0.4,
    face_weight   REAL    DEFAULT 0.2,
    pose_weight   REAL    DEFAULT 0.1,
    threshold     REAL    DEFAULT 0.8,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO similarity_presets (id, name, phash_weight, clip_weight, face_weight, pose_weight, threshold)
VALUES
    (1, '厳密',   0.4, 0.4, 0.1, 0.1, 0.95),
    (2, '標準',   0.3, 0.4, 0.2, 0.1, 0.80),
    (3, 'ゆるい', 0.2, 0.5, 0.2, 0.1, 0.60);

CREATE TABLE IF NOT EXISTS classification_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name    TEXT,
    preset_id        INTEGER REFERENCES similarity_presets(id),
    status           TEXT    DEFAULT 'pending',         -- pending/running/done/cancelled
    target_count     INTEGER,
    processed_count  INTEGER DEFAULT 0,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS classification_operations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES classification_sessions(id) ON DELETE CASCADE,
    operation_type TEXT    NOT NULL,                    -- move/rename/link
    source_path    TEXT,
    dest_path      TEXT,
    generation_id  INTEGER REFERENCES generations(id),
    undone         BOOLEAN DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- タイルグループ系
-- ============================================================

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

-- ============================================================
-- 文章プロンプト管理系
-- ============================================================

CREATE TABLE IF NOT EXISTS prompt_texts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text     TEXT    NOT NULL,
    translated_text TEXT,
    display_label   TEXT,
    thumbnail_path  TEXT,
    category        TEXT,                               -- 将来の分類キー
    genre           TEXT,                               -- 将来のサブ分類/ジャンルキー
    parent_id       INTEGER REFERENCES prompt_texts(id) ON DELETE SET NULL,
    sort_order      INTEGER DEFAULT 0,
    keywords        TEXT,                               -- 検索・分類補助用の自由キーワード
    language        TEXT    DEFAULT 'ja',
    status          TEXT    DEFAULT 'active',
    rating          INTEGER,                            -- 1-5, NULL許容
    memo            TEXT,
    is_nsfw         BOOLEAN DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_texts_source ON prompt_texts(source_text);

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
