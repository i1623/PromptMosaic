"""
Invoke APIクライアント

対応エンドポイント:
  recall   : POST/GET /api/v1/recall/{queue_id}
  queue    : enqueue_batch / status / current
  images   : 一覧・メタデータ・フル画像・サムネイル
  boards   : 作成・画像追加
  models   : 一覧
  utilities: dynamicprompts解析

設定はDBの app_settings から取得する。
接続失敗時は InvokeConnectionError を送出する。
"""

from __future__ import annotations

import httpx
import json as _json
from copy import deepcopy
from pathlib import Path
from typing import Any

import db.env_db as _env_db

# テンプレートキャッシュ保存先
_TEMPLATE_CACHE_PATH = Path(__file__).parent.parent / "data" / "template_cache.json"


class InvokeConnectionError(Exception):
    """Invokeへの接続・通信エラー"""


class TemplateBaseMismatch(Exception):
    """ベース別テンプレ取得で、最新ジョブのベースが期待ベースと一致しない。"""

    def __init__(self, expected_base: str, actual_base: str):
        self.expected_base = expected_base
        self.actual_base = actual_base
        super().__init__(
            f"最新ジョブのベース({actual_base})が期待ベース({expected_base})と一致しません。"
        )


def _settings() -> dict[str, str]:
    rows = _env_db.fetchall("SELECT key, value FROM env_settings WHERE key IN ('invoke_endpoint', 'invoke_queue_id')")
    return {r["key"]: r["value"] for r in rows}


class InvokeClient:
    """
    Invoke REST APIのラッパークライアント。

    インスタンスはアプリ起動時に1つ作成し、使い回す想定。
    httpxのtimeoutはデフォルト10秒（画像取得時は別途設定）。
    """

    def __init__(self, endpoint: str | None = None, queue_id: str | None = None) -> None:
        cfg = _settings()
        self.endpoint = (endpoint or cfg.get("invoke_endpoint", "http://localhost:9090")).rstrip("/")
        self.queue_id = queue_id or cfg.get("invoke_queue_id", "default")
        self._client = httpx.Client(timeout=10.0)

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> Any:
        url = f"{self.endpoint}{path}"
        try:
            resp = self._client.get(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise InvokeConnectionError(f"Invokeに接続できません: {self.endpoint}") from e
        except httpx.HTTPStatusError as e:
            raise InvokeConnectionError(f"HTTPエラー {e.response.status_code}: {url}") from e
        except httpx.TimeoutException as e:
            raise InvokeConnectionError(f"タイムアウト: {url}") from e

    def _get_bytes(self, path: str, timeout: float = 30.0) -> bytes:
        url = f"{self.endpoint}{path}"
        try:
            resp = self._client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content
        except httpx.ConnectError as e:
            raise InvokeConnectionError(f"Invokeに接続できません: {self.endpoint}") from e
        except httpx.HTTPStatusError as e:
            raise InvokeConnectionError(f"HTTPエラー {e.response.status_code}: {url}") from e
        except httpx.TimeoutException as e:
            raise InvokeConnectionError(f"タイムアウト: {url}") from e

    def _post(self, path: str, json: Any = None, **kwargs) -> Any:
        url = f"{self.endpoint}{path}"
        try:
            resp = self._client.post(url, json=json, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise InvokeConnectionError(f"Invokeに接続できません: {self.endpoint}") from e
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response.text else ""
            # コンソールにフルテキストを出力（デバッグ用）
            import sys
            print(f"\n[PromptMosaic] HTTP {e.response.status_code} ERROR: {url}",
                  file=sys.stderr, flush=True)
            print(f"[PromptMosaic] Response body:\n{body}\n",
                  file=sys.stderr, flush=True)
            # 送信ペイロードも出力（graph 422 の解析用）
            if json is not None and e.response.status_code == 422:
                import json as _json
                try:
                    payload_str = _json.dumps(json, ensure_ascii=False, indent=2)
                    print(f"[PromptMosaic] Sent payload:\n{payload_str}\n",
                          file=sys.stderr, flush=True)
                    # ファイルにも書き出す（ターミナルが見えない場合の保険）
                    _debug_path = Path(__file__).parent.parent / "data" / "debug_last_graph.json"
                    _debug_path.parent.mkdir(parents=True, exist_ok=True)
                    _debug_path.write_text(payload_str, encoding="utf-8")
                    print(f"[PromptMosaic] Graph saved to: {_debug_path}", file=sys.stderr, flush=True)
                except Exception:
                    pass
            raise InvokeConnectionError(
                f"HTTPエラー {e.response.status_code}: {url}\n{body[:800]}"
            ) from e
        except httpx.TimeoutException as e:
            raise InvokeConnectionError(f"タイムアウト: {url}") from e

    # ------------------------------------------------------------------
    # 接続確認
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Invokeが起動しているか確認する。True=OK, False or 例外=NG。"""
        try:
            self._get("/api/v1/queue/default/status")
            return True
        except InvokeConnectionError:
            return False

    # ------------------------------------------------------------------
    # recall: プロンプトの即時転送
    # ------------------------------------------------------------------

    def recall_get(self) -> dict:
        """現在のキャンバスのパラメータを取得する。"""
        return self._get(f"/api/v1/recall/{self.queue_id}")

    def recall_post(self, positive_prompt: str, negative_prompt: str = "") -> dict:
        """
        プロンプトをInvokeのキャンバスに即時反映する。

        Args:
            positive_prompt: 送信するポジティブプロンプト文字列
            negative_prompt: ネガティブプロンプト文字列（省略可）

        Returns:
            APIレスポンスのdict
        """
        payload = {
            "positive_prompt": positive_prompt,
            "negative_prompt": negative_prompt,
        }
        return self._post(f"/api/v1/recall/{self.queue_id}", json=payload)

    # ------------------------------------------------------------------
    # queue: 画像生成
    # ------------------------------------------------------------------

    def queue_status(self) -> dict:
        """キューの状態を取得する。"""
        return self._get(f"/api/v1/queue/{self.queue_id}/status")

    def queue_current(self) -> dict:
        """現在処理中のキューアイテムを取得する。"""
        return self._get(f"/api/v1/queue/{self.queue_id}/current")

    def enqueue_batch(self, batch: dict) -> dict:
        """
        画像生成をキューに追加する（自動実行）。

        Args:
            batch: Invoke enqueue_batch API形式のdict
                   {"batch": {"graph": {...}, "runs": 1, ...}}

        Returns:
            APIレスポンスのdict
        """
        return self._post(f"/api/v1/queue/{self.queue_id}/enqueue_batch", json=batch)

    def cancel_queue_item(self, item_id: int) -> None:
        """
        キューアイテムを1件キャンセルする。

        PromptMosaic が発行した item_id だけを個別にキャンセルする用途
        （キュー全クリアは使わない＝Invoke 側で手動投入されたジョブを巻き込まない）。
        完了済み・キャンセル済みアイテムへの呼び出しは Invoke 側で無害に処理される。
        """
        url = f"{self.endpoint}/api/v1/queue/{self.queue_id}/i/{item_id}/cancel"
        try:
            resp = self._client.put(url, timeout=10.0)
            resp.raise_for_status()
        except httpx.ConnectError as e:
            raise InvokeConnectionError(f"Invokeに接続できません: {self.endpoint}") from e
        except httpx.HTTPStatusError as e:
            raise InvokeConnectionError(f"HTTPエラー {e.response.status_code}: {url}") from e
        except httpx.TimeoutException as e:
            raise InvokeConnectionError(f"タイムアウト: {url}") from e

    @staticmethod
    def _batch_field_node_ids(nodes: dict) -> tuple[str | None, str | None]:
        """Invoke batch data で差し替える positive prompt / seed ノードIDを探す。"""
        prompt_node_id: str | None = None
        seed_node_id: str | None = None
        for node_id, node in nodes.items():
            ntype = node.get("type")
            if prompt_node_id is None and ntype == "string" and node_id.startswith("positive_prompt"):
                prompt_node_id = node_id
            if seed_node_id is None and ntype == "integer" and node_id.startswith("seed"):
                seed_node_id = node_id
            if prompt_node_id and seed_node_id:
                break
        return prompt_node_id, seed_node_id

    # ------------------------------------------------------------------
    # images: 画像取得
    # ------------------------------------------------------------------

    def images_list(
        self,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at",
        order_dir: str = "DESC",
        is_intermediate: bool = False,
    ) -> dict:
        """
        生成画像の一覧を取得する。

        Returns:
            {"items": [...], "total": int}
        """
        params = {
            "limit": limit,
            "offset": offset,
            "order_by": order_by,
            "direction": order_dir,
            "is_intermediate": is_intermediate,
        }
        return self._get("/api/v1/images/", params=params)

    def image_metadata(self, image_name: str) -> dict:
        """
        画像のメタデータ（生成パラメータ等）を取得する。

        Args:
            image_name: Invoke側の画像名（例: "abc123.png"）
        """
        return self._get(f"/api/v1/images/i/{image_name}/metadata")

    def image_info(self, image_name: str) -> dict:
        """画像レコード本体（board_id 等を含む）を取得する。"""
        return self._get(f"/api/v1/images/i/{image_name}")

    def image_full(self, image_name: str) -> bytes:
        """画像本体をバイト列で取得する。"""
        return self._get_bytes(f"/api/v1/images/i/{image_name}/full", timeout=60.0)

    def image_thumbnail(self, image_name: str) -> bytes:
        """サムネイル画像をバイト列で取得する。"""
        return self._get_bytes(f"/api/v1/images/i/{image_name}/thumbnail")

    # ------------------------------------------------------------------
    # boards: ボード管理
    # ------------------------------------------------------------------

    def board_create(self, name: str) -> dict:
        """新規ボードを作成する。"""
        return self._post("/api/v1/boards/", params={"board_name": name})

    def boards_list(self, include_archived: bool = False, limit: int = 100) -> list[dict]:
        """Invoke のボード一覧を取得する。"""
        data = self._get(
            "/api/v1/boards/",
            params={"limit": limit, "offset": 0, "include_archived": include_archived},
        )
        if isinstance(data, dict):
            return list(data.get("items") or [])
        if isinstance(data, list):
            return data
        return []

    def board_get(self, board_id: str) -> dict:
        """Invoke のボード詳細を取得する。"""
        return self._get(f"/api/v1/boards/{board_id}")

    def board_add_image(self, board_id: str, image_name: str) -> dict:
        """ボードに画像を追加する。"""
        return self._post("/api/v1/board_images/", json={"board_id": board_id, "image_name": image_name})

    # ------------------------------------------------------------------
    # models
    # ------------------------------------------------------------------

    def models_list(self, model_type: str | None = None, base_model: str | None = None) -> dict:
        """
        インストール済みモデルの一覧を取得する。

        Args:
            model_type: "main" / "lora" / "controlnet" 等（Noneで全種）
            base_model: "sdxl" / "sd-1" 等（Noneで全種）

        Returns:
            {"models": [...]}  ※v2 APIは "items" ではなく "models" キーを使う

        Note:
            Invoke 6.13 でクエリパラメータ名が変更された。
            旧: type / base  →  新: model_type / base_models
            旧パラメータを送ると 0 件が返るため必ず新名称を使う。
        """
        params: dict = {}
        if model_type:
            params["model_type"] = model_type
        if base_model:
            params["base_models"] = base_model
        return self._get("/api/v2/models/", params=params)

    # ------------------------------------------------------------------
    # style_presets
    # ------------------------------------------------------------------

    def style_presets_list(self) -> list[dict]:
        """スタイルプリセット一覧を取得する。"""
        return self._get("/api/v1/style_presets/")

    def fetch_scheduler_map(self) -> dict[str, list[str]]:
        """
        Invoke の OpenAPI 仕様を解析し、ノードタイプ別スケジューラーリストを返す。

        専用エンドポイントが存在しないため /openapi.json から動的に取得する。
        スキーマの scheduler プロパティに enum が定義されているノードを自動収集。

        戻り値例:
            {
                "anima_denoise":   ["euler", "heun", "dpmpp_2m", ...],
                "flux2_denoise":   ["euler", "heun", "lcm"],
                "denoise_latents": ["euler", "euler_a", "dpmpp_2m", ...],
                ...
            }

        接続エラーや解析失敗時は空の dict を返す。
        """
        try:
            spec = self._get("/openapi.json")
        except (InvokeConnectionError, Exception):
            return {}

        schemas = spec.get("components", {}).get("schemas", {})
        result: dict[str, list[str]] = {}

        for schema in schemas.values():
            props = schema.get("properties", {})
            sched_prop = props.get("scheduler", {})
            if not sched_prop:
                continue

            # type フィールドの default 値をノードタイプ識別子として使用
            node_type = props.get("type", {}).get("default", "")
            if not node_type:
                continue

            # scheduler の enum を取得（$ref 解決含む）
            ref = sched_prop.get("$ref", "")
            if ref:
                ref_name = ref.split("/")[-1]
                schedulers = schemas.get(ref_name, {}).get("enum", [])
            else:
                schedulers = sched_prop.get("enum", [])

            if schedulers:
                result[node_type] = schedulers

        return result

    # ------------------------------------------------------------------
    # utilities
    # ------------------------------------------------------------------

    def parse_dynamic_prompts(self, prompt: str) -> dict:
        """
        Dynamic Prompts記法を解析して展開されたプロンプト一覧を返す。

        Args:
            prompt: {option1|option2} 記法を含むプロンプト

        Returns:
            {"prompts": [...], "error": None}
        """
        return self._post("/api/v1/utilities/dynamicprompts", json={"prompt": prompt, "max_prompts": 100})

    # ------------------------------------------------------------------
    # グラフテンプレートを使った生成（enqueue_batch）
    # ------------------------------------------------------------------

    def get_latest_item_id(self) -> int | None:
        """完了済みキューアイテムの最新IDを返す。なければNone。"""
        r = self._get(f"/api/v1/queue/{self.queue_id}/item_ids",
                      params={"limit": 1, "status": "completed"})
        ids = r.get("item_ids", [])
        return ids[0] if ids else None

    def get_queue_item(self, item_id: int) -> dict:
        """キューアイテム詳細（graph含む）を取得する。"""
        return self._get(f"/api/v1/queue/{self.queue_id}/i/{item_id}")

    def get_image_name_for_item(self, item_id: int) -> str | None:
        """
        完了済みキューアイテムから生成画像名を取得する。

        Returns:
            画像名（例: "d9b74999-....png"）、未完了・エラー・取得失敗時は None。
        """
        try:
            item = self.get_queue_item(item_id)
            if item.get("status") != "completed":
                return None
            for result in item.get("session", {}).get("results", {}).values():
                if result.get("type") == "image_output":
                    return result.get("image", {}).get("image_name")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # グラフノードパッチ（内部ヘルパー）
    # ------------------------------------------------------------------

    @staticmethod
    def _patch_nodes(
        nodes: dict,
        positive_prompt: str,
        negative_prompt: str,
        seed: int,
        steps: int | None,
        cfg_scale: float | None,
        scheduler: str | None,
        width: int | None,
        height: int | None,
        model_key: str | None = None,
        model_name: str | None = None,
        model_hash: str | None = None,
        model_base: str | None = None,
        loras_for_metadata: list[dict] | None = None,
        board_id: str | None = None,
        skip_node_ids: set | None = None,
    ) -> bool:
        """
        グラフの nodes dict を in-place でパッチする。

        汎用化により、新しいモデルアーキテクチャも原則コード変更なしで対応。

        検出ロジック:
          positive_prompt ノード : type=string、id が positive_prompt: で始まる
          ネガティブ条件付けノード: negative_prompt:* の string.value、
                                    または neg_prompt: / neg_cond: の prompt フィールド
          シードノード            : type=integer、id が seed: で始まる
          noise ノード            : type=noise (SD1.5/SDXL)
          デノイザーノード (汎用) : type に "denoise" を含む、または denoising_start フィールドを持つ
                                    ※ フィールドの存在チェックで steps/cfg/seed/w/h を柔軟に対応
                                    ※ flux2_denoise のみ scheduler 値を制限
          モデルローダー (汎用)   : type に "model_loader" を含む → model.key を更新
          core_metadata           : type=core_metadata
                                    ※ フィールドの存在チェックで各モデルに汎用対応
                                    ※ core_metadata を更新しないと Invoke が前回 GUI 値を使い回すバグがある

        Returns:
            True if positive_prompt node was found and replaced.
        """
        pos_replaced = False
        base = InvokeClient._detect_base(nodes)
        model_identifier: dict | None = None
        if model_key:
            model_identifier = {
                "key": model_key,
                **({"hash": model_hash} if model_hash is not None else {}),
                **({"name": model_name} if model_name else {}),
                **({"base": model_base} if model_base else {}),
                "type": "main",
            }
        else:
            model_identifier = InvokeClient._primary_model_identifier(nodes)

        skip_node_ids = skip_node_ids or set()
        for node_id, node in nodes.items():
            ntype = node.get("type", "")

            # リファイナー段のノードはテンプレ焼き込みのまま（モデル/Steps/CFG を触らない）
            if node_id in skip_node_ids:
                continue

            if board_id and "board" in node:
                node["board"] = {"board_id": board_id}

            if width is not None and height is not None:
                for width_key, height_key in (
                    ("original_width", "original_height"),
                    ("target_width", "target_height"),
                ):
                    if width_key in node and height_key in node:
                        node[width_key] = width
                        node[height_key] = height

            # ── ポジティブプロンプト（全ベース共通）──────────────────────────
            if ntype == "string" and node_id.startswith("positive_prompt"):
                node["value"] = positive_prompt
                pos_replaced = True

            elif ntype == "string" and node_id.startswith("negative_prompt"):
                node["value"] = negative_prompt

            # ── ネガティブプロンプト（汎用）──────────────────────────────────
            # SD1.5: negative_prompt:* の string、SDXL: sdxl_compel_prompt (neg_cond:*)、
            # Anima: anima_text_encoder (neg_prompt:*) など
            elif node_id.startswith(("neg_prompt", "neg_cond")) and "prompt" in node:
                node["prompt"] = negative_prompt
                if "style" in node:
                    node["style"] = ""

            # ── シードノード（integer ノード）────────────────────────────────
            elif ntype == "integer" and node_id.startswith("seed"):
                node["value"] = seed

            # ── noise ノード（SD1.5 / SDXL）──────────────────────────────────
            elif ntype == "noise":
                node["seed"] = seed
                if width is not None:
                    node["width"] = width
                if height is not None:
                    node["height"] = height

            # ── デノイザー（汎用）─────────────────────────────────────────────
            # type に "denoise" を含むノード、または denoising_start フィールドを持つノード
            # フィールドの存在チェックで各モデルに柔軟対応:
            #   denoise_latents  : seed なし(noise ノード経由)、cfg_scale、steps、scheduler
            #   flux_denoise     : seed あり、guidance、num_steps、w/h、scheduler
            #   flux2_denoise    : seed あり、num_steps、w/h、scheduler（制限あり）
            #   z_image_denoise  : seed あり、guidance_scale、steps、w/h、scheduler
            #   anima_denoise    : seed あり、guidance_scale、steps、w/h、scheduler
            #   将来のモデル     : フィールド名が上記いずれかに準じれば自動対応
            elif "denoise" in ntype or "denoising_start" in node:
                # seed: ノードが seed フィールドを直接持つ場合のみ設定
                # （denoise_latents は noise ノード経由のため seed フィールドなし）
                if "seed" in node:
                    node["seed"] = seed
                if width is not None and "width" in node:
                    node["width"] = width
                if height is not None and "height" in node:
                    node["height"] = height
                # steps / num_steps（どちらかを持つ方に設定）
                if steps is not None:
                    if "steps" in node:
                        node["steps"] = steps
                    elif "num_steps" in node:
                        node["num_steps"] = steps
                # CFG: フィールド名を優先順で自動検出
                # guidance_scale (anima/z-image) → cfg_scale (sdxl/denoise_latents) → guidance (flux)
                # ※ flux2 の cfg_scale は 1.0 固定（負プロンプト経路なし）。入力段階（GUI/プラン編集）で
                #   CFG=1.0 にロックしているため、ここに渡る値も常に 1.0 になる（core.gen_params 参照）。
                if cfg_scale is not None:
                    if "guidance_scale" in node:
                        node["guidance_scale"] = cfg_scale
                    elif "cfg_scale" in node:
                        node["cfg_scale"] = cfg_scale
                    elif "guidance" in node:
                        node["guidance"] = cfg_scale
                # scheduler: モデルによって受付値を制限（Invoke OpenAPI仕様に基づく）
                if scheduler is not None and "scheduler" in node:
                    if ntype in ("flux_denoise", "flux2_denoise", "z_image_denoise"):
                        # flux / flux2 / z-image は euler/heun/lcm のみ
                        node["scheduler"] = scheduler if scheduler in {"euler", "heun", "lcm"} else "euler"
                    elif ntype == "anima_denoise":
                        # anima: euler/heun/dpmpp_2m/dpmpp_2m_sde/er_sde/lcm
                        _ANIMA_OK = {"euler", "heun", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "lcm"}
                        node["scheduler"] = scheduler if scheduler in _ANIMA_OK else "euler"
                    else:
                        node["scheduler"] = scheduler

            # ── モデルローダー（汎用）──────────────────────────────────────────
            # type に "model_loader" を含むすべてのノードに対して model を更新
            elif "model_loader" in ntype:
                if model_identifier:
                    node["model"] = {
                        **(node.get("model") or {}),
                        **model_identifier,
                    }

            # ── core_metadata（汎用）────────────────────────────────────────────
            # フィールドの存在チェックで各モデルに対応（ベース別分岐不要）
            # 省略すると Invoke が前回の GUI 値をフォールバックとして使うため必ず更新する
            elif ntype == "core_metadata":
                node["positive_prompt"] = positive_prompt
                node["seed"] = seed
                if steps is not None:
                    node["steps"] = steps
                if scheduler is not None:
                    node["scheduler"] = scheduler
                if width is not None:
                    node["width"] = width
                if height is not None:
                    node["height"] = height
                # negative_prompt: フィールドが存在する場合のみ（flux 系はなし）
                if "negative_prompt" in node:
                    node["negative_prompt"] = negative_prompt
                # CFG: フィールド名を自動検出（cfg_scale → guidance の順）
                if cfg_scale is not None:
                    if "cfg_scale" in node:
                        node["cfg_scale"] = cfg_scale
                    elif "guidance" in node:
                        node["guidance"] = cfg_scale
                # モデル情報をツール設定値で上書き（PNG に正しいモデル名を記録）
                if model_identifier:
                    node["model"] = {
                        **(node.get("model") or {}),
                        **model_identifier,
                    }
                # LoRA 情報を core_metadata に書き込む（Invoke の画像パラメータ表示用）
                # CoreMetadataInvocation.loras は nullable・任意フィールドのため
                # テンプレートに元々存在しなくても追加可能（OpenAPI 6.13.0.rc2 確認済み）
                # hash は ModelIdentifierField の required フィールド。
                # LoRAbrowser 追加時に DB から取得した invoke_hash を使用する。
                # 古い履歴データ等で hash が無い場合は空文字列でフォールバック。
                if loras_for_metadata is not None:
                    active = [l for l in loras_for_metadata if l.get("enabled", True)]
                    node["loras"] = [
                        {
                            "model": {
                                "key":  l["invoke_key"],
                                "hash": l.get("hash", ""),
                                "name": l.get("name", ""),
                                "base": l.get("base", base),
                                "type": "lora",
                            },
                            "weight": float(l.get("weight", 0.75)),
                        }
                        for l in active
                    ] or None  # 空リストは null にして無駄な空配列送信を避ける

        return pos_replaced

    # ------------------------------------------------------------------
    # グラフテンプレートを使った生成（enqueue_batch）
    # ------------------------------------------------------------------

    def generate_with_prompt(
        self,
        positive_prompt: str,
        negative_prompt: str = "",
        seed: int | None = None,
        steps: int | None = None,
        cfg_scale: float | None = None,
        scheduler: str | None = None,
        width: int | None = None,
        height: int | None = None,
        model_key: str | None = None,
    ) -> dict:
        """
        直近の完了ジョブのグラフを流用し、プロンプト・パラメータを差し替えて1枚生成する。
        複数枚生成する場合は generate_batch() を使うこと（テンプレート取得が1回で済む）。

        Args:
            model_key: Invoke モデルの UUID キー。None のとき既存グラフのモデルをそのまま使う。

        Raises:
            InvokeConnectionError: 接続失敗 / テンプレートが見つからない
        """
        item_id = self.get_latest_item_id()
        if item_id is None:
            raise InvokeConnectionError(
                "テンプレートとなる完了済みジョブが見つかりません。"
                "Invokeで一度手動生成してください。"
            )

        item  = self.get_queue_item(item_id)
        graph = item["session"]["graph"]

        actual_seed = seed if seed is not None else 0
        model_hash = None
        model_base = None
        model_name = None
        if model_key:
            row = _env_db.fetchone(
                "SELECT invoke_hash, base, name FROM models WHERE invoke_key=?",
                (model_key,),
            )
            if row:
                model_hash = row["invoke_hash"] or None
                model_base = row["base"] or None
                model_name = model_name or row["name"]
        ok = self._patch_nodes(
            graph["nodes"], positive_prompt, negative_prompt,
            actual_seed, steps, cfg_scale, scheduler, width, height,
            model_key=model_key,
            model_name=model_name,
            model_hash=model_hash,
            model_base=model_base,
        )
        if not ok:
            raise InvokeConnectionError(
                "グラフ内にpositive_promptノードが見つかりません。"
                "非標準グラフかもしれません。"
            )

        return self.enqueue_batch({"batch": {"graph": graph, "runs": 1}})

    # ------------------------------------------------------------------
    # LoRAグラフ操作ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_base(nodes: dict) -> str:
        """グラフからベースモデル種別を推定する。

        汎用化: *_model_loader 系ノードの model.base フィールドを読む。
        Invoke の命名規則に従う限り、新モデルはコード変更なしで自動対応。
        flux2 のみエンコーダーキーで cache_key を分岐するため個別判定を残す。
        """
        for node in nodes.values():
            ntype = node.get("type", "")
            # flux2 は cache_key 分岐があるため特別扱い
            if ntype == "flux2_klein_model_loader":
                return "flux2"
            # 汎用: *_model_loader 系ノードから model.base を読む
            # (sdxl_model_loader / flux_model_loader / anima_model_loader /
            #  z_image_model_loader / main_model_loader など将来の新モデルも自動対応)
            if "model_loader" in ntype:
                base = node.get("model", {}).get("base", "")
                if base:
                    return base
        return "unknown"  # 未知モデル（sdxl と混同しないよう unknown を返す）

    @staticmethod
    def _detect_encoder_key(nodes: dict) -> str:
        """flux2 / z-image グラフから qwen3_encoder_model.key の先頭8文字を返す。"""
        for node in nodes.values():
            if node.get("type") in ("flux2_klein_model_loader", "z_image_model_loader"):
                key = node.get("qwen3_encoder_model", {}).get("key", "")
                return key[:8] if key else ""
        return ""

    # 主テキストエンコーダのフィールド名（優先順）。アーキ間で名前が違う。
    _ENCODER_FIELDS = ("qwen3_encoder_model", "qwen_vl_encoder_model", "t5_encoder_model")

    @staticmethod
    def _detect_encoder(nodes: dict) -> tuple[str, str]:
        """グラフの主テキストエンコーダの (key先頭8文字, 表示名) を返す。
        モデルローダの qwen3 / qwen_vl / t5 エンコーダフィールドを優先順で探す。
        SDXL / SD1.5 はエンコーダがモデル内蔵で別指定が無いため ("","")。"""
        for node in nodes.values():
            ntype = node.get("type", "")
            if "model_loader" not in ntype or "lora" in ntype:
                continue
            for field in InvokeClient._ENCODER_FIELDS:
                enc = node.get(field)
                if isinstance(enc, dict):
                    key = str(enc.get("key") or "")
                    name = str(enc.get("name") or "")
                    if key or name:
                        return key[:8], name
        return "", ""

    @staticmethod
    def _primary_model_identifier(nodes: dict) -> dict | None:
        """実生成に使われる main model identifier を model_loader から取得する。"""
        for node in nodes.values():
            ntype = node.get("type", "")
            if "model_loader" in ntype and "lora" not in ntype:
                model = node.get("model")
                if isinstance(model, dict) and model.get("key"):
                    return dict(model)
        return None

    @staticmethod
    def _cache_key_from_graph(graph: dict) -> str:
        """グラフから「そのベースの基準キャッシュキー」を返す（= base のみ）。
        テンプレの中身は評価しない方針。設定違いの区別はユーザーが名前で行う。
        自己修復/レガシー経路で「正しいベースのキー」を求めるのに使う。
        新規取得のユニークキーは _new_cache_key_for_base を使う。"""
        nodes = graph.get("nodes", {})
        return InvokeClient._detect_base(nodes).replace("-", "_")

    @staticmethod
    def _new_cache_key_for_base(base: str) -> str:
        """ベースに対する未使用のキャッシュキーを返す。
        1個目は base（例: sdxl）、2個目以降は base_v2, base_v3 …。"""
        safe = base.replace("-", "_")
        candidate = safe
        n = 1
        while (
            _env_db.fetchone("SELECT 1 FROM templates WHERE cache_key=?", (candidate,))
            or InvokeClient._template_cache_path(candidate).exists()
        ):
            n += 1
            candidate = f"{safe}_v{n}"
        return candidate

    @staticmethod
    def _template_cache_path(cache_key: str) -> Path:
        """キャッシュキーからファイルパスを返す。"""
        return Path(__file__).parent.parent / "data" / f"template_cache_{cache_key}.json"

    @staticmethod
    def _strip_lora_nodes(graph: dict) -> None:
        """
        既存の LoRA 関連ノード（lora_selector / anima_lora_loader /
        lora_collector / *_lora_collection_loader）とそれらに繋がるエッジを削除し、
        model_loader → downstream の直接配線を復元する。
        LoRA ノードが 1 つも存在しない場合はグラフを一切変更しない。

        汎用化: フィールド名（unet/clip/clip2 や transformer/qwen3_encoder）を
        ハードコードせず、collection_loader の実際の出力エッジから動的に判断する。
        model_loader の検出も型名ではなく "model_loader" を含み "lora" を含まない
        という条件で行うため、将来の新アーキテクチャにも対応。
        """
        nodes = graph["nodes"]
        edges = graph["edges"]

        # セレクター系（lora_selector / anima_lora_loader 等）
        lora_selector_types = {"lora_selector", "anima_lora_loader"}
        # コレクションローダー系（型ごとに intercept フィールドが異なる）
        collection_loader_types = {
            "sdxl_lora_collection_loader",
            "lora_collection_loader",
            "flux_lora_collection_loader",
            "flux2_klein_lora_collection_loader",
            "anima_lora_collection_loader",
            "qwen_image_lora_collection_loader",
            "z_image_lora_collection_loader",
        }
        lora_node_types = lora_selector_types | collection_loader_types
        collector_types = {"collect"}

        # LoRA ノードが存在しない場合は早期リターン（グラフを変更しない）
        if not any(n.get("type") in lora_node_types for n in nodes.values()):
            return

        # LoRA 専用 collector の node_id を特定
        # (lora_selector/anima_lora_loader → collector → collection_loader のパターン)
        lora_collector_ids: set[str] = set()
        for edge in edges:
            src_node = nodes.get(edge["source"]["node_id"], {})
            if src_node.get("type") in lora_node_types and edge["destination"]["field"] == "item":
                lora_collector_ids.add(edge["destination"]["node_id"])

        remove_ids: set[str] = set()
        for nid, node in nodes.items():
            ntype = node.get("type", "")
            if ntype in lora_node_types:
                remove_ids.add(nid)
            if ntype in collector_types and nid in lora_collector_ids:
                remove_ids.add(nid)

        # collection_loader が出力していた接続先を収集
        # （unet/clip/clip2、transformer/qwen3_encoder 等をハードコードせず動的に収集）
        remap: dict[str, list[dict]] = {}   # collection_loader_id → [dst edge]
        for edge in edges:
            src_node = nodes.get(edge["source"]["node_id"], {})
            if src_node.get("type") in collection_loader_types:
                remap.setdefault(edge["source"]["node_id"], []).append(edge)

        # model_loader から collection_loader への配線を特定
        # （汎用: "model_loader" を含み "lora" を含まない型名で判定）
        model_to_coll: dict[str, list[dict]] = {}  # collection_loader_id → [src edge]
        for edge in edges:
            src    = edge["source"]
            dst    = edge["destination"]
            src_node = nodes.get(src["node_id"], {})
            src_type = src_node.get("type", "")
            if ("model_loader" in src_type and "lora" not in src_type
                    and dst["node_id"] in remove_ids):
                model_to_coll.setdefault(dst["node_id"], []).append(edge)

        # 新しいエッジ: model_loader → 元の downstream 直結（フィールド名で照合）
        new_edges: list[dict] = []
        for coll_id, src_edges in model_to_coll.items():
            dst_edges = remap.get(coll_id, [])
            for s_edge in src_edges:
                field = s_edge["source"]["field"]
                for d_edge in dst_edges:
                    if d_edge["source"]["field"] == field:
                        new_edges.append({
                            "source":      s_edge["source"],
                            "destination": d_edge["destination"],
                        })

        # 削除対象ノードに関わるエッジを全て除去
        graph["edges"] = [
            e for e in edges
            if e["source"]["node_id"] not in remove_ids
            and e["destination"]["node_id"] not in remove_ids
        ]
        graph["edges"].extend(new_edges)

        # 削除対象ノードを除去
        for nid in remove_ids:
            nodes.pop(nid, None)

    @staticmethod
    def _replace_lora_selectors(graph: dict, active_loras: list[dict]) -> bool:
        """
        テンプレートの lora_selector / anima_lora_loader ノードをユーザー指定の
        LoRA で in-place 置き換える。
        collector / collection_loader / それらに繋がる全エッジはそのまま保持する。

        Invoke Canvas モードでは model_loader の passthrough エッジが
        グラフに存在しないことがあり、_strip_lora_nodes で復元できない。
        このメソッドは selector ノードと lora→collector エッジのみを差し替え、
        その他の接続（model_loader→collection_loader等）を一切変更しないため
        Canvas モードでも正しく動作する。

        対応セレクター型: lora_selector（SDXL/SD1）/ anima_lora_loader（Anima）

        Returns:
            True  : テンプレートにセレクターノードが存在し、置き換え完了
            False : テンプレートにセレクターノードがなかった（fallback が必要）
        """
        import uuid

        nodes = graph["nodes"]
        edges = graph["edges"]

        # 認識するセレクター型（SDXL系 / Anima系）
        selector_node_types = {"lora_selector", "anima_lora_loader"}

        # 既存 selector ノードの node_id を収集
        old_selector_ids = {
            nid for nid, n in nodes.items()
            if n.get("type") in selector_node_types
        }
        if not old_selector_ids:
            return False

        # selector → collector のエッジから collector_id / 接続フィールドを特定する。
        # 同時に「セレクターの出力フィールド名」「collector の入力フィールド名」も控える
        # （決め打ちせずテンプレートの配線をそのまま踏襲するため）。
        collector_id: str | None = None
        selector_out_field = "lora"
        collector_in_field = "item"
        for edge in edges:
            if (edge["source"]["node_id"] in old_selector_ids
                    and edge["destination"]["field"] in ("item", "collection")):
                collector_id = edge["destination"]["node_id"]
                selector_out_field = edge["source"]["field"]
                collector_in_field = edge["destination"]["field"]
                break
        if collector_id is None:
            return False

        # 既存セレクターを「見本」として1つ確保（型・フィールド構造をそのまま複製する）。
        sample_id = next(iter(old_selector_ids))
        sample_selector = deepcopy(nodes[sample_id])

        # 見本から「LoRA モデル参照のフィールド名」と「weight フィールド名」を検出。
        lora_ref_field = "lora"
        weight_field = "weight" if "weight" in sample_selector else None
        for k, v in sample_selector.items():
            if isinstance(v, dict) and ("key" in v or v.get("type") == "lora"):
                lora_ref_field = k
                break

        base = InvokeClient._detect_base(nodes)

        # selector からのエッジを削除し、古い selector ノードも削除
        graph["edges"] = [
            e for e in edges if e["source"]["node_id"] not in old_selector_ids
        ]
        for nid in old_selector_ids:
            nodes.pop(nid, None)

        # 見本を複製して、ユーザー指定 LoRA ごとに新しい selector を作る
        for lora in active_loras:
            sid = f"{sample_selector.get('type', 'lora_selector')}:{uuid.uuid4().hex[:8]}"
            node = deepcopy(sample_selector)
            node["id"] = sid
            node[lora_ref_field] = {
                "key":  lora["invoke_key"],
                "hash": lora.get("hash", ""),  # DB から取得した invoke_hash を使用
                "name": lora.get("name", ""),
                "base": lora.get("base", base),
                "type": "lora",
            }
            if weight_field is not None:
                node[weight_field] = float(lora.get("weight", 0.75))
            nodes[sid] = node
            graph["edges"].append({
                "source":      {"node_id": sid,          "field": selector_out_field},
                "destination": {"node_id": collector_id, "field": collector_in_field},
            })

        return True

    @staticmethod
    def _has_lora_application(graph: dict, expected_count: int | None = None) -> bool:
        """実生成グラフに LoRA selector → collector 経路が存在するか確認する。"""
        nodes = graph.get("nodes", {})
        edges = graph.get("edges", [])
        selector_types = {"lora_selector", "anima_lora_loader"}
        selector_ids = [
            nid for nid, node in nodes.items()
            if node.get("type") in selector_types
        ]
        if expected_count is not None and len(selector_ids) < expected_count:
            return False
        if not selector_ids:
            return False
        # _replace_lora_selectors はテンプレの実配線（出力フィールド名・
        # 接続先 "item"/"collection"）を踏襲するため、ここも同じ条件で検証する。
        # source.field を "lora" に決め打ちすると、配線名が異なるテンプレで
        # 注入成功にもかかわらず検証が偽になり LoRA が黙って外されてしまう。
        for sid in selector_ids:
            if not any(
                edge.get("source", {}).get("node_id") == sid
                and edge.get("destination", {}).get("field") in ("item", "collection")
                for edge in edges
            ):
                return False
        return True

    @staticmethod
    def _graph_has_lora_path(graph: dict) -> bool:
        """テンプレートに「見本にできる LoRA 経路」があるか。
        セレクター(lora_selector/anima_lora_loader)が collector("item")へ繋がっていれば True。
        この経路があれば _replace_lora_selectors で任意個の LoRA を差し替え注入できる。"""
        nodes = graph.get("nodes", {})
        edges = graph.get("edges", [])
        selector_types = {"lora_selector", "anima_lora_loader"}
        sel_ids = {nid for nid, n in nodes.items() if n.get("type") in selector_types}
        if not sel_ids:
            return False
        return any(
            e.get("source", {}).get("node_id") in sel_ids
            and e.get("destination", {}).get("field") == "item"
            for e in edges
        )

    # ------------------------------------------------------------------
    # テンプレートキャッシュ（txt2img グラフの保存・再利用）
    # ------------------------------------------------------------------

    @staticmethod
    def _is_txt2img_graph(graph: dict) -> bool:
        """
        グラフが通常の txt2img グラフかどうかを判定する。

        core_metadata ノードの generation_mode に "txt2img" が含まれるとき True を返す。
          例: "txt2img" (SD1.5) / "sdxl_txt2img" (SDXL) → True
          例: "img2img" / "canvas_outpaint" / "canvas_inpaint" 等 → False
        core_metadata が存在しない場合は判断できないため False を返す。
        """
        import sys
        for node in graph.get("nodes", {}).values():
            if node.get("type") == "core_metadata":
                mode = node.get("generation_mode", "")
                print(f"[PromptMosaic] テンプレート generation_mode: {mode!r}",
                      file=sys.stderr, flush=True)
                return "txt2img" in mode
        print("[PromptMosaic] テンプレートに core_metadata ノードが見つかりません",
              file=sys.stderr, flush=True)
        return False

    @staticmethod
    def _graph_has_refiner(graph: dict | None) -> bool:
        """グラフに SDXL リファイナー（2段パイプライン）が含まれるか。
        PromptMosaic は単段 txt2img 専用で、汎用上書きがリファイナーのモデル/Steps/CFG を
        潰してしまうため、取り込み時にこれを弾く。
        検出: type に "refiner" を含むノード、または model.base=="sdxl-refiner" のローダー。"""
        if not graph:
            return False
        for node in graph.get("nodes", {}).values():
            if "refiner" in node.get("type", "").lower():
                return True
            model = node.get("model")
            if isinstance(model, dict) and model.get("base") == "sdxl-refiner":
                return True
        return False

    @staticmethod
    def _graph_supports_negative_prompt(graph: dict | None) -> bool:
        """グラフに実生成へ届くネガティブプロンプト経路があるか判定する。"""
        if not graph:
            return True
        for node_id, node in graph.get("nodes", {}).items():
            if node_id.startswith("negative_prompt") and node.get("type") == "string":
                return True
            if node_id.startswith(("neg_prompt", "neg_cond")) and "prompt" in node:
                return True
        return False

    @staticmethod
    def template_supports_negative(cache_key: str | None) -> bool:
        """保存済みテンプレートがネガティブプロンプトを実生成へ送れるか判定する。"""
        if not cache_key:
            return True
        graph = InvokeClient._load_template(cache_key)
        return InvokeClient._graph_supports_negative_prompt(graph)

    @staticmethod
    def _detect_vae(nodes: dict) -> tuple[str, str]:
        """グラフに設定されている VAE の (key先頭8文字, 表示名) を返す。
        vae_model フィールドを持つノード（model_loader / vae_loader）から取得する。
        モデル内蔵で別VAE指定が無い場合（sdxl/sd-1 既定など）は ("","")。"""
        for node in nodes.values():
            if "vae_model" in node:
                vm = node.get("vae_model") or {}
                key = str(vm.get("key") or "")
                name = str(vm.get("name") or "")
                if key or name:
                    return key[:8], name
        return "", ""

    @staticmethod
    def _refiner_node_ids(graph: dict | None) -> set:
        """SDXL リファイナー段に属するノードID集合（実生成の上書き対象から除外する）。
        - type に "refiner" を含むノード（refiner compel など）
        - model.base=="sdxl-refiner" のモデルローダー
        - そのローダーの unet/transformer 出力を受け取る denoise ノード（リファイナー段）
        """
        ids: set = set()
        if not graph:
            return ids
        nodes = graph.get("nodes", {})
        loader_ids: set = set()
        for nid, n in nodes.items():
            ntype = n.get("type", "").lower()
            if "refiner" in ntype:
                ids.add(nid)
            model = n.get("model")
            if "model_loader" in ntype and isinstance(model, dict) and model.get("base") == "sdxl-refiner":
                ids.add(nid)
                loader_ids.add(nid)
        for e in graph.get("edges", []):
            src = e.get("source", {})
            dst = e.get("destination", {})
            if src.get("node_id") in loader_ids and dst.get("field") in ("unet", "transformer"):
                ids.add(dst.get("node_id"))
        return ids

    @staticmethod
    def _register_template_if_absent(graph: dict, cache_key: str) -> int | None:
        """
        キャッシュキーが templates テーブルに未登録ならベース既定として登録する。
        登録されていれば何もしない。登録時は templates.id を返す。
        """
        row = _env_db.fetchone(
            "SELECT id FROM templates WHERE cache_key=?", (cache_key,),
        )
        if row:
            return row["id"]
        # base を決定
        base = InvokeClient._detect_base(graph.get("nodes", {}))
        # このベースに既定が無ければ新規を既定に
        has_default = _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND is_base_default=1", (base,),
        )
        is_default = 0 if has_default else 1
        name = "Default" if is_default else cache_key
        # UNIQUE(base, name) 対策
        suffix = 1
        base_name = name
        while _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND name=?", (base, name),
        ):
            suffix += 1
            name = f"{base_name} ({suffix})"
        cur = _env_db.execute(
            "INSERT INTO templates (name, base, cache_key, is_base_default) VALUES (?, ?, ?, ?)",
            (name, base, cache_key, is_default),
        )
        return cur.lastrowid

    def fetch_template_graph(self, expected_base: str | None = None) -> dict:
        """Invoke の最新完了ジョブの txt2img グラフを取得して返す（保存はしない）。
        中身の評価はせずベースだけ確認する。命名はこの後 UI 側で行い save_fetched_template で保存。

        Returns: {"graph": dict, "base": str}
        Raises:
            InvokeConnectionError
            TemplateBaseMismatch: expected_base と最新ジョブのベースが不一致
        """
        item_id = self.get_latest_item_id()
        if item_id is None:
            raise InvokeConnectionError(
                "テンプレートにできる生成が見つかりません。\n"
                "Invoke で一度画像を生成してください。"
            )
        item = self.get_queue_item(item_id)
        graph = item["session"]["graph"]
        if not self._is_txt2img_graph(graph):
            raise InvokeConnectionError(
                "最新の Invoke の生成は txt2img ではありません。\n"
                "Invoke で txt2img 生成を1回行ってから再度お試しください。"
            )
        base = self._detect_base(graph.get("nodes", {}))
        if expected_base is not None and base != expected_base:
            raise TemplateBaseMismatch(expected_base, base)
        # LoRA は「テンプレの LoRA 経路を見本に」差し替え注入する方式。
        # そのため取得する生成には LoRA が1つ以上含まれている必要がある。
        if not self._graph_has_lora_path(graph):
            raise InvokeConnectionError(
                "直近の生成に LoRA が含まれていません。\n"
                "PromptMosaic は取得するテンプレートの LoRA 経路を見本にするため、\n"
                "Invoke で LoRA を1つ以上使って画像を生成してから、再度取得してください。"
            )
        return {"graph": graph, "base": base}

    @staticmethod
    def suggested_template_name(base: str) -> str:
        """そのベースの新規テンプレ候補名。1個目=Default、2個目以降=Default2, Default3…。"""
        cnt = _env_db.fetchone("SELECT COUNT(*) AS c FROM templates WHERE base=?", (base,))
        n = int(cnt["c"] or 0) if cnt else 0
        return "Default" if n == 0 else f"Default{n + 1}"

    @staticmethod
    def save_fetched_template(graph: dict, base: str, name: str | None = None) -> dict:
        """取得したグラフを「常に新しいテンプレート」として保存する。
        中身の評価・重複判定はしない（同一でも別行として登録される）。
        name 未指定なら suggested_template_name。同ベース内で名前衝突したら連番付与。"""
        name = (name or "").strip() or InvokeClient.suggested_template_name(base)
        cache_key = InvokeClient._new_cache_key_for_base(base)
        InvokeClient._save_template(graph, cache_key)
        is_default = 0 if _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND is_base_default=1", (base,)
        ) else 1
        final_name = name
        n = 1
        while _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND name=?", (base, final_name)
        ):
            n += 1
            final_name = f"{name} ({n})"
        cur = _env_db.execute(
            "INSERT INTO templates (name, base, cache_key, is_base_default) VALUES (?, ?, ?, ?)",
            (final_name, base, cache_key, is_default),
        )
        return {
            "template_id": cur.lastrowid, "name": final_name,
            "base": base, "cache_key": cache_key,
        }

    @staticmethod
    def delete_template(template_id: int) -> None:
        """テンプレート行を削除し、他に使われていなければキャッシュファイルも消す。"""
        row = _env_db.fetchone("SELECT cache_key FROM templates WHERE id=?", (template_id,))
        if not row:
            return
        cache_key = row["cache_key"]
        _env_db.execute("DELETE FROM templates WHERE id=?", (template_id,))
        still = _env_db.fetchone(
            "SELECT 1 FROM templates WHERE cache_key=? LIMIT 1", (cache_key,)
        )
        if not still:
            try:
                path = InvokeClient._template_cache_path(cache_key)
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    @staticmethod
    def rename_template(template_id: int, new_name: str) -> bool:
        """テンプレート名を変更する。同ベース内で重複したら False。"""
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        row = _env_db.fetchone("SELECT base FROM templates WHERE id=?", (template_id,))
        if not row:
            return False
        dup = _env_db.fetchone(
            "SELECT 1 FROM templates WHERE base=? AND name=? AND id!=?",
            (row["base"], new_name, template_id),
        )
        if dup:
            return False
        _env_db.execute(
            "UPDATE templates SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_name, template_id),
        )
        return True

    @staticmethod
    def _extract_model_invoke_key(graph: dict) -> str | None:
        """グラフ内のモデルローダーノードから Invoke モデルの invoke_key を取得する。
        汎用化: "model_loader" を含み "lora" を含まない型名で検出。
        """
        for node in graph.get("nodes", {}).values():
            ntype = node.get("type", "")
            if "model_loader" in ntype and "lora" not in ntype:
                key = node.get("model", {}).get("key", "")
                if key:
                    return key
        return None

    @staticmethod
    def _base_from_cache_key(cache_key: str) -> str:
        """キャッシュキーから期待されるモデルベース名を返す。

        例: sdxl→sdxl, anima→anima, z_image→z-image, flux→flux, flux2_*→flux2, sd_1→sd-1
        VAE/リファイナーのサフィックス（_vae<hex> / _rf）は base 判定の前に取り除く。
        """
        import re as _re
        core = cache_key
        # テンプレ複製時のサフィックス _copy2, _copy3 …（入れ子含む）を取り除く
        # （settings_dialog の複製が {cache_key}_copy{n} 形式のキーを生成する）
        core = _re.sub(r"(_copy[0-9]+)+$", "", core)
        # 複数テンプレ用の連番サフィックス _v2, _v3 … を取り除く
        core = _re.sub(r"_v[0-9]+$", "", core)
        if core.startswith("flux2"):
            return "flux2"
        # 旧フォーマット互換: VAE/エンコーダ/リファイナーのサフィックスも剥がす
        if core.endswith("_rf"):
            core = core[:-3]
        core = _re.sub(r"_vae[0-9a-f]+$", "", core)
        core = _re.sub(r"_enc[0-9a-f]+$", "", core)
        mapping = {
            "sdxl":       "sdxl",
            "sd_1":       "sd-1",
            "flux":       "flux",
            "z_image":    "z-image",
            "anima":      "anima",
            "qwen_image": "qwen-image",
        }
        return mapping.get(core, core.replace("_", "-"))

    @staticmethod
    def _save_template(graph: dict, cache_key: str) -> None:
        """txt2img テンプレートグラフをキャッシュキー別ファイルに保存する。

        グラフの実際のベースがキャッシュキーの期待ベースと一致しない場合は保存しない。
        """
        import sys
        actual_base = InvokeClient._detect_base(graph.get("nodes", {}))
        expected_base = InvokeClient._base_from_cache_key(cache_key)
        if actual_base != expected_base:
            # 正しいキーで保存し直す
            correct_key = InvokeClient._cache_key_from_graph(graph)
            print(
                f"[PromptMosaic] 警告: cache_key={cache_key!r} に対してグラフのベースが "
                f"{actual_base!r}（期待: {expected_base!r}）です。"
                f"正しいキー {correct_key!r} で保存します。",
                file=sys.stderr, flush=True,
            )
            cache_key = correct_key
        try:
            path = InvokeClient._template_cache_path(cache_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _json.dumps(graph, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[PromptMosaic] テンプレートキャッシュの保存に失敗: {exc}",
                  file=sys.stderr, flush=True)
            return

    @staticmethod
    def _load_template(cache_key: str) -> dict | None:
        """
        キャッシュキーに対応する txt2img テンプレートグラフを読み込む。

        読み込んだグラフのベースがキャッシュキーの期待ベースと一致しない場合は:
          1. 壊れたファイルを .invalid_YYYYMMDD.json にリネームする。
          2. 実際のベースに対応する正しいキーでグラフを保存し直す。
          3. 要求されたキーに対しては None を返す（下流で別途修復を試みる）。

        sdxl の場合は旧形式 template_cache.json からの一回限りの移行も行う。

        Returns:
            graph dict、またはキャッシュが存在しない/読み込めない場合は None。
        """
        import sys
        from datetime import datetime as _dt

        path = InvokeClient._template_cache_path(cache_key)

        if path.exists():
            try:
                graph = _json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[PromptMosaic] テンプレートキャッシュの読み込みに失敗: {exc}",
                      file=sys.stderr, flush=True)
                return None

            # ベースの整合チェック
            actual_base   = InvokeClient._detect_base(graph.get("nodes", {}))
            expected_base = InvokeClient._base_from_cache_key(cache_key)

            if actual_base != expected_base:
                # ファイルが壊れている（別ベースのグラフが混入）
                stamp = _dt.now().strftime("%Y%m%d")
                invalid_name = path.with_name(
                    path.stem + f".invalid_{actual_base}_{stamp}.json"
                )
                try:
                    path.rename(invalid_name)
                    print(
                        f"[PromptMosaic] テンプレートキャッシュ不整合を検出: "
                        f"{path.name} のグラフ実ベース={actual_base!r}（期待: {expected_base!r}）。"
                        f"ファイルを {invalid_name.name} にリネームしました。",
                        file=sys.stderr, flush=True,
                    )
                except Exception as exc:
                    print(f"[PromptMosaic] 壊れたキャッシュのリネームに失敗: {exc}",
                          file=sys.stderr, flush=True)

                # 救済: 正しいキーでグラフを保存（既存ファイルを上書きしない場合のみ）
                correct_key  = InvokeClient._cache_key_from_graph(graph)
                correct_path = InvokeClient._template_cache_path(correct_key)
                if not correct_path.exists():
                    try:
                        correct_path.parent.mkdir(parents=True, exist_ok=True)
                        correct_path.write_text(
                            _json.dumps(graph, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        print(
                            f"[PromptMosaic] 救済: グラフを正しいキー {correct_key!r} "
                            f"({correct_path.name}) として保存しました。",
                            file=sys.stderr, flush=True,
                        )
                    except Exception as exc:
                        print(f"[PromptMosaic] 救済保存に失敗: {exc}",
                              file=sys.stderr, flush=True)

                # 要求されたキーに対してはファイルなしとして下流へ（下記の legacy 移行を試みる）
            else:
                return graph

        # sdxl の場合のみ旧 template_cache.json から移行を試みる
        if cache_key == "sdxl":
            legacy = Path(__file__).parent.parent / "data" / "template_cache.json"
            if legacy.exists():
                try:
                    graph = _json.loads(legacy.read_text(encoding="utf-8"))
                    # 移行元ファイルのベースも念のちゃんと確認
                    legacy_base = InvokeClient._detect_base(graph.get("nodes", {}))
                    if legacy_base == "sdxl":
                        InvokeClient._save_template(graph, "sdxl")
                        print("[PromptMosaic] template_cache.json → template_cache_sdxl.json に移行しました。",
                              file=sys.stderr, flush=True)
                        return graph
                    else:
                        print(
                            f"[PromptMosaic] 旧 template_cache.json のベースが {legacy_base!r} のため "
                            "sdxl 移行に使用しません。",
                            file=sys.stderr, flush=True,
                        )
                except Exception as exc:
                    print(f"[PromptMosaic] 旧キャッシュの移行に失敗: {exc}",
                          file=sys.stderr, flush=True)
        return None

    @staticmethod
    def _load_template_for_base(base: str) -> dict | None:
        """
        指定ベースのキャッシュテンプレートを探して返す。

        すべての読み込みは _load_template() 経由で行い、ベース整合チェックを必ず通す。

        flux2 は encoder variant（4B/9B）ごとにテンプレートが異なる。
        まず exact match を試み、なければ template_cache_flux2_*.json を glob し、
        1ファイルだけ見つかった場合はそれを使用する（ユーザーが1種類しか持っていない場合）。
        複数見つかった場合は None を返してユーザーに案内する。
        """
        import sys
        from pathlib import Path as _Path
        safe_base = base.replace("-", "_")

        # exact match: _load_template() を通してベース整合チェックも実施
        graph = InvokeClient._load_template(safe_base)
        if graph is not None:
            return graph

        # flux2 のみ: エンコーダーキー付きファイルを glob で探す
        if base == "flux2":
            data_dir = _Path(__file__).parent.parent / "data"
            candidates = list(data_dir.glob("template_cache_flux2_*.json"))
            # .invalid_* ファイルは除外
            candidates = [c for c in candidates if ".invalid_" not in c.name]
            if len(candidates) == 1:
                # キャッシュキーはファイル名から stem の "template_cache_" 除去部分
                ckey = candidates[0].stem.replace("template_cache_", "")
                graph = InvokeClient._load_template(ckey)
                if graph is not None:
                    print(
                        f"[PromptMosaic] flux2 テンプレートを {candidates[0].name} から読み込みました",
                        file=sys.stderr, flush=True,
                    )
                    return graph
            elif len(candidates) > 1:
                print(
                    f"[PromptMosaic] flux2 テンプレートが複数存在します: "
                    f"{[c.name for c in candidates]}。"
                    "Invoke でそのモデルを使って1枚生成してから再試行してください。",
                    file=sys.stderr, flush=True,
                )
                return None
        print(f"[PromptMosaic] base={base!r} のテンプレートが見つかりません",
              file=sys.stderr, flush=True)
        return None

    def generate_batch(
        self,
        positive_prompt: "list[str] | str",   # リストなら seeds[i] に対応するプロンプトを使用
        negative_prompt: str,
        seeds: list[int],
        steps: int | None = None,
        cfg_scale: float | None = None,
        scheduler: str | None = None,
        width: int | None = None,
        height: int | None = None,
        model_key: str | None = None,
        model_name: str | None = None,
        loras: list[dict] | None = None,
        model_base: str | None = None,
        template_id: int | None = None,
        board_id: str | None = None,
    ) -> list[int]:
        """
        指定されたテンプレートグラフを元に、seeds の各シードでキューに順次追加する。

        テンプレート解決:
          - template_id が指定されていれば、DBの templates.cache_key から
            対応するファイル（data/template_cache_{cache_key}.json）を読み込んで使用する。
            Invoke の最新ジョブは参照しない（保存済みテンプレートが変わらない方が安心）。
          - template_id が None の場合は旧互換動作（Invoke の最新ジョブを取得、
            txt2img なら保存、Canvas/img2img なら既存キャッシュを使用）。

        Args:
            model_key:   Invoke モデルの UUID キー。None のとき既存グラフのモデルをそのまま使う。
            loras:       LoRAリスト [{"invoke_key", "name", "base", "weight", "enabled"}, ...]。
                         None のとき LoRA なし（テンプレートのLoRAも除去して直結）。
            template_id: DB templates.id。指定時はそのテンプレートを使用する。

        Raises:
            InvokeConnectionError
        """
        import sys

        graph_template: dict | None = None

        if template_id is not None:
            row = _env_db.fetchone(
                "SELECT cache_key, name, base FROM templates WHERE id=?", (template_id,),
            )
            if not row:
                raise InvokeConnectionError(
                    f"指定されたテンプレート (id={template_id}) が DB に存在しません。"
                )
            cache_key   = row["cache_key"]
            tmpl_name   = row["name"]
            tmpl_base   = row["base"] or ""   # sqlite3.Row は .get() 非対応

            loaded = self._load_template(cache_key)

            # ── ベース整合チェック & 自動修復 ──────────────────────────────
            # 期待ベース: model_base（呼び出し元が指定）> DB templates.base > cache_key から推定
            expected_base = model_base or tmpl_base or self._base_from_cache_key(cache_key)

            if loaded is not None:
                actual_base = self._detect_base(loaded.get("nodes", {}))
                if actual_base != expected_base:
                    print(
                        f"[PromptMosaic] テンプレート「{tmpl_name}」のグラフベース={actual_base!r} が "
                        f"期待ベース={expected_base!r} と不一致。自動修復を試みます…",
                        file=sys.stderr, flush=True,
                    )
                    loaded = None   # 不正グラフは使わない

            if loaded is None:
                # 修復: expected_base に合うテンプレートを探す
                repaired = self._load_template_for_base(expected_base) if expected_base else None

                if repaired is None and expected_base:
                    # DB 内の他テンプレート行も試す
                    other_rows = _env_db.fetchall(
                        "SELECT cache_key FROM templates WHERE base=? AND id!=?",
                        (expected_base, template_id),
                    )
                    for other in (other_rows or []):
                        candidate = self._load_template(other["cache_key"])
                        if candidate is not None:
                            candidate_base = self._detect_base(candidate.get("nodes", {}))
                            if candidate_base == expected_base:
                                repaired = candidate
                                print(
                                    f"[PromptMosaic] 修復: DB の別テンプレート "
                                    f"(cache_key={other['cache_key']!r}) を使用します。",
                                    file=sys.stderr, flush=True,
                                )
                                break

                if repaired is not None:
                    graph_template = repaired
                    print(
                        f"[PromptMosaic] テンプレート「{tmpl_name}」を自動修復しました "
                        f"（base={expected_base!r}）。",
                        file=sys.stderr, flush=True,
                    )
                else:
                    raise InvokeConnectionError(
                        f"テンプレート「{tmpl_name}」（base={expected_base}）の修復に失敗しました。\n"
                        f"Invoke で {expected_base} モデルを使って txt2img を1枚生成してから、\n"
                        "テンプレート編集画面で「現在のグラフを取得」を実行してください。"
                    )
            else:
                graph_template = loaded

            print(
                f"[PromptMosaic] テンプレート「{tmpl_name}」(cache_key={cache_key}) を使用します。",
                file=sys.stderr, flush=True,
            )
        else:
            # 旧互換: Invoke から取得して自動保存
            item_id = self.get_latest_item_id()
            if item_id is None:
                raise InvokeConnectionError(
                    "テンプレートとなる完了済みジョブが見つかりません。"
                    "Invokeで一度手動生成してください。"
                )

            item = self.get_queue_item(item_id)
            graph_template = item["session"]["graph"]

            if self._is_txt2img_graph(graph_template):
                cache_key = self._cache_key_from_graph(graph_template)
                self._save_template(graph_template, cache_key)
                # templates テーブルにも登録（初回のみ Default として）
                self._register_template_if_absent(graph_template, cache_key)
                print(
                    f"[PromptMosaic] txt2img テンプレートを取得・キャッシュしました。(key={cache_key})",
                    file=sys.stderr, flush=True,
                )
            else:
                cache_key = self._cache_key_from_graph(graph_template)
                cached = self._load_template(cache_key)
                if cached is not None:
                    print(
                        f"[PromptMosaic] Canvas/img2img テンプレートを検出。"
                        f"キャッシュされた txt2img テンプレートを使用します。(key={cache_key})",
                        file=sys.stderr, flush=True,
                    )
                    graph_template = cached
                else:
                    print(
                        "[PromptMosaic] 警告: Canvas/img2img テンプレートですがキャッシュが"
                        "ありません。そのまま使用します（noise/latents サイズ不一致の"
                        "可能性があります）。Invoke で通常の txt2img を一度実行してください。",
                        file=sys.stderr, flush=True,
                    )

            # 旧互換: 選択モデルの base がテンプレートの base と異なる場合の切替え
            if model_base:
                template_base = self._detect_base(graph_template.get("nodes", {}))
                if template_base != model_base:
                    alt = self._load_template_for_base(model_base)
                    if alt is not None:
                        graph_template = alt
                    else:
                        raise InvokeConnectionError(
                            f"選択中のモデル（{model_base}）のテンプレートが見つかりません。\n"
                            f"モデル一覧から再選択するか、テンプレート編集画面から取得してください。"
                        )

        # LoRA の hash を DB から補完する
        # LoRAbar に保存された古いデータや履歴からの復元では hash が欠落している場合がある。
        # ModelIdentifierField.hash は Invoke の required フィールドのため、
        # ここで invoke_key をキーに models テーブルから invoke_hash を取得して補完する。
        if loras:
            enriched: list[dict] = []
            for l in loras:
                if not l.get("hash"):
                    row = _env_db.fetchone(
                        "SELECT invoke_hash FROM models WHERE invoke_key=?",
                        (l["invoke_key"],),
                    )
                    l = {**l, "hash": (row["invoke_hash"] or "") if row else ""}
                enriched.append(l)
            loras = enriched

        selected_model_hash: str | None = None
        selected_model_base: str | None = model_base
        if model_key:
            row = _env_db.fetchone(
                "SELECT invoke_hash, name, base FROM models WHERE invoke_key=?",
                (model_key,),
            )
            if row:
                selected_model_hash = row["invoke_hash"] or None
                selected_model_base = row["base"] or selected_model_base
                model_name = model_name or row["name"]

        pos_values = (
            list(positive_prompt)
            if isinstance(positive_prompt, list)
            else [positive_prompt for _ in seeds]
        )
        if len(pos_values) != len(seeds):
            raise InvokeConnectionError(
                "プロンプト数とシード数が一致しません。"
            )

        def _build_graph_for(pos_for_seed: str, seed_i: int) -> dict:
            graph = deepcopy(graph_template)

            # LoRA の適用戦略を決定する
            graph_base = self._detect_base(graph.get("nodes", {}))
            requested_loras = [l for l in (loras or []) if l.get("enabled", True)]
            active = [
                l for l in requested_loras
                if not l.get("base") or l.get("base") == graph_base
            ]
            if len(active) != len(requested_loras):
                skipped = [
                    l.get("name") or l.get("invoke_key", "")
                    for l in requested_loras
                    if l not in active
                ]
                print(
                    f"[PromptMosaic] 警告: ベースが一致しないLoRAを除外しました "
                    f"(graph_base={graph_base!r}, skipped={skipped!r})",
                    file=sys.stderr, flush=True,
                )
            loras_for_metadata = active
            if active:
                # テンプレートの LoRA 経路を見本に、selector だけユーザー指定 LoRA へ差し替える。
                # （取得時に LoRA 経路の存在を必須化しているので、通常ここで成功する）
                injected = self._replace_lora_selectors(graph, active)
                if not injected or not self._has_lora_application(graph, expected_count=len(active)):
                    # 見本経路が無い/壊れている（旧形式テンプレ等）→ 決め打ち注入は廃止したので
                    # LoRAなしで生成を続行する（実生成と metadata を揃える）。
                    print(
                        "[PromptMosaic] 警告: テンプレートに LoRA 経路がありません。"
                        "LoRAなしで生成を続行します。LoRA入りで取得し直してください。",
                        file=sys.stderr, flush=True,
                    )
                    self._strip_lora_nodes(graph)
                    loras_for_metadata = []
            else:
                # LoRA なし（またはすべて無効）: テンプレートのLoRAを除去
                self._strip_lora_nodes(graph)

            # リファイナー段のノードは上書きしない（モデル/Steps/CFG をテンプレ焼き込みの
            # まま使う）。ベース段だけにパラメータを適用してリファイナーを壊さない。
            skip_ids = self._refiner_node_ids(graph)
            ok = self._patch_nodes(
                graph["nodes"], pos_for_seed, negative_prompt,
                seed_i, steps, cfg_scale, scheduler, width, height,
                model_key=model_key,
                model_name=model_name,
                model_hash=selected_model_hash,
                model_base=selected_model_base,
                loras_for_metadata=loras_for_metadata,
                board_id=board_id,
                skip_node_ids=skip_ids,
            )
            if not ok:
                raise InvokeConnectionError(
                    "グラフ内にpositive_promptノードが見つかりません。"
                    "非標準グラフかもしれません。"
                )
            return graph

        graph = _build_graph_for(pos_values[0], seeds[0])
        prompt_node_id, seed_node_id = self._batch_field_node_ids(graph.get("nodes", {}))

        if len(seeds) > 1 and prompt_node_id and seed_node_id:
            batch_data = [[
                {
                    "node_path": prompt_node_id,
                    "field_name": "value",
                    "items": pos_values,
                },
                {
                    "node_path": seed_node_id,
                    "field_name": "value",
                    "items": seeds,
                },
            ]]
            try:
                _enq_resp = self.enqueue_batch({
                    "batch": {
                        "graph": graph,
                        "runs": 1,
                        "data": batch_data,
                    }
                })
                return _enq_resp.get("item_ids", [])
            except Exception as exc:
                print(
                    f"[PromptMosaic] 警告: 単一バッチ投入に失敗しました。"
                    f"従来の個別投入に戻します: {exc}",
                    file=sys.stderr, flush=True,
                )

        collected_item_ids: list[int] = []
        for idx, seed_i in enumerate(seeds):
            graph = graph if idx == 0 else _build_graph_for(pos_values[idx], seed_i)
            _enq_resp = self.enqueue_batch({"batch": {"graph": graph, "runs": 1}})
            collected_item_ids.extend(_enq_resp.get("item_ids", []))

        return collected_item_ids

    # ------------------------------------------------------------------
    # クリーンアップ
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

