-- 送信キュー（一時バッファ）
--
-- 生成ユニット（generate_batch 1回分）の送信データを貯め、送信して item_ids を
-- 履歴行へ書き込んだらレコードを削除する。情報の蓄積はしない。
-- 不変条件: 正常起動時・正常終了時にレコードは存在しない。
-- 起動時にレコードが残っていれば前回セッションは異常終了 →
-- 送信済み item をキャンセルして全クリアする（自動再開はしない）。

CREATE TABLE IF NOT EXISTS send_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    seq            INTEGER NOT NULL,           -- プラン内の送信順序
    history_name   TEXT,                       -- 履歴DB名（NULL=記録なし生成）
    generation_ids TEXT,                       -- JSON配列: 紐づく履歴行ID（記録なしはNULL）
    payload        TEXT NOT NULL,              -- JSON: generate_batch の引数一式
    sent_item_ids  TEXT,                       -- JSON配列: 送信済みなら item_ids（未送信はNULL）
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
