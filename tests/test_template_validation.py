from __future__ import annotations

import sqlite3
import unittest

from api.invoke_client import InvokeClient, TemplateValidationError
from db.init import _upgrade_template_capture_version


def _complete_anima_graph() -> dict:
    nodes = {
        "anima_model_loader:m": {"id": "anima_model_loader:m", "type": "anima_model_loader"},
        "lora_selector:l": {
            "id": "lora_selector:l", "type": "lora_selector",
            "lora": {"key": "lora", "type": "lora"}, "weight": 0.75,
        },
        "lora_collector:lc": {"id": "lora_collector:lc", "type": "collect"},
        "anima_lora_collection_loader:al": {
            "id": "anima_lora_collection_loader:al",
            "type": "anima_lora_collection_loader",
        },
        "positive_prompt:p": {"id": "positive_prompt:p", "type": "string", "value": "cat"},
        "pos_prompt:pe": {"id": "pos_prompt:pe", "type": "anima_text_encoder"},
        "pos_cond_collect:pc": {"id": "pos_cond_collect:pc", "type": "collect"},
        "neg_prompt:ne": {
            "id": "neg_prompt:ne", "type": "anima_text_encoder", "prompt": "bad anatomy",
        },
        "neg_cond_collect:nc": {"id": "neg_cond_collect:nc", "type": "collect"},
        "seed:s": {"id": "seed:s", "type": "integer", "value": 12},
        "denoise_latents:d": {
            "id": "denoise_latents:d", "type": "anima_denoise",
            "denoising_start": 0.0, "guidance_scale": 4.0,
        },
        "core_metadata:md": {
            "id": "core_metadata:md", "type": "core_metadata",
            "generation_mode": "anima_txt2img", "negative_prompt": "bad anatomy",
        },
        "canvas_output:o": {
            "id": "canvas_output:o", "type": "anima_l2i", "is_intermediate": False,
        },
    }
    edge = lambda src, src_field, dst, dst_field: {
        "source": {"node_id": src, "field": src_field},
        "destination": {"node_id": dst, "field": dst_field},
    }
    edges = [
        edge("anima_model_loader:m", "transformer", "anima_lora_collection_loader:al", "transformer"),
        edge("anima_model_loader:m", "qwen3_encoder", "anima_lora_collection_loader:al", "qwen3_encoder"),
        edge("anima_model_loader:m", "vae", "canvas_output:o", "vae"),
        edge("lora_selector:l", "lora", "lora_collector:lc", "item"),
        edge("lora_collector:lc", "collection", "anima_lora_collection_loader:al", "loras"),
        edge("anima_lora_collection_loader:al", "transformer", "denoise_latents:d", "transformer"),
        edge("anima_lora_collection_loader:al", "qwen3_encoder", "pos_prompt:pe", "qwen3_encoder"),
        edge("anima_lora_collection_loader:al", "qwen3_encoder", "neg_prompt:ne", "qwen3_encoder"),
        edge("positive_prompt:p", "value", "pos_prompt:pe", "prompt"),
        edge("pos_prompt:pe", "conditioning", "pos_cond_collect:pc", "item"),
        edge("pos_cond_collect:pc", "collection", "denoise_latents:d", "positive_conditioning"),
        edge("neg_prompt:ne", "conditioning", "neg_cond_collect:nc", "item"),
        edge("neg_cond_collect:nc", "collection", "denoise_latents:d", "negative_conditioning"),
        edge("seed:s", "value", "denoise_latents:d", "seed"),
        edge("seed:s", "value", "core_metadata:md", "seed"),
        edge("positive_prompt:p", "value", "core_metadata:md", "positive_prompt"),
        edge("denoise_latents:d", "latents", "canvas_output:o", "latents"),
        edge("core_metadata:md", "metadata", "canvas_output:o", "metadata"),
    ]
    return {"nodes": nodes, "edges": edges}


class TemplateValidationTests(unittest.TestCase):
    def test_complete_anima_template_passes(self) -> None:
        graph = _complete_anima_graph()
        InvokeClient.validate_template_graph(graph, "anima")
        self.assertTrue(InvokeClient._graph_supports_negative_prompt(graph))

    def test_incomplete_anima_template_reports_capture_requirements(self) -> None:
        graph = _complete_anima_graph()
        graph["nodes"].pop("neg_prompt:ne")
        graph["nodes"].pop("neg_cond_collect:nc")
        graph["nodes"]["denoise_latents:d"]["guidance_scale"] = 1.0
        graph["edges"] = [
            edge for edge in graph["edges"]
            if edge["source"]["node_id"] not in {"neg_prompt:ne", "neg_cond_collect:nc"}
            and edge["destination"]["node_id"] not in {"neg_prompt:ne", "neg_cond_collect:nc"}
        ]
        with self.assertRaises(TemplateValidationError) as caught:
            InvokeClient.validate_template_graph(graph, "anima")
        codes = {issue["code"] for issue in caught.exception.issues}
        self.assertIn("missing_negative_path", codes)
        self.assertIn("negative_prompt_empty", codes)
        self.assertIn("cfg_not_above_one", codes)

    def test_removing_lora_keeps_negative_conditioning_path(self) -> None:
        graph = _complete_anima_graph()
        InvokeClient._strip_lora_nodes(graph)
        self.assertTrue(InvokeClient._graph_has_negative_conditioning_path(graph))
        self.assertFalse(any("lora" in node.get("type", "") for node in graph["nodes"].values()))

    def test_flux_single_cfg_control_updates_guidance_not_true_cfg(self) -> None:
        nodes = {
            "positive_prompt:p": {"type": "string", "value": "old"},
            "denoise:d": {
                "type": "flux_denoise", "cfg_scale": 1.0, "guidance": 4.0,
                "denoising_start": 0.0,
            },
        }
        self.assertTrue(
            InvokeClient._patch_nodes(
                nodes, "new", "", 1, 20, 6.5, "euler", 1024, 1024,
            )
        )
        self.assertEqual(nodes["denoise:d"]["cfg_scale"], 1.0)
        self.assertEqual(nodes["denoise:d"]["guidance"], 6.5)

    def test_template_capture_revision_unregisters_legacy_rows_once(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE env_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.execute("CREATE TABLE templates (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO templates (name) VALUES ('Legacy')")
        self.assertTrue(_upgrade_template_capture_version(conn, "2"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0], 0)
        self.assertFalse(_upgrade_template_capture_version(conn, "2"))


if __name__ == "__main__":
    unittest.main()
