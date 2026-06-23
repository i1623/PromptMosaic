-- ============================================================
-- environment.db schema — machine/environment-specific settings
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS env_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO env_settings (key, value) VALUES
    ('invoke_endpoint',              'http://localhost:9090'),
    ('invoke_queue_id',              'default'),
    ('local_images_dir',             ''),
    ('lm_translate_prompt_reverse',  'あなたはStable Diffusion画像生成プロンプトの逆翻訳者です。
英語のDanbooruタグ、カンマ区切りタグ、または英語の自然文プロンプトを、現在のUI言語で使いやすい短い表現に翻訳してください。
出力は逆翻訳結果のみとし、説明文、ラベル、Markdown、引用符は含めないでください。');

-- LLM settings (translation / classification)
CREATE TABLE IF NOT EXISTS llm_settings (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    external_enabled      BOOLEAN DEFAULT 0,
    external_endpoint     TEXT,
    external_model        TEXT,
    encrypted_api_key     BLOB,
    salt                  BLOB,
    local_enabled         BOOLEAN DEFAULT 1,
    local_endpoint        TEXT    DEFAULT 'http://localhost:1234/v1',
    local_model           TEXT,
    default_temperature   REAL    DEFAULT 0.7,
    default_max_tokens    INTEGER DEFAULT 2000,
    prefer_local          BOOLEAN DEFAULT 1,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO llm_settings (id) VALUES (1);

-- InvokeAI model / LoRA catalog
CREATE TABLE IF NOT EXISTS models (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoke_key        TEXT    UNIQUE NOT NULL,
    invoke_hash       TEXT,
    name              TEXT    NOT NULL,
    base              TEXT    NOT NULL DEFAULT 'sdxl',
    type              TEXT    NOT NULL DEFAULT 'main',
    thumbnail_path    TEXT,
    thumbnail_data    BLOB,
    title             TEXT,
    comment           TEXT    DEFAULT '',
    available         INTEGER NOT NULL DEFAULT 1,
    is_nsfw           INTEGER DEFAULT 0,
    lora_genre        TEXT    REFERENCES lora_genres(key) ON DELETE SET NULL,
    variant           TEXT,
    default_steps     INTEGER,
    default_cfg       REAL,
    default_scheduler TEXT,
    template_cache_key TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_models_type  ON models(type);
CREATE INDEX IF NOT EXISTS idx_models_base  ON models(base);
CREATE INDEX IF NOT EXISTS idx_models_genre ON models(lora_genre);

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

INSERT OR IGNORE INTO lora_genres (key, label, sort_order) VALUES
    ('character_identity',           '01 キャラクター・個体',        10),
    ('human_expression',             '02 人物表現',                  20),
    ('pose_action_interaction',      '03 ポーズ・動作・相互作用',    30),
    ('clothing_accessory',           '04 衣装・装身具',              40),
    ('living_creature',              '05 生物・クリーチャー',        50),
    ('object_artifact',              '06 物品・人工物',              60),
    ('architecture_structure',       '07 建築・構造物',              70),
    ('location_background',          '08 場所・背景',                80),
    ('natural_feature',              '09 自然物・地形',              90),
    ('phenomenon_event',             '10 現象・状況・事象',         100),
    ('era_culture_worldview',        '11 時代・文化・世界観',       110),
    ('art_style_medium',             '12 画風・媒体・画面表現',     120),
    ('lighting_color_screen_effect', '13 光・色・画面効果',         130),
    ('quality_correction',           '14 品質・補正',               140),
    ('mixed_unsorted',               '99 複合・未整理',             990);

CREATE TABLE IF NOT EXISTS lora_trigger_sets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoke_key    TEXT    NOT NULL,
    sort_order    INTEGER DEFAULT 0,
    label         TEXT    NOT NULL DEFAULT 'StandardPrompt',
    trigger_words TEXT    NOT NULL DEFAULT '',
    UNIQUE(invoke_key, sort_order)
);

CREATE TABLE IF NOT EXISTS lora_neg_prompt_sets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoke_key    TEXT    NOT NULL,
    sort_order    INTEGER DEFAULT 0,
    label         TEXT    NOT NULL DEFAULT 'Default',
    neg_words     TEXT    NOT NULL DEFAULT '',
    UNIQUE(invoke_key, sort_order)
);

CREATE TABLE IF NOT EXISTS model_auto_loras (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key  TEXT    NOT NULL,
    lora_key   TEXT    NOT NULL,
    weight     REAL    NOT NULL DEFAULT 0.75,
    sort_order INTEGER DEFAULT 0,
    UNIQUE(model_key, lora_key)
);

CREATE INDEX IF NOT EXISTS idx_model_auto_loras_model ON model_auto_loras(model_key);

CREATE TABLE IF NOT EXISTS templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    base            TEXT NOT NULL,
    cache_key       TEXT UNIQUE NOT NULL,
    is_base_default INTEGER DEFAULT 0,
    vae_name        TEXT DEFAULT '',     -- 取り込み時に検出した VAE 名（空=モデル内蔵/未指定）
    has_refiner     INTEGER DEFAULT 0,   -- リファイナー段を含むか（1=含む）
    encoder_name    TEXT DEFAULT '',     -- 主テキストエンコーダ名（空=モデル内蔵/未指定）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(base, name)
);

CREATE TABLE IF NOT EXISTS generation_plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS generation_plan_rows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id        INTEGER NOT NULL REFERENCES generation_plans(id) ON DELETE CASCADE,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    enabled        INTEGER NOT NULL DEFAULT 1,
    model_key      TEXT NOT NULL,
    model_name     TEXT NOT NULL DEFAULT '',
    model_base     TEXT NOT NULL DEFAULT '',
    image_count    INTEGER NOT NULL DEFAULT 1,
    steps          INTEGER NOT NULL DEFAULT 30,
    cfg_scale      REAL    NOT NULL DEFAULT 7.0,
    scheduler      TEXT    NOT NULL DEFAULT 'euler',
    extra_positive TEXT    NOT NULL DEFAULT '',
    extra_negative TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_generation_plan_rows_plan
    ON generation_plan_rows(plan_id, sort_order, id);

CREATE TABLE IF NOT EXISTS generation_plan_loras (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id     INTEGER NOT NULL REFERENCES generation_plan_rows(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    enabled    INTEGER NOT NULL DEFAULT 1,
    lora_key   TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    base       TEXT NOT NULL DEFAULT '',
    weight     REAL NOT NULL DEFAULT 0.75
);

CREATE INDEX IF NOT EXISTS idx_generation_plan_loras_row
    ON generation_plan_loras(row_id, sort_order, id);

CREATE TABLE IF NOT EXISTS i2t_prompt_templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    system_prompt TEXT NOT NULL DEFAULT '',
    user_prompt   TEXT NOT NULL DEFAULT '',
    sort_order    INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
