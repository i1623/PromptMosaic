"""ベースモデル別の生成パラメータ・ポリシー（状態制御の中心）。

ベースモデルの種別に応じて、UI / プラン編集の「入力段階」でパラメータを制御する。
グラフ生成時に内部で値を書き換える（場当たり的）のではなく、入力側で正しい値に
固定・無効化することで、表示値と実送信値を常に一致させる。

現状の制御:
  - CFG ロック: flux2 は denoise が「真の cfg_scale」を持ち、1.0 以外だと
    Invoke が negative text conditioning を要求する（txt2img テンプレートは
    ネガ経路を持たないため生成不可）。flux2 は蒸留ガイダンス(guidance)で制御する
    モデルなので、CFG は 1.0 固定・編集不可にする。
"""
from __future__ import annotations

# CFG（真の cfg_scale）を固定値にして編集不可にするベース
_CFG_LOCKED_BASES: frozenset[str] = frozenset({"flux2"})

# CFG をロックするベースで使う固定値
LOCKED_CFG_VALUE: float = 1.0


def cfg_is_locked(base: str | None) -> bool:
    """このベースでは CFG を固定値・編集不可にするか。"""
    return (base or "") in _CFG_LOCKED_BASES

