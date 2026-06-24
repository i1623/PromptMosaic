"""
PNG metadata reader.

Supported sources:
  - Invoke: invokeai_metadata / invokeai / sd-metadata
  - AUTOMATIC1111 / Forge: parameters
  - ComfyUI: prompt / workflow
  - NovelAI: Software=NovelAI, Description, Comment
  - ChatGPT / GPT-4o C2PA: caBX detection only

The public API returns one normalized dict shape so the central pane load flow
can stay source-agnostic.
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any


_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_INVOKE_KEYS = {'invokeai_metadata', 'invokeai', 'sd-metadata'}

_A1111_PARAM_RE = re.compile(
    r'\s*([\w][\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)'
)
_LORA_TAG_RE = re.compile(
    r'<lora:([^:>]+):([+-]?(?:\d+(?:\.\d*)?|\.\d+))>',
    re.IGNORECASE,
)

_SAMPLER_TYPES = {
    "KSampler",
    "KSamplerAdvanced",
    "SamplerCustom",
    "SamplerCustomAdvanced",
    "FaceDetailer",
    "FaceDetailerPipe",
}
_TEXT_ENCODER_MARKERS = (
    "CLIPTextEncode",
    "TextEncode",
)
_STRING_NODE_MARKERS = (
    "PrimitiveString",
    "String",
)


def read_png_meta(path: str | Path) -> dict[str, Any] | None:
    """
    Read PNG generation metadata and return a normalized dict.

    Returns None when the file is not a PNG or no supported metadata exists.
    """
    path = Path(path)
    try:
        chunks = _read_png_chunks(path)
    except Exception:
        return None

    if chunks is None:
        try:
            webp_chunks = _read_webp_chunks(path)
        except Exception:
            return None
        if webp_chunks is None:
            return None
        return _parse_webp_meta(webp_chunks)

    text = _extract_text_chunks(chunks)
    binary_types = {chunk["type"] for chunk in chunks}

    invoke_raw = _extract_invoke_json(text)
    if invoke_raw is not None:
        return _parse_invoke_meta(invoke_raw)

    if (text.get("Software") or "").strip().lower() == "novelai":
        return _parse_novelai_meta(text)

    if "prompt" in text and _looks_like_json_object(text["prompt"]):
        return _parse_comfyui_meta(text["prompt"], text.get("workflow"))

    if "parameters" in text:
        return _parse_a1111_meta(text["parameters"])

    if "caBX" in binary_types:
        return _empty_meta(
            "chatgpt_c2pa",
            raw={"chunk_types": [chunk["type"] for chunk in chunks]},
            warnings=["ChatGPT / GPT-4o C2PA metadata was found, but the prompt is not embedded in the PNG."],
        )

    return None


def _parse_webp_meta(chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    exif_values = [
        _decode_text_value(chunk["data"])
        for chunk in chunks
        if chunk["type"] == "EXIF" and chunk["data"]
    ]
    xmp_values = [
        _decode_text_value(chunk["data"])
        for chunk in chunks
        if chunk["type"] == "XMP " and chunk["data"]
    ]

    for text in exif_values + xmp_values:
        workflow = _extract_json_after_label(text, ("workflow", "Workflow"))
        if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
            return _parse_comfyui_workflow_meta(workflow)

        prompt = _extract_json_after_label(text, ("prompt", "Prompt"))
        if isinstance(prompt, dict):
            # Some tools store the ComfyUI API prompt graph in WebP EXIF/XMP.
            return _parse_comfyui_meta(json.dumps(prompt, ensure_ascii=False), None)

        params = _extract_a1111_parameters_from_text(text)
        if params:
            return _parse_a1111_meta(params)

    return None


def _empty_meta(source_format: str, raw: Any | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "source_format": source_format,
        "positive_prompt": "",
        "negative_prompt": "",
        "model_name": "",
        "model_base": "",
        "model_key": "",
        "model_hash": "",
        "seed": None,
        "cfg_scale": None,
        "steps": None,
        "scheduler": "",
        "width": None,
        "height": None,
        "loras": [],
        "raw": raw or {},
        "warnings": warnings or [],
    }


# ─────────────────────────────────────────────────────────────
# PNG chunk parsing
# ─────────────────────────────────────────────────────────────

def _read_png_chunks(path: Path) -> list[dict[str, Any]] | None:
    data = path.read_bytes()
    if not data.startswith(_SIGNATURE):
        return None

    chunks: list[dict[str, Any]] = []
    pos = 8
    n = len(data)
    while pos + 12 <= n:
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        ctype_bytes = data[pos + 4:pos + 8]
        ctype = ctype_bytes.decode('ascii', errors='replace')
        start = pos + 8
        end = start + length
        if end + 4 > n:
            break
        chunks.append({"type": ctype, "data": data[start:end]})
        pos = end + 4
        if ctype == "IEND":
            break
    return chunks


def _read_webp_chunks(path: Path) -> list[dict[str, Any]] | None:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None

    chunks: list[dict[str, Any]] = []
    pos = 12
    n = len(data)
    while pos + 8 <= n:
        fourcc = data[pos:pos + 4].decode("ascii", errors="replace")
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        start = pos + 8
        end = start + size
        if end > n:
            break
        chunks.append({"type": fourcc, "data": data[start:end]})
        pos = end + (size % 2)
    return chunks


def _extract_text_chunks(chunks: list[dict[str, Any]]) -> dict[str, str]:
    text: dict[str, str] = {}
    for chunk in chunks:
        ctype = chunk["type"]
        data = chunk["data"]
        if ctype == "tEXt":
            parsed = _parse_text_chunk(data)
        elif ctype == "iTXt":
            parsed = _parse_itxt_chunk(data)
        else:
            parsed = None
        if parsed is not None:
            key, value = parsed
            text[key] = value
    return text


def _parse_text_chunk(data: bytes) -> tuple[str, str] | None:
    sep = data.find(b'\x00')
    if sep < 0:
        return None
    key = data[:sep].decode('latin-1', errors='replace')
    value_bytes = data[sep + 1:]
    return key, _decode_text_value(value_bytes)


def _parse_itxt_chunk(data: bytes) -> tuple[str, str] | None:
    sep = data.find(b'\x00')
    if sep < 0:
        return None
    key = data[:sep].decode('latin-1', errors='replace')
    rest = data[sep + 1:]
    if len(rest) < 2:
        return None
    comp_flag = rest[0]
    comp_method = rest[1]
    rest = rest[2:]

    sep2 = rest.find(b'\x00')
    if sep2 < 0:
        return None
    rest = rest[sep2 + 1:]

    sep3 = rest.find(b'\x00')
    if sep3 < 0:
        return None
    text_bytes = rest[sep3 + 1:]

    if comp_flag == 1 and comp_method == 0:
        try:
            text_bytes = zlib.decompress(text_bytes)
        except zlib.error:
            return None
    return key, _decode_text_value(text_bytes)


def _decode_text_value(data: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _looks_like_json_object(value: str) -> bool:
    value = (value or "").lstrip()
    return value.startswith("{") and value.rstrip().endswith("}")


# ─────────────────────────────────────────────────────────────
# Invoke
# ─────────────────────────────────────────────────────────────

def _extract_invoke_json(text: dict[str, str]) -> dict | None:
    for key in _INVOKE_KEYS:
        value = text.get(key)
        if not value:
            continue
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            return raw
    return None


def _parse_invoke_meta(raw: dict) -> dict[str, Any]:
    meta = _empty_meta("invokeai", raw=raw)

    meta["positive_prompt"] = (
        raw.get("positive_prompt")
        or raw.get("prompt")
        or _get_nested(raw, "image", "prompt", 0, "prompt")
        or ""
    )
    meta["negative_prompt"] = (
        raw.get("negative_prompt")
        or _get_nested(raw, "image", "negative_prompt")
        or ""
    )

    model = raw.get("model") or {}
    if isinstance(model, dict):
        meta["model_name"] = model.get("model_name") or model.get("name") or ""
        meta["model_base"] = model.get("base") or model.get("base_model") or ""
        meta["model_key"] = model.get("key") or model.get("id") or ""
        meta["model_hash"] = model.get("hash") or ""
    elif isinstance(model, str):
        meta["model_name"] = model

    meta["seed"] = _num(raw.get("seed"), int)
    meta["cfg_scale"] = _num(raw.get("cfg_scale"), float)
    meta["steps"] = _num(raw.get("steps"), int)
    meta["scheduler"] = raw.get("scheduler") or raw.get("sampler_name") or ""
    meta["width"] = _num(raw.get("width"), int)
    meta["height"] = _num(raw.get("height"), int)
    meta["loras"] = _normalize_loras(raw.get("loras") or [])
    return meta


# ─────────────────────────────────────────────────────────────
# AUTOMATIC1111 / Forge
# ─────────────────────────────────────────────────────────────

def _parse_a1111_meta(parameters: str) -> dict[str, Any]:
    meta = _empty_meta("a1111", raw={"parameters": parameters})
    lines = parameters.splitlines()
    params_line = ""
    prompt_lines = lines
    if lines and len(_A1111_PARAM_RE.findall(lines[-1])) >= 3:
        params_line = lines[-1]
        prompt_lines = lines[:-1]

    positive_lines: list[str] = []
    negative_lines: list[str] = []
    is_negative = False
    for line in prompt_lines:
        if line.startswith("Negative prompt:"):
            is_negative = True
            line = line[len("Negative prompt:"):].lstrip()
        (negative_lines if is_negative else positive_lines).append(line)

    positive = "\n".join(positive_lines).strip()
    loras = _extract_loras_from_prompt(positive)
    positive = _strip_lora_tags(positive)

    params = _parse_a1111_params(params_line)
    meta.update({
        "positive_prompt": positive,
        "negative_prompt": "\n".join(negative_lines).strip(),
        "model_name": params.get("Model", ""),
        "seed": _num(params.get("Seed"), int),
        "cfg_scale": _num(params.get("CFG scale"), float),
        "steps": _num(params.get("Steps"), int),
        "scheduler": params.get("Sampler") or params.get("Schedule type") or "",
        "loras": loras,
        "raw": {"parameters": parameters, "params": params},
    })
    size = params.get("Size") or ""
    match = re.search(r'(\d+)\s*x\s*(\d+)', size, re.IGNORECASE)
    if match:
        meta["width"] = int(match.group(1))
        meta["height"] = int(match.group(2))

    if params.get("Lora hashes") and not loras:
        meta["warnings"].append("LoRA hashes were found, but no <lora:name:weight> tags were present in the prompt.")
    return meta


def _parse_a1111_params(params_line: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in _A1111_PARAM_RE.findall(params_line or ""):
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        params[key] = str(value).strip()
    return params


def _extract_a1111_parameters_from_text(text: str) -> str:
    idx = text.find("Negative prompt:")
    if idx < 0:
        idx = text.find("Steps:")
    if idx < 0:
        return ""
    start = max(0, text.rfind("\n", 0, idx - 1))
    snippet = text[start:].strip()
    steps = snippet.find("Steps:")
    if steps < 0:
        return ""
    return snippet


def _extract_loras_from_prompt(prompt: str) -> list[dict[str, Any]]:
    loras: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for match in _LORA_TAG_RE.finditer(prompt or ""):
        name = match.group(1).strip()
        weight = _num(match.group(2), float)
        if not name or weight is None:
            continue
        key = (name, weight)
        if key in seen:
            continue
        seen.add(key)
        loras.append({"name": name, "weight": weight})
    return loras


def _strip_lora_tags(prompt: str) -> str:
    lines = []
    for line in (prompt or "").splitlines():
        line = _LORA_TAG_RE.sub("", line).strip()
        line = re.sub(r'[ \t]+,\s*', ', ', line)
        line = re.sub(r',\s*,+', ', ', line)
        if line:
            lines.append(line)
    return "\n".join(lines).strip(" ,\n")


# ─────────────────────────────────────────────────────────────
# ComfyUI
# ─────────────────────────────────────────────────────────────

def _parse_comfyui_meta(prompt_json: str, workflow_json: str | None = None) -> dict[str, Any]:
    try:
        graph = json.loads(prompt_json)
    except json.JSONDecodeError:
        return _empty_meta("comfyui", raw={"prompt": prompt_json}, warnings=["ComfyUI prompt JSON could not be parsed."])

    warnings: list[str] = []
    meta = _empty_meta("comfyui", raw={"prompt": graph, "workflow": _safe_json(workflow_json)}, warnings=warnings)
    if not isinstance(graph, dict):
        warnings.append("ComfyUI prompt JSON did not contain a node graph.")
        return meta

    sampler_id, sampler = _find_comfy_sampler(graph)
    if sampler:
        inputs = sampler.get("inputs") or {}
        meta["positive_prompt"] = _resolve_comfy_text(graph, inputs.get("positive"), warnings)
        meta["negative_prompt"] = _resolve_comfy_text(graph, inputs.get("negative"), warnings)
        meta["seed"] = _resolve_comfy_number(graph, inputs.get("seed"), int)
        meta["steps"] = _num(inputs.get("steps"), int)
        meta["cfg_scale"] = _num(inputs.get("cfg"), float)
        meta["scheduler"] = " / ".join(
            str(v) for v in (inputs.get("sampler_name"), inputs.get("scheduler")) if v
        )
        width, height = _find_comfy_size(graph, inputs.get("latent_image"))
        meta["width"] = width
        meta["height"] = height
    else:
        warnings.append("No supported ComfyUI sampler node was found.")

    meta["model_name"] = _find_comfy_model_name(graph, sampler)
    meta["loras"] = _find_comfy_loras(graph)
    if sampler_id is not None:
        meta["raw"]["selected_sampler_id"] = sampler_id
    return meta


def _parse_comfyui_workflow_meta(workflow: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    nodes = workflow.get("nodes") or []
    meta = _empty_meta(
        "comfyui_webp",
        raw={"workflow": workflow},
        warnings=warnings,
    )
    if not isinstance(nodes, list):
        warnings.append("ComfyUI workflow did not contain a node list.")
        return meta

    pos_parts: list[str] = []
    neg_parts: list[str] = []
    models: list[tuple[int, str]] = []
    loras: list[dict[str, Any]] = []
    seed = None
    steps = None
    cfg = None
    sampler = ""
    scheduler = ""
    width = None
    height = None

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        title = str(node.get("title") or "")
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        node_name = str(props.get("Node name for S&R") or "")
        label = " ".join((node_type, title, node_name)).lower()
        values = node.get("widgets_values") if isinstance(node.get("widgets_values"), list) else []
        text_values = [v.strip() for v in values if isinstance(v, str) and v.strip()]

        if any(key in label for key in ("ksampler", "samplercustom", "sampler")):
            sampler_params = _workflow_sampler_params(values)
            seed = seed if seed is not None else sampler_params.get("seed")
            steps = steps if steps is not None else sampler_params.get("steps")
            cfg = cfg if cfg is not None else sampler_params.get("cfg")
            sampler = sampler or sampler_params.get("sampler", "")
            scheduler = scheduler or sampler_params.get("scheduler", "")

        if "latent" in label or "emptylatentimage" in label:
            nums = [_num(v, int) for v in values]
            nums = [n for n in nums if n and 128 <= n <= 4096]
            if len(nums) >= 2:
                width = width or nums[0]
                height = height or nums[1]

        if any(key in label for key in ("checkpoint", "unetloader", "cliploader", "vaeloader")):
            for value in text_values:
                if _looks_like_model_file(value):
                    priority = 0 if ("unetloader" in label or "checkpoint" in label) else 1
                    priority = 2 if any(skip in value.lower() for skip in ("vae", "clip", "upscale", "yolo", "sam_")) else priority
                    models.append((priority, value))

        if "lora" in label or "lllite" in label:
            for value in text_values:
                if _looks_like_model_file(value) and value not in {"None", "none"}:
                    weight = _first_num(values, float, min_value=-10.0, max_value=10.0) or 1.0
                    loras.append({"name": value, "weight": float(weight)})

        if not text_values:
            continue
        if "negative" in label:
            neg_parts.extend(_prompt_like_values(text_values))
        elif _workflow_positive_node(label):
            pos_parts.extend(_prompt_like_values(text_values))

    meta["positive_prompt"] = _join_prompt_parts(pos_parts)
    meta["negative_prompt"] = _join_prompt_parts(neg_parts)
    meta["model_name"] = _choose_workflow_model(models)
    meta["loras"] = _dedupe_loras(loras)
    meta["seed"] = seed
    meta["steps"] = steps
    meta["cfg_scale"] = cfg
    meta["scheduler"] = " / ".join(v for v in (sampler, scheduler) if v)
    meta["width"] = width
    meta["height"] = height

    if not meta["positive_prompt"]:
        warnings.append("ComfyUI workflow was found, but no positive prompt text was confidently extracted.")
    return meta


def _find_comfy_sampler(graph: dict) -> tuple[str | None, dict | None]:
    samplers: list[tuple[int, int, str, dict]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type") or ""
        if class_type in _SAMPLER_TYPES:
            priority = 0 if class_type.startswith("KSampler") else 1
            samplers.append((priority, _node_sort_key(node_id), str(node_id), node))
    if not samplers:
        return None, None
    samplers.sort()
    _, _, node_id, node = samplers[0]
    return node_id, node


def _resolve_comfy_text(graph: dict, value: Any, warnings: list[str], depth: int = 0, seen: set[str] | None = None) -> str:
    if depth > 12:
        warnings.append("ComfyUI graph text traversal reached the depth limit.")
        return ""
    if isinstance(value, str):
        return value
    if not _is_comfy_link(value):
        return ""

    node_id = str(value[0])
    seen = seen or set()
    if node_id in seen:
        warnings.append("ComfyUI graph text traversal hit a cycle.")
        return ""
    seen.add(node_id)

    node = graph.get(node_id)
    if not isinstance(node, dict):
        return ""
    class_type = node.get("class_type") or ""
    inputs = node.get("inputs") or {}

    if any(marker in class_type for marker in _TEXT_ENCODER_MARKERS):
        values = []
        for key in ("text", "text_g", "text_l", "string"):
            resolved = _resolve_comfy_text_value(graph, inputs.get(key), warnings, depth + 1, seen)
            if resolved:
                values.append((key, resolved))
        if values:
            first_key, first_text = values[0]
            for key, text in values[1:]:
                if text != first_text:
                    warnings.append(f"ComfyUI node {node_id} has different {first_key} and {key}; {first_key} was used.")
                    break
            return first_text

    if any(marker in class_type for marker in _STRING_NODE_MARKERS):
        for key in ("text", "string", "value"):
            resolved = _resolve_comfy_text_value(graph, inputs.get(key), warnings, depth + 1, seen)
            if resolved:
                return resolved
        widgets = node.get("widgets_values")
        if isinstance(widgets, list):
            for item in widgets:
                if isinstance(item, str) and item.strip():
                    return item

    for child_value in inputs.values():
        resolved = _resolve_comfy_text(graph, child_value, warnings, depth + 1, seen)
        if resolved:
            return resolved
    return ""


def _resolve_comfy_text_value(graph: dict, value: Any, warnings: list[str], depth: int, seen: set[str]) -> str:
    if isinstance(value, str):
        return value
    if _is_comfy_link(value):
        return _resolve_comfy_text(graph, value, warnings, depth, seen)
    return ""


def _find_comfy_model_name(graph: dict, sampler: dict | None) -> str:
    if sampler:
        linked = _resolve_comfy_model_node(graph, (sampler.get("inputs") or {}).get("model"))
        if linked:
            name = _model_name_from_comfy_node(linked)
            if name:
                return name
    for node in graph.values():
        if isinstance(node, dict):
            name = _checkpoint_name_from_comfy_node(node)
            if name:
                return name
    return ""


def _resolve_comfy_model_node(graph: dict, value: Any, depth: int = 0) -> dict | None:
    if depth > 12 or not _is_comfy_link(value):
        return None
    node = graph.get(str(value[0]))
    if not isinstance(node, dict):
        return None
    if _model_name_from_comfy_node(node):
        return node
    inputs = node.get("inputs") or {}
    return _resolve_comfy_model_node(graph, inputs.get("model"), depth + 1)


def _model_name_from_comfy_node(node: dict) -> str:
    return _checkpoint_name_from_comfy_node(node) or _string_input(node, "model_name", "unet_name")


def _checkpoint_name_from_comfy_node(node: dict) -> str:
    inputs = node.get("inputs") or {}
    return _string_input_from_value(inputs.get("ckpt_name"))


def _string_input(node: dict, *keys: str) -> str:
    inputs = node.get("inputs") or {}
    for key in keys:
        value = _string_input_from_value(inputs.get(key))
        if value:
            return value
    return ""


def _string_input_from_value(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _find_comfy_loras(graph: dict) -> list[dict[str, Any]]:
    loras: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type") or ""
        if "lora" not in class_type.lower():
            continue
        inputs = node.get("inputs") or {}
        found = []
        direct_name = _string_input_from_value(inputs.get("lora_name") or inputs.get("name"))
        if direct_name:
            found.append((direct_name, inputs.get("strength_model") or inputs.get("strength") or inputs.get("weight")))
        for i in range(1, 10):
            switch = str(inputs.get(f"switch_{i}", "On")).lower()
            name = _string_input_from_value(inputs.get(f"lora_name_{i}"))
            if not name or name.lower() in {"none", "null"} or switch == "off":
                continue
            found.append((name, inputs.get(f"model_weight_{i}") or inputs.get(f"clip_weight_{i}")))
        for name, raw_weight in found:
            weight = _num(raw_weight, float) or 1.0
            key = (name, float(weight))
            if key in seen:
                continue
            seen.add(key)
            loras.append({"name": name, "weight": float(weight)})
    return loras


def _find_comfy_size(graph: dict, latent_link: Any) -> tuple[int | None, int | None]:
    node = graph.get(str(latent_link[0])) if _is_comfy_link(latent_link) else None
    candidates = [node] if isinstance(node, dict) else []
    candidates.extend(n for n in graph.values() if isinstance(n, dict))
    for candidate in candidates:
        inputs = candidate.get("inputs") or {}
        width = _resolve_comfy_number(graph, inputs.get("width"), int)
        height = _resolve_comfy_number(graph, inputs.get("height"), int)
        if width and height:
            return width, height
    return None, None


def _resolve_comfy_number(graph: dict, value: Any, typ, depth: int = 0):
    direct = _num(value, typ)
    if direct is not None:
        return direct
    if depth > 8 or not _is_comfy_link(value):
        return None
    node = graph.get(str(value[0]))
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}
    for key in ("int", "float", "seed", "value", "number"):
        direct = _num(inputs.get(key), typ)
        if direct is not None:
            return direct
    for child in inputs.values():
        direct = _resolve_comfy_number(graph, child, typ, depth + 1)
        if direct is not None:
            return direct
    return None


def _is_comfy_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 1
        and isinstance(value[0], (str, int))
    )


def _node_sort_key(node_id: Any) -> int:
    try:
        return int(node_id)
    except (TypeError, ValueError):
        return 10**9


def _workflow_positive_node(label: str) -> bool:
    if any(skip in label for skip in ("negative", "note", "markdown", "saveimage")):
        return False
    return any(
        key in label
        for key in (
            "cliptextencode",
            "primitive",
            "artistpack",
            "prompt",
            "quality",
            "general tags",
            "natural language",
        )
    )


def _prompt_like_values(values: list[str]) -> list[str]:
    prompts: list[str] = []
    for value in values:
        text = value.strip()
        if len(text) < 3:
            continue
        if text.startswith("```") or text.lower().startswith("prompting\n"):
            continue
        if text.lower().startswith(("sampler_name", "workflow", "commentout")):
            continue
        prompts.append(text)
    return prompts


def _join_prompt_parts(parts: list[str]) -> str:
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        text = part.strip(" ,\n")
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return "\n".join(cleaned)


def _choose_workflow_model(models: list[tuple[int, str]]) -> str:
    if not models:
        return ""
    return sorted(models, key=lambda item: item[0])[0][1]


def _looks_like_model_file(value: str) -> bool:
    low = value.lower()
    return low.endswith((".safetensors", ".ckpt", ".pt", ".pth"))


def _first_matching_text(values: list[str], needles: tuple[str, ...]) -> str:
    for value in values:
        low = value.lower()
        if any(needle in low for needle in needles):
            return value
    return ""


def _first_num(values: list[Any], typ, min_value: float | None = None, max_value: float | None = None, keys: tuple[str, ...] = ()):
    for value in values:
        if isinstance(value, dict):
            candidates = [value.get(key) for key in keys] if keys else value.values()
        else:
            candidates = [value]
        for candidate in candidates:
            num = _num(candidate, typ)
            if num is None:
                continue
            if min_value is not None and num < min_value:
                continue
            if max_value is not None and num > max_value:
                continue
            return num
    return None


def _workflow_sampler_params(values: list[Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if len(values) >= 6:
        params["seed"] = _num(values[0], int)
        params["steps"] = _num(values[2], int)
        params["cfg"] = _num(values[3], float)
        if isinstance(values[4], str):
            params["sampler"] = values[4]
        if isinstance(values[5], str):
            params["scheduler"] = values[5]
    if params:
        return params
    text_values = [v for v in values if isinstance(v, str)]
    params["seed"] = _first_num(values, int, keys=("seed",))
    params["steps"] = _first_num(values, int, min_value=2, max_value=200)
    params["sampler"] = _first_matching_text(text_values, ("euler", "dpm", "ddim", "heun", "lcm", "sgm"))
    params["scheduler"] = _first_matching_text(text_values, ("normal", "simple", "karras", "exponential", "sgm"))
    return params


def _dedupe_loras(loras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, float]] = set()
    result: list[dict[str, Any]] = []
    for lora in loras:
        name = str(lora.get("name") or "").strip()
        weight = _num(lora.get("weight"), float) or 1.0
        if not name:
            continue
        key = (name, float(weight))
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "weight": float(weight)})
    return result


# ─────────────────────────────────────────────────────────────
# NovelAI
# ─────────────────────────────────────────────────────────────

def _parse_novelai_meta(text: dict[str, str]) -> dict[str, Any]:
    comment = _safe_json(text.get("Comment"))
    raw = {"text": dict(text), "comment": comment}
    meta = _empty_meta("novelai", raw=raw)
    if isinstance(comment, dict):
        meta["positive_prompt"] = text.get("Description") or comment.get("prompt") or ""
        meta["negative_prompt"] = comment.get("uc") or ""
        meta["seed"] = _num(comment.get("seed"), int)
        meta["cfg_scale"] = _num(comment.get("scale"), float)
        meta["steps"] = _num(comment.get("steps"), int)
        meta["scheduler"] = comment.get("sampler") or ""
        meta["width"] = _num(comment.get("width"), int)
        meta["height"] = _num(comment.get("height"), int)
    else:
        meta["positive_prompt"] = text.get("Description") or ""
        meta["warnings"].append("NovelAI Comment JSON was not available.")
    meta["model_name"] = text.get("Source") or ""
    return meta


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _extract_json_after_label(text: str, labels: tuple[str, ...]) -> Any:
    for label in labels:
        idx = text.find(label)
        if idx < 0:
            continue
        start = text.find("{", idx)
        if start < 0:
            continue
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(text[start:])
            return obj
        except json.JSONDecodeError:
            continue
    return None


def _num(value: Any, typ):
    if value is None or value == "":
        return None
    try:
        return typ(value)
    except (TypeError, ValueError):
        return None


def _normalize_loras(loras_raw: Any) -> list[dict[str, Any]]:
    loras = []
    if not isinstance(loras_raw, list):
        return loras
    for lora in loras_raw:
        if not isinstance(lora, dict):
            continue
        lora_model = lora.get("lora") or lora.get("model") or {}
        name = ""
        key = ""
        hash_value = ""
        if isinstance(lora_model, dict):
            name = lora_model.get("model_name") or lora_model.get("name") or ""
            key = lora_model.get("key") or lora_model.get("id") or ""
            hash_value = lora_model.get("hash") or ""
        elif isinstance(lora_model, str):
            name = lora_model
        weight = _num(lora.get("weight"), float) or 1.0
        if name:
            loras.append({"name": name, "weight": weight, "key": key, "hash": hash_value})
    return loras


def _get_nested(d, *keys):
    cur = d
    for k in keys:
        if cur is None:
            return None
        if isinstance(k, int):
            if isinstance(cur, list) and len(cur) > k:
                cur = cur[k]
            else:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    return cur

