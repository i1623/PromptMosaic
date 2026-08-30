from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

# The developer runtime's original .venv is currently tied to a removed Python
# install.  Core tests run on the available interpreter, so stub the transport
# module; this suite only exercises InvokeClient's pure graph inspection helper.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from api.invoke_client import InvokeClient
from core.prompt_builder import GroupTile, NaturalTextTile, PromptDocument, TagTile
from core.prompt_pack import (
    PACK_VERSION,
    PromptPackError,
    _digest,
    portable_document,
    read_prompt_pack,
    write_prompt_pack,
)


class PromptPackTests(unittest.TestCase):
    def _document(self) -> PromptDocument:
        doc = PromptDocument()
        nested = GroupTile(name="Nested", tiles=[TagTile("nested_off", enabled=False)], enabled=False)
        group = GroupTile(
            name="Main",
            tiles=[TagTile("direct_off", enabled=False), nested, NaturalTextTile("direct text", enabled=False)],
        )
        group.ui_expanded = True
        group.ui_width = 640
        doc.positive.middle.tiles.append(group)
        doc.negative.middle.tiles.append(TagTile("bad hands", enabled=True))
        return doc

    def test_group_all_on_only_changes_direct_non_group_tiles(self) -> None:
        doc = self._document()
        group = doc.positive.middle.tiles[0]
        nested = group.tiles[1]

        self.assertTrue(group.enable_direct_tiles())
        self.assertTrue(group.tiles[0].enabled)
        self.assertTrue(group.tiles[2].enabled)
        self.assertFalse(nested.enabled)
        self.assertFalse(nested.tiles[0].enabled)
        self.assertFalse(group.enable_direct_tiles())

    def test_group_width_roundtrip(self) -> None:
        restored = PromptDocument.from_dict(self._document().to_dict())
        group = restored.positive.middle.tiles[0]
        self.assertEqual(group.ui_width, 640)
        self.assertTrue(group.ui_expanded)

        preset = GroupTile.from_dict(group.to_dict(include_ui_state=False), restore_ui_state=False)
        self.assertEqual(preset.ui_width, 640)
        self.assertFalse(preset.ui_expanded)

    def test_legacy_group_without_group_type_remains_comma_joined(self) -> None:
        legacy = GroupTile(
            name="Legacy",
            tiles=[TagTile("red"), TagTile("sneakers")],
        ).to_dict()
        legacy.pop("group_type")
        restored = GroupTile.from_dict(legacy)
        self.assertEqual(restored.group_type, "normal")
        self.assertEqual(restored.compile(), "red, sneakers")

    def test_connection_group_joins_active_fragments_with_one_space(self) -> None:
        colors = GroupTile(
            name="Colors",
            mode="sequential",
            tiles=[TagTile("red"), TagTile("blue")],
        )
        connection = GroupTile(
            name="Shoes",
            group_type="connection",
            tiles=[colors, TagTile("sneakers"), TagTile("unused", enabled=False)],
        )
        self.assertEqual(connection.compile(), "red sneakers")
        connection.enabled = False
        self.assertEqual(connection.compile(), "")
        self.assertEqual(colors._seq_idx, 1)

    def test_connection_group_is_one_comma_delimited_block_fragment(self) -> None:
        doc = PromptDocument()
        block = doc.positive.middle
        block.tiles.extend(
            [
                TagTile("masterpiece"),
                GroupTile(
                    name="Shoes",
                    group_type="connection",
                    tiles=[TagTile("red"), TagTile("sneakers")],
                ),
                TagTile("standing"),
            ]
        )
        self.assertEqual(doc.compile_positive(), "masterpiece, red sneakers, standing")

    def test_connection_group_roundtrip_and_recursive_candidate_restore(self) -> None:
        nested_random = GroupTile(
            name="Nested random",
            mode="random",
            enabled=False,
            tiles=[TagTile("leather", enabled=False), TagTile("canvas")],
        )
        sequential = GroupTile(
            name="Sequence",
            mode="sequential",
            tiles=[TagTile("new", enabled=False), nested_random],
        )
        ordinary = GroupTile(name="Wrapper", tiles=[sequential])
        connection = GroupTile(
            name="Connected",
            group_type="connection",
            tiles=[ordinary, TagTile("shoes")],
        )

        self.assertTrue(connection.restore_selection_candidates_recursive())
        self.assertTrue(sequential.tiles[0].enabled)
        self.assertTrue(nested_random.enabled)
        self.assertTrue(nested_random.tiles[0].enabled)
        self.assertEqual(ordinary.mode, "none")
        self.assertEqual(connection.group_type, "connection")

        restored = GroupTile.from_dict(connection.to_dict())
        self.assertEqual(restored.group_type, "connection")
        self.assertEqual(restored.compile(), "new shoes")

    def test_portable_document_removes_install_specific_lora_keys(self) -> None:
        doc = self._document()
        group = doc.positive.middle.tiles[0]
        group.lora_source_key = "source-install-group-key"
        group.tiles[0].lora_source_key = "source-install-tile-key"
        portable = portable_document(doc.to_dict())
        restored = PromptDocument.from_dict(portable)
        portable_group = restored.positive.middle.tiles[0]
        self.assertEqual(portable_group.lora_source_key, "")
        self.assertEqual(portable_group.tiles[0].lora_source_key, "")
        self.assertEqual(group.lora_source_key, "source-install-group-key")

    def test_pack_roundtrip_and_hash_rejection(self) -> None:
        payload = {
            "unit": "single_image",
            "document": self._document().to_dict(),
            "memo": "one image",
            "generation": {"loras": []},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_prompt_pack(
                Path(temp_dir) / "sample.promptmosaic-pack",
                payload,
                app_version="test",
            )
            self.assertEqual(read_prompt_pack(path), payload)

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["format_version"], PACK_VERSION)
            data["payload"]["memo"] = "tampered"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(PromptPackError):
                read_prompt_pack(path)

    def test_pack_v1_is_still_accepted(self) -> None:
        payload = {
            "unit": "single_image",
            "document": self._document().to_dict(),
            "memo": "legacy",
            "generation": {},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_prompt_pack(
                Path(temp_dir) / "legacy.promptmosaic-pack",
                payload,
                app_version="legacy",
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            data["format_version"] = 1
            body = {key: value for key, value in data.items() if key != "sha256"}
            data["sha256"] = _digest(body)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(read_prompt_pack(path), payload)

    def test_metadata_only_does_not_claim_negative_prompt_support(self) -> None:
        # metadataへの記録欄だけでは、負条件がdenoiseへ届く保証にならない。
        graph = {
            "nodes": {
                "positive_prompt:test": {"type": "string", "value": ""},
                "core_metadata:test": {
                    "type": "core_metadata",
                    "positive_prompt": "",
                    "negative_prompt": "",
                    "seed": 0,
                },
            }
        }
        self.assertFalse(InvokeClient._graph_supports_negative_prompt(graph))
        self.assertTrue(
            InvokeClient._patch_nodes(
                graph["nodes"],
                "positive test",
                "negative test",
                123,
                None,
                None,
                None,
                None,
                None,
            )
        )
        metadata = next(node for node in graph["nodes"].values() if node.get("type") == "core_metadata")
        self.assertEqual(metadata["negative_prompt"], "negative test")


if __name__ == "__main__":
    unittest.main()
