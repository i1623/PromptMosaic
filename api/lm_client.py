"""
LM Studio ネイティブ API クライアント（ストリーミング対応）

公式ドキュメント: https://lmstudio.ai/docs/developer/rest/chat

エンドポイント : POST /api/v1/chat  （LM Studio ネイティブ SSE）
モデル一覧     : GET  /v1/models    （OpenAI 互換）

リクエスト形式:
  {
    "model": "...",
    "input": [
      {"type": "image",   "data_url": "data:mime;base64,..."},
      {"type": "message", "content": "..."}
    ],
    "system_prompt":    "...",       ← トップレベルフィールド
    "max_output_tokens": 4096,
    "temperature":       0.8,
    "top_p":             1.0,
    "repeat_penalty":    1.1,
    "stream":            true
  }

SSE イベント種別（全20種）:
  chat.start
  model_load.start / model_load.progress / model_load.end
  prompt_processing.start / prompt_processing.progress / prompt_processing.end
  reasoning.start / reasoning.delta / reasoning.end
  tool_call.start / tool_call.arguments / tool_call.success / tool_call.failure
  message.start / message.delta / message.end
  error
  chat.end
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import httpx


class LMStudioError(Exception):
    pass


# (event_type, content) タプル型エイリアス
#   event_type: "status" | "thinking" | "content" | "done"
Event = tuple[str, str]

PROVIDER_LMSTUDIO = "lmstudio"
PROVIDER_LLAMA_CPP = "llama_cpp"
PROVIDER_OLLAMA = "ollama"
SUPPORTED_PROVIDERS = {PROVIDER_LMSTUDIO, PROVIDER_LLAMA_CPP, PROVIDER_OLLAMA}


@dataclass(frozen=True)
class LMConnectionStatus:
    ok: bool
    provider: str
    endpoint: str
    message: str = ""
    models: list[str] | None = None


def normalize_provider(provider: str | None) -> str:
    value = (provider or PROVIDER_LMSTUDIO).strip().lower().replace("-", "_")
    aliases = {
        "lm_studio": PROVIDER_LMSTUDIO,
        "lmstudio": PROVIDER_LMSTUDIO,
        "llamacpp": PROVIDER_LLAMA_CPP,
        "llama": PROVIDER_LLAMA_CPP,
        "llama_cpp": PROVIDER_LLAMA_CPP,
        "ollama": PROVIDER_OLLAMA,
    }
    return aliases.get(value, PROVIDER_LMSTUDIO)


def _split_channel_delta(delta: str, current_channel: str | None) -> tuple[list[Event], str | None]:
    """Handle models that stream internal channel markers in message.delta."""
    marker = "<|channel|>"
    msg_marker = "<|message|>"
    events: list[Event] = []
    pos = 0

    def _clean(text: str) -> str:
        for token in ("<|start|>", "<|end|>", "<|message|>"):
            text = text.replace(token, "")
        return text

    def _append(channel: str | None, text: str) -> None:
        text = _clean(text)
        if not text or text.strip().lower() in {"assistant", "analysis", "final"}:
            return
        if channel and channel.strip().lower() != "final":
            events.append(("thinking", text))
        else:
            events.append(("content", text))

    while pos < len(delta):
        idx = delta.find(marker, pos)
        if idx < 0:
            _append(current_channel, delta[pos:])
            break
        if idx > pos:
            _append(current_channel, delta[pos:idx])
        pos = idx + len(marker)
        end = delta.find(msg_marker, pos)
        if end < 0:
            name = delta[pos:].strip()
            if name:
                current_channel = name
            break
        current_channel = delta[pos:end].strip()
        pos = end + len(msg_marker)

    return events, current_channel


def _event_text(ev: dict) -> str:
    """Extract streamed text across LM Studio/native/OpenAI-like delta shapes."""
    for key in ("content", "text"):
        value = ev.get(key)
        if isinstance(value, str):
            return value
    delta = ev.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, dict):
        value = delta.get("content") or delta.get("text")
        if isinstance(value, str):
            return value
    choices = ev.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            delta = first.get("delta")
            if isinstance(delta, dict):
                value = delta.get("content") or delta.get("text")
                if isinstance(value, str):
                    return value
            value = first.get("text")
            if isinstance(value, str):
                return value
    return ""


def _openai_delta_text(ev: dict) -> str:
    choices = ev.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if isinstance(delta, dict):
        value = delta.get("content")
        if isinstance(value, str):
            return value
    message = first.get("message")
    if isinstance(message, dict):
        value = message.get("content")
        if isinstance(value, str):
            return value
    value = first.get("text")
    return value if isinstance(value, str) else ""


def _ollama_message_text(ev: dict) -> str:
    message = ev.get("message")
    if isinstance(message, dict):
        value = message.get("content")
        if isinstance(value, str):
            return value
    value = ev.get("response")
    return value if isinstance(value, str) else ""


def translation_fallback_from_thinking(text: str) -> str:
    """Last-resort extraction when a thinking model puts the final answer in reasoning."""
    text = (text or "").strip()
    if not text:
        return ""

    for token in ("<|start|>", "<|end|>", "<|message|>"):
        text = text.replace(token, "")

    lower = text.lower()
    for marker in ("<|channel|>final", "final answer:", "final:", "answer:", "output:", "出力:", "答え:"):
        idx = lower.rfind(marker.lower())
        if idx >= 0:
            text = text[idx + len(marker):].strip()
            break

    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1].strip()
    if "<think>" in text:
        text = text.split("<think>", 1)[0].strip()

    return text.strip().strip("\"`") if any(
        marker in lower
        for marker in ("<|channel|>final", "final answer:", "final:", "answer:", "output:", "出力:", "答え:")
    ) else ""


class LMClient:
    """LM Studio ネイティブ API クライアント。"""

    def __init__(
        self,
        base_url: str = "http://localhost:1234",
        chunk_timeout: float = 60.0,
        provider: str = PROVIDER_LMSTUDIO,
    ):
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        self.base_url      = url
        self.chunk_timeout = chunk_timeout
        self.provider      = normalize_provider(provider)

    # ── モデル一覧 ────────────────────────────────────────────────────

    def check_connection(self) -> LMConnectionStatus:
        """設定された provider / endpoint に接続できるかを確認する。"""
        try:
            models = self.models_list_detailed()
            model_ids = [
                str(m.get("id") or m.get("key") or m.get("name") or "")
                for m in models
                if isinstance(m, dict)
            ]
            model_ids = [m for m in model_ids if m]
            return LMConnectionStatus(
                ok=True,
                provider=self.provider,
                endpoint=self.base_url,
                message="OK",
                models=model_ids,
            )
        except Exception as e:
            return LMConnectionStatus(
                ok=False,
                provider=self.provider,
                endpoint=self.base_url,
                message=str(e),
                models=[],
            )

    def models_list(self) -> list[dict]:
        """設定 provider のモデル一覧を返す。"""
        try:
            if self.provider == PROVIDER_OLLAMA:
                resp = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
                resp.raise_for_status()
                models = resp.json().get("models", [])
                return [
                    {
                        "id": m.get("name") or m.get("model") or "",
                        "key": m.get("name") or m.get("model") or "",
                        "name": m.get("name") or m.get("model") or "",
                        "size": m.get("size"),
                        **m,
                    }
                    for m in models
                    if isinstance(m, dict)
                ]
            resp = httpx.get(f"{self.base_url}/v1/models", timeout=10.0)
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            raise LMStudioError(f"モデル一覧の取得に失敗しました ({self.provider}): {e}") from e

    def models_list_detailed(self) -> list[dict]:
        """設定 provider の詳細モデル一覧を返す。size_bytes 等を含む場合がある。"""
        if self.provider != PROVIDER_LMSTUDIO:
            return self.models_list()
        try:
            resp = httpx.get(f"{self.base_url}/api/v1/models", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data.get("models") or data.get("data") or []
            return data if isinstance(data, list) else []
        except Exception:
            return self.models_list()

    # ── Vision ストリーミング ─────────────────────────────────────────

    def image_to_text_stream(
        self,
        image_path:     str | Path,
        system_prompt:  str,
        user_prompt:    str   = "",
        model:          str   = "",
        max_tokens:     int   = 4096,      # → max_output_tokens
        temperature:    float = 0.8,
        top_p:          float = 1.0,
        repeat_penalty: float = 1.1,       # frequency_penalty の代替
        cancel_flag:    list[bool] | None = None,
    ) -> Generator[Event, None, None]:
        """
        画像を LM Studio Vision モデルで解析し、(event_type, content) を yield する。

        Yields:
            ("status",   str) – 処理フェーズ通知
            ("thinking", str) – reasoning テキストデルタ
            ("content",  str) – 本文テキストデルタ
            ("done",     "")  – 完了
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise LMStudioError(f"画像ファイルが見つかりません: {image_path}")

        # MIME タイプ
        suffix = image_path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",  ".webp": "image/webp",
            ".gif": "image/gif",  ".bmp":  "image/bmp",
        }
        mime     = mime_map.get(suffix, "image/png")
        b64      = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        data_url = f"data:{mime};base64,{b64}"

        # ネイティブ API リクエスト構造
        # input: image → message の順で並べる
        # input の有効 type は "text" | "image"
        # テキストは {"type": "text", "text": "..."} (fieldも"text")
        # 画像は    {"type": "image", "data_url": "data:..."}
        # system_prompt は別途トップレベルフィールドに設定済み
        # 確定済み形式:
        #   テキスト → {"type": "text",  "content": "..."}
        #   画像     → {"type": "image", "data_url": "data:..."}
        input_items: list[dict] = [
            {"type": "image", "data_url": data_url},
            {"type": "text",  "content": user_prompt or "Describe this image."},
        ]

        payload: dict = {
            "input":             input_items,
            "system_prompt":     system_prompt,   # トップレベルフィールド
            "max_output_tokens": max_tokens,
            "temperature":       temperature,
            "top_p":             top_p,
            "repeat_penalty":    repeat_penalty,
            "stream":            True,
            "store":             False,            # チャット履歴を LM Studio 側で保存しない
        }
        if model:
            payload["model"] = model

        # thinking 検出フォールバック用ステート
        _in_think_tag = False
        _channel      = None

        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/v1/chat",
                json=payload,
                timeout=httpx.Timeout(
                    None,
                    connect=10.0,
                    read=self.chunk_timeout,
                ),
            ) as resp:
                # エラーステータスは read() してから raise_for_status()
                if resp.status_code >= 400:
                    resp.read()
                    resp.raise_for_status()

                # SSE パーサー
                # 各イベントは "event: <type>" + "data: <json>" の2行で構成される
                # data の JSON にも "type" フィールドが含まれる
                _event_line_type = ""

                for line in resp.iter_lines():
                    if cancel_flag and cancel_flag[0]:
                        return

                    if not line:
                        _event_line_type = ""
                        continue

                    # "event:" 行 → イベントタイプを記録
                    if line.startswith("event:"):
                        _event_line_type = line[6:].strip()
                        continue

                    # "data:" 行以外はスキップ
                    if not line.startswith("data:"):
                        continue

                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break

                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # event: 行 → JSON の type フィールドの順で優先
                    ev_type: str = _event_line_type or ev.get("type", "")
                    _event_line_type = ""

                    # ── モデルロード ────────────────────────────
                    if ev_type in ("model_load.start",):
                        yield ("status", "モデルをロード中…")
                    elif ev_type == "model_load.end":
                        yield ("status", "モデルのロード完了")

                    # ── プロンプト処理 ───────────────────────────
                    elif ev_type == "prompt_processing.start":
                        yield ("status", "プロンプトを処理中…")
                    elif ev_type == "prompt_processing.end":
                        yield ("status", "生成開始…")

                    # ── Reasoning（ネイティブ thinking）──────────
                    elif ev_type == "reasoning.start":
                        yield ("status", "thinking…")
                    elif ev_type == "reasoning.delta":
                        delta = _event_text(ev)
                        if delta:
                            yield ("thinking", delta)
                    elif ev_type == "reasoning.end":
                        yield ("status", "thinking 完了、本文生成中…")

                    # ── 本文 ────────────────────────────────────
                    elif ev_type == "message.start":
                        pass

                    elif ev_type == "message.delta":
                        delta: str = _event_text(ev)
                        if not delta:
                            continue

                        # フォールバック 1: channel markup used by some thinking models.
                        if "<|channel|>" in delta or _channel is not None:
                            channel_events, _channel = _split_channel_delta(delta, _channel)
                            for ch_ev in channel_events:
                                yield ch_ev
                            continue

                        # フォールバック 2: <think>…</think> タグ
                        if not _in_think_tag and "<think>" in delta:
                            before, _, rest = delta.partition("<think>")
                            if before:
                                yield ("content", before)
                            _in_think_tag = True
                            if "</think>" in rest:
                                think_part, _, after = rest.partition("</think>")
                                if think_part:
                                    yield ("thinking", think_part)
                                _in_think_tag = False
                                if after:
                                    yield ("content", after)
                            else:
                                if rest:
                                    yield ("thinking", rest)
                            continue

                        if _in_think_tag:
                            if "</think>" in delta:
                                think_part, _, after = delta.partition("</think>")
                                if think_part:
                                    yield ("thinking", think_part)
                                _in_think_tag = False
                                if after:
                                    yield ("content", after)
                            else:
                                yield ("thinking", delta)
                            continue

                        # 通常本文
                        yield ("content", delta)

                    elif ev_type in ("message.end", "chat.end"):
                        break

                    elif ev_type == "error":
                        err_msg = ev.get("message", ev.get("content", str(ev)))
                        raise LMStudioError(f"サーバーエラー: {err_msg}")

        except httpx.ReadTimeout as e:
            raise LMStudioError(
                f"チャンクタイムアウト ({self.chunk_timeout:.0f}s)。\n"
                "モデルが応答していません。パラメータ設定の「チャンクTO」値を増やすか、"
                "モデルの状態を確認してください。"
            ) from e
        except httpx.ConnectError as e:
            raise LMStudioError(
                f"接続できません: {self.base_url}\n"
                "LM Studio が起動しているか、エンドポイント設定を確認してください。"
            ) from e
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.text
            except Exception:
                body = f"(status {e.response.status_code})"
            raise LMStudioError(f"API エラー ({e.response.status_code}): {body}") from e
        except LMStudioError:
            raise
        except Exception as e:
            raise LMStudioError(f"予期しないエラー: {e}") from e

        yield ("done", "")

    # ── テキスト翻訳 ストリーミング（ネイティブ /api/v1/chat）────────────

    def translate_stream(
        self,
        text:          str,
        system_prompt: str,
        model:         str   = "",
        temperature:   float = 0.3,
        max_tokens:    int   = 4096,
        cancel_flag:   list[bool] | None = None,
        seed:          int | None = None,
    ) -> Generator[Event, None, None]:
        """
        テキストを LM Studio ネイティブ SSE API でストリーミング翻訳する。

        Yields:
            ("status",   str) – 処理フェーズ通知
            ("thinking", str) – reasoning テキストデルタ
            ("content",  str) – 本文テキストデルタ
            ("done",     "")  – 完了
        """
        if self.provider == PROVIDER_LLAMA_CPP:
            yield from self._translate_stream_openai(
                text, system_prompt, model, temperature, max_tokens, cancel_flag, seed
            )
            return
        if self.provider == PROVIDER_OLLAMA:
            yield from self._translate_stream_ollama(
                text, system_prompt, model, temperature, max_tokens, cancel_flag, seed
            )
            return

        input_items: list[dict] = [
            {"type": "text", "content": text},
        ]
        payload: dict = {
            "input":             input_items,
            "system_prompt":     system_prompt,
            "max_output_tokens": max_tokens,
            "temperature":       temperature,
            "stream":            True,
            "store":             False,
        }
        if model:
            payload["model"] = model

        _in_think_tag = False
        _channel      = None

        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/v1/chat",
                json=payload,
                timeout=httpx.Timeout(
                    None,
                    connect=10.0,
                    read=self.chunk_timeout,
                ),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    resp.raise_for_status()

                _event_line_type = ""

                for line in resp.iter_lines():
                    if cancel_flag and cancel_flag[0]:
                        return

                    if not line:
                        _event_line_type = ""
                        continue

                    if line.startswith("event:"):
                        _event_line_type = line[6:].strip()
                        continue

                    if not line.startswith("data:"):
                        continue

                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break

                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    ev_type: str = _event_line_type or ev.get("type", "")
                    _event_line_type = ""

                    if ev_type in ("model_load.start",):
                        yield ("status", "モデルをロード中…")
                    elif ev_type == "model_load.end":
                        yield ("status", "モデルのロード完了")
                    elif ev_type == "prompt_processing.start":
                        yield ("status", "プロンプトを処理中…")
                    elif ev_type == "prompt_processing.end":
                        yield ("status", "生成開始…")
                    elif ev_type == "reasoning.start":
                        yield ("status", "thinking…")
                    elif ev_type == "reasoning.delta":
                        delta = _event_text(ev)
                        if delta:
                            yield ("thinking", delta)
                    elif ev_type == "reasoning.end":
                        yield ("status", "thinking 完了、本文生成中…")
                    elif ev_type == "message.start":
                        pass
                    elif ev_type == "message.delta":
                        delta: str = _event_text(ev)
                        if not delta:
                            continue

                        if "<|channel|>" in delta or _channel is not None:
                            channel_events, _channel = _split_channel_delta(delta, _channel)
                            for ch_ev in channel_events:
                                yield ch_ev
                            continue

                        if not _in_think_tag and "<think>" in delta:
                            before, _, rest = delta.partition("<think>")
                            if before:
                                yield ("content", before)
                            _in_think_tag = True
                            if "</think>" in rest:
                                think_part, _, after = rest.partition("</think>")
                                if think_part:
                                    yield ("thinking", think_part)
                                _in_think_tag = False
                                if after:
                                    yield ("content", after)
                            else:
                                if rest:
                                    yield ("thinking", rest)
                            continue

                        if _in_think_tag:
                            if "</think>" in delta:
                                think_part, _, after = delta.partition("</think>")
                                if think_part:
                                    yield ("thinking", think_part)
                                _in_think_tag = False
                                if after:
                                    yield ("content", after)
                            else:
                                yield ("thinking", delta)
                            continue

                        yield ("content", delta)

                    elif ev_type in ("message.end", "chat.end"):
                        break

                    elif ev_type == "error":
                        err_msg = ev.get("message", ev.get("content", str(ev)))
                        raise LMStudioError(f"サーバーエラー: {err_msg}")

        except httpx.ReadTimeout as e:
            raise LMStudioError(
                f"チャンクタイムアウト ({self.chunk_timeout:.0f}s)。\n"
                "モデルが応答していません。"
            ) from e
        except httpx.ConnectError as e:
            raise LMStudioError(
                f"接続できません: {self.base_url}\n"
                "LM Studio が起動しているか、エンドポイント設定を確認してください。"
            ) from e
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.text
            except Exception:
                body = f"(status {e.response.status_code})"
            raise LMStudioError(f"API エラー ({e.response.status_code}): {body}") from e
        except LMStudioError:
            raise
        except Exception as e:
            raise LMStudioError(f"予期しないエラー: {e}") from e

        yield ("done", "")

    def classify_stream(
        self,
        text: str,
        system_prompt: str,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 1024,
        seed: int | None = 1,
        cancel_flag: list[bool] | None = None,
    ) -> Generator[Event, None, None]:
        """Stream a short classification response with deterministic settings when supported."""
        if self.provider == PROVIDER_LMSTUDIO and seed is not None and int(seed) > 0:
            yield from self._translate_stream_openai(
                text,
                system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                cancel_flag=cancel_flag,
                seed=seed,
            )
            return
        yield from self.translate_stream(
            text,
            system_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_flag=cancel_flag,
            seed=seed,
        )

    def _translate_stream_openai(
        self,
        text: str,
        system_prompt: str,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        cancel_flag: list[bool] | None = None,
        seed: int | None = None,
    ) -> Generator[Event, None, None]:
        payload: dict = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if model:
            payload["model"] = model
        if seed is not None and int(seed) > 0:
            payload["seed"] = int(seed)

        try:
            yield ("status", "LLMサーバーへ接続中…")
            with httpx.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(None, connect=10.0, read=self.chunk_timeout),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    resp.raise_for_status()
                yield ("status", "生成開始…")
                for line in resp.iter_lines():
                    if cancel_flag and cancel_flag[0]:
                        return
                    if not line:
                        continue
                    raw = line.strip()
                    if raw.startswith("data:"):
                        raw = raw[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    delta = _openai_delta_text(ev)
                    if delta:
                        yield ("content", delta)
        except httpx.ReadTimeout as e:
            raise LMStudioError(f"チャンクタイムアウト ({self.chunk_timeout:.0f}s)。モデルが応答していません。") from e
        except httpx.ConnectError as e:
            raise LMStudioError(
                f"接続できません: {self.base_url}\nローカルLLMサーバー（OpenAI互換）が起動しているか確認してください。"
            ) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response is not None else ""
            raise LMStudioError(f"API エラー ({e.response.status_code}): {body}") from e
        except LMStudioError:
            raise
        except Exception as e:
            raise LMStudioError(f"予期しないエラー: {e}") from e
        yield ("done", "")

    def _translate_stream_ollama(
        self,
        text: str,
        system_prompt: str,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        cancel_flag: list[bool] | None = None,
        seed: int | None = None,
    ) -> Generator[Event, None, None]:
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if seed is not None and int(seed) > 0:
            payload["options"]["seed"] = int(seed)
        if not model:
            raise LMStudioError("Ollamaではモデル名を指定してください。")

        try:
            yield ("status", "Ollamaへ接続中…")
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=httpx.Timeout(None, connect=10.0, read=self.chunk_timeout),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    resp.raise_for_status()
                yield ("status", "生成開始…")
                for line in resp.iter_lines():
                    if cancel_flag and cancel_flag[0]:
                        return
                    if not line:
                        continue
                    try:
                        ev = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    delta = _ollama_message_text(ev)
                    if delta:
                        yield ("content", delta)
                    if ev.get("done"):
                        break
        except httpx.ReadTimeout as e:
            raise LMStudioError(f"チャンクタイムアウト ({self.chunk_timeout:.0f}s)。モデルが応答していません。") from e
        except httpx.ConnectError as e:
            raise LMStudioError(f"接続できません: {self.base_url}\nOllama が起動しているか確認してください。") from e
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response is not None else ""
            raise LMStudioError(f"API エラー ({e.response.status_code}): {body}") from e
        except LMStudioError:
            raise
        except Exception as e:
            raise LMStudioError(f"予期しないエラー: {e}") from e
        yield ("done", "")

    # ── テキスト翻訳（OpenAI互換 /v1/chat/completions）────────────────────

    def translate_text(
        self,
        text: str,
        system_prompt: str,
        model: str = "",
        temperature: float = 0.3,
    ) -> str:
        """
        テキストを翻訳して結果文字列を返す（非ストリーミング）。

        OpenAI互換の /v1/chat/completions エンドポイントを使用する。
        LM Studio が起動していない場合や API エラー時は LMStudioError を送出する。
        """
        payload: dict = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": text},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if model:
            payload["model"] = model

        try:
            resp = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(None, connect=10.0, read=60.0),
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip()
        except httpx.ConnectError as e:
            raise LMStudioError(
                f"接続できません: {self.base_url}\n"
                "LM Studio が起動しているか、設定のURLを確認してください。"
            ) from e
        except httpx.ReadTimeout as e:
            raise LMStudioError("タイムアウト: モデルが応答していません。") from e
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.text
            except Exception:
                body = f"(status {e.response.status_code})"
            raise LMStudioError(f"API エラー ({e.response.status_code}): {body}") from e
        except (KeyError, IndexError) as e:
            raise LMStudioError(f"レスポンス形式エラー: {e}") from e
        except LMStudioError:
            raise
        except Exception as e:
            raise LMStudioError(f"予期しないエラー: {e}") from e
