-- ============================================================
-- app.db schema — application-wide settings
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO app_settings (key, value) VALUES
    ('language',                    'ja'),
    ('theme',                       'dark'),
    ('font_size',                   '10'),
    ('current_library_db',          'default'),
    ('current_history_db',          'default'),
    ('show_nsfw',                   '0'),
    ('thumbnail_size',              '128'),
    ('auto_save_prompt',            '1'),
    ('suggestions_rebuild_on_startup', '0'),
    ('gen_seed',                    '-1'),
    ('gen_steps',                   '30'),
    ('gen_cfg',                     '7.0'),
    ('gen_scheduler',               'euler'),
    ('gen_width',                   '1024'),
    ('gen_height',                  '1024'),
    ('gen_count',                   '1'),
    ('category_colors',             '{}'),
    ('unregistered_tile_bg',        ''),
    ('unregistered_tile_fg',        '');
