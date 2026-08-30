from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QMimeData, QPointF, Qt, qInstallMessageHandler
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication, QWidget

    from core.prompt_builder import Block, GroupTile, NaturalTextTile, TagTile
    from ui.block_widget import BlockWidget
    from ui.connection_drag import ConnectionHandle
    from ui.tile_widget import TILE_MIME

    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not available to this interpreter")
class ConnectionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _text(self, value: str, *, enabled: bool = True) -> NaturalTextTile:
        return NaturalTextTile(value, source_text=value, translated_text=value, enabled=enabled)

    def _tag(self, value: str, *, category: str = "") -> TagTile:
        return TagTile(tag_name=value, tag_local=value, category=category)

    def test_connection_handle_styles_parse_without_qt_warnings(self) -> None:
        messages: list[str] = []
        previous_handler = qInstallMessageHandler(
            lambda _message_type, _context, message: messages.append(message)
        )
        try:
            owner = QWidget()
            handle = ConnectionHandle(owner)
            for state in ("normal", "valid", "invalid", "normal"):
                handle.set_drop_state(state)
            self.app.processEvents()
            handle.deleteLater()
            owner.deleteLater()
            self.app.processEvents()
        finally:
            qInstallMessageHandler(previous_handler)

        parse_warnings = [
            message for message in messages if "Could not parse stylesheet" in message
        ]
        self.assertEqual(parse_warnings, [])

    def test_connect_insert_detach_split_and_copy(self) -> None:
        a, b, f = self._text("A"), self._text("B"), self._text("F")
        block = Block(tiles=[a, b])
        widget = BlockWidget(block)
        widget.resize(900, 480)
        widget.show()
        self.app.processEvents()

        widget._connect_owner_tiles(widget._tile_widgets[0], widget._tile_widgets[1], after=False)
        self.app.processEvents()
        connection = block.tiles[0]
        self.assertIsInstance(connection, GroupTile)
        self.assertEqual(connection.group_type, "connection")
        self.assertEqual(connection.tiles, [a, b])
        connection_widget = widget._tile_widgets[0]
        self.assertTrue(connection_widget.property("flow_full_row"))
        self.assertIsNotNone(connection_widget._chain_scroll)

        block.tiles.append(f)
        widget.reload()
        self.app.processEvents()
        connection_widget = widget._tile_widgets[0]
        source_widget = widget._tile_widgets[1]
        target_widget = connection_widget.find_widget_for_tile(b)
        widget._connect_owner_tiles(source_widget, target_widget, after=True)
        self.assertEqual(connection.tiles, [a, b, f])

        widget._detach_connection_tile(b, connection)
        self.assertEqual(connection.tiles, [a, f])
        self.assertIs(block.tiles[1], b)

        widget._split_connection_group(connection, 1)
        self.assertEqual(block.tiles[0].tiles, [a])
        self.assertEqual(block.tiles[1].tiles, [f])
        self.assertIs(block.tiles[2], b)

        source_connection_widget = widget._tile_widgets[0]
        widget._on_group_copy_requested(source_connection_widget)
        copied = block.tiles[1]
        self.assertEqual(copied.group_type, "connection")
        self.assertFalse(copied.enabled)
        self.assertIsNot(copied.tiles[0], a)
        widget.close()

    def test_compact_child_and_recursive_restore_button(self) -> None:
        nested = GroupTile(
            name="靴の色グループ",
            mode="random",
            tiles=[self._text("red", enabled=False), self._text("blue")],
        )
        nested.ui_width = 460
        connection = GroupTile(
            name="Shoes",
            group_type="connection",
            tiles=[nested, self._text("sneakers")],
        )
        connection.ui_expanded = True
        block = Block(tiles=[connection])
        widget = BlockWidget(block)
        widget.resize(900, 480)
        widget.show()
        self.app.processEvents()

        connection_widget = widget._tile_widgets[0]
        nested_widget = connection_widget.find_widget_for_tile(nested)
        self.assertEqual(nested_widget.width(), 102)
        self.assertIn("靴", nested_widget._name_lbl.text())
        self.assertIn("靴の色グループ", nested_widget._name_lbl.toolTip())
        collapsed_connection_height = connection_widget.height()

        connection_widget._turn_direct_children_on()
        self.assertTrue(nested.tiles[0].enabled)
        nested_widget.ensure_expanded()
        self.app.processEvents()
        self.assertEqual(nested_widget.width(), 460)
        self.assertGreater(connection_widget.height(), collapsed_connection_height)
        widget.close()

    def test_generation_state_refresh_preserves_nested_group_geometry(self) -> None:
        nested = GroupTile(
            name="colors",
            mode="random",
            tiles=[self._text("red"), self._text("blue"), self._text("black")],
        )
        nested.ui_expanded = True
        nested.ui_width = 300
        tall_group = GroupTile(
            name="many colors",
            tiles=[self._text(f"long-color-name-{i}") for i in range(12)],
        )
        tall_group.ui_expanded = True
        tall_group.ui_width = 300
        connection = GroupTile(
            name="Shoes",
            group_type="connection",
            tiles=[nested, tall_group],
        )
        connection.ui_expanded = True
        widget = BlockWidget(Block(tiles=[connection]))
        widget.resize(900, 520)
        widget.show()
        for _ in range(5):
            self.app.processEvents()

        connection_widget = widget._tile_widgets[0]
        nested_widget = connection_widget.find_widget_for_tile(nested)
        tall_widget = connection_widget.find_widget_for_tile(tall_group)
        before_identity = id(nested_widget)
        before_heights = (nested_widget.height(), connection_widget.height())
        self.assertLess(nested_widget.height(), tall_widget.height())
        self.assertGreaterEqual(
            nested_widget.minimumHeight(),
            nested_widget._hdr.height() + nested_widget._inner.height(),
        )

        nested.tiles[0].enabled = False
        widget.refresh_tile_states()
        self.app.processEvents()

        self.assertEqual(id(connection_widget.find_widget_for_tile(nested)), before_identity)
        self.assertEqual((nested_widget.height(), connection_widget.height()), before_heights)
        widget.close()

    def test_nested_group_content_change_settles_above_minimum_height(self) -> None:
        nested = GroupTile(
            name="colors",
            tiles=[self._text("red"), self._text("blue")],
        )
        nested.ui_expanded = True
        nested.ui_width = 280
        connection = GroupTile(
            name="Shoes",
            group_type="connection",
            tiles=[nested, self._text("sneakers")],
        )
        connection.ui_expanded = True
        widget = BlockWidget(Block(tiles=[connection]))
        widget.resize(900, 520)
        widget.show()
        self.app.processEvents()

        connection_widget = widget._tile_widgets[0]
        nested_widget = connection_widget.find_widget_for_tile(nested)
        nested.tiles.extend(self._text(f"new-color-{i}") for i in range(5))
        nested_widget._refresh_sub_tiles()
        nested_widget._schedule_geometry_settle()
        for _ in range(5):
            self.app.processEvents()

        required = nested_widget._hdr.height() + nested_widget._inner.height()
        self.assertEqual(nested_widget.minimumHeight(), required)
        self.assertGreaterEqual(nested_widget.height(), required)
        self.assertGreaterEqual(connection_widget._chain_canvas.height(), nested_widget.height())
        widget.close()

    def test_extracting_nested_tile_does_not_rebuild_or_grow_other_connections(self) -> None:
        first_group = GroupTile(
            name="first",
            tiles=[self._text(f"first-{i}") for i in range(6)],
        )
        second_group = GroupTile(
            name="second",
            tiles=[self._text(f"second-{i}") for i in range(6)],
        )
        first_group.ui_expanded = second_group.ui_expanded = True
        first_connection = GroupTile(
            name="A", group_type="connection", tiles=[first_group, self._text("shoe")]
        )
        second_connection = GroupTile(
            name="B", group_type="connection", tiles=[second_group, self._text("wall")]
        )
        first_connection.ui_expanded = second_connection.ui_expanded = True
        widget = BlockWidget(Block(tiles=[first_connection, second_connection]))
        widget.resize(900, 700)
        widget.show()
        for _ in range(5):
            self.app.processEvents()

        first_widget, second_widget = widget._tile_widgets
        first_identity = id(first_widget)
        second_identity = id(second_widget)
        second_height = second_widget.height()
        dragged_tile = first_group.tiles[0]
        source_widget = first_widget.find_widget_for_tile(dragged_tile)

        import ui.tile_drag as tile_drag
        tile_drag.set_drag(source_widget)
        mime = QMimeData()
        mime.setData(TILE_MIME, b"1")
        event = QDropEvent(
            QPointF(widget.width() - 5, widget.height() - 5),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.dropEvent(event)
        tile_drag.clear_drag()
        for _ in range(5):
            self.app.processEvents()

        self.assertTrue(event.isAccepted())
        self.assertEqual(id(widget._tile_widgets[0]), first_identity)
        self.assertEqual(id(widget._tile_widgets[1]), second_identity)
        self.assertEqual(widget._tile_widgets[1].height(), second_height)
        self.assertIs(widget.block.tiles[-1], dragged_tile)
        remaining_group_widget = widget._tile_widgets[0].find_widget_for_tile(first_group)
        required_source_height = (
            remaining_group_widget._hdr.height() + remaining_group_widget._inner.height()
        )
        self.assertEqual(remaining_group_widget.minimumHeight(), required_source_height)
        self.assertGreaterEqual(remaining_group_widget.height(), required_source_height)
        self.assertGreater(remaining_group_widget._inner.height(), 36)
        self.assertEqual(
            widget._tiles_container.minimumHeight(),
            max(36, widget._flow.heightForWidth(widget._tiles_container.width())),
        )

        # 取り出したタイルを元の子グループへ戻しても、対象以外の接続グループは
        # 作り直されず高さも変わらない。
        extracted_widget = widget._tile_widgets[-1]
        target_group_widget = widget._tile_widgets[0].find_widget_for_tile(first_group)
        tile_drag.set_drag(extracted_widget)
        return_mime = QMimeData()
        return_mime.setData(TILE_MIME, b"1")
        return_event = QDropEvent(
            QPointF(target_group_widget.width() / 2, target_group_widget.height() / 2),
            Qt.DropAction.MoveAction,
            return_mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        target_group_widget.dropEvent(return_event)
        tile_drag.clear_drag()
        for _ in range(5):
            self.app.processEvents()

        self.assertTrue(return_event.isAccepted())
        self.assertEqual(len(widget._tile_widgets), 2)
        self.assertEqual(id(widget._tile_widgets[1]), second_identity)
        self.assertEqual(widget._tile_widgets[1].height(), second_height)
        widget.close()

    def test_extracting_tile_from_standalone_group_keeps_content_height(self) -> None:
        group = GroupTile(
            name="standalone",
            tiles=[self._text(f"long-standalone-item-{i}") for i in range(7)],
        )
        group.ui_expanded = True
        group.ui_width = 300
        widget = BlockWidget(Block(tiles=[group]))
        widget.resize(900, 620)
        widget.show()
        for _ in range(5):
            self.app.processEvents()

        group_widget = widget._tile_widgets[0]
        group_identity = id(group_widget)
        dragged_tile = group.tiles[0]
        source_widget = group_widget.find_widget_for_tile(dragged_tile)

        import ui.tile_drag as tile_drag
        tile_drag.set_drag(source_widget)
        mime = QMimeData()
        mime.setData(TILE_MIME, b"1")
        event = QDropEvent(
            QPointF(widget.width() - 5, widget.height() - 5),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.dropEvent(event)
        tile_drag.clear_drag()
        for _ in range(5):
            self.app.processEvents()

        group_widget = widget.find_widget_for_tile(group)
        required = group_widget._hdr.height() + group_widget._inner.height()
        self.assertTrue(event.isAccepted())
        self.assertEqual(id(group_widget), group_identity)
        self.assertEqual(group_widget.minimumHeight(), required)
        self.assertGreaterEqual(group_widget.height(), required)
        self.assertGreater(group_widget._inner.height(), 36)
        self.assertIs(widget.block.tiles[-1], dragged_tile)
        widget.close()

    def test_category_refresh_reaches_same_tag_in_other_connection_group(self) -> None:
        first_tag = self._tag("red")
        second_tag = self._tag("red")
        first_group = GroupTile(name="first", tiles=[first_tag, self._text("one")])
        second_group = GroupTile(name="second", tiles=[second_tag, self._text("two")])
        first_group.ui_expanded = second_group.ui_expanded = True
        first_connection = GroupTile(
            name="A", group_type="connection", tiles=[first_group, self._text("shoe")]
        )
        second_connection = GroupTile(
            name="B", group_type="connection", tiles=[second_group, self._text("wall")]
        )
        first_connection.ui_expanded = second_connection.ui_expanded = True
        widget = BlockWidget(Block(tiles=[first_connection, second_connection]))
        widget.resize(900, 700)
        widget.show()
        self.app.processEvents()

        registered = {
            "name_en": "red",
            "name_local": "赤",
            "category": "clothing_accessory",
        }
        with patch("ui.tile_widget._find_registered_tag", return_value=registered):
            widget.refresh_tile_styles()

        self.assertEqual(first_tag.category, "clothing_accessory")
        self.assertEqual(second_tag.category, "clothing_accessory")
        widget.close()

    def test_drag_overlay_and_vertical_connection_group_layout(self) -> None:
        a, b = self._text("A"), self._text("B")
        block = Block(tiles=[a, b])
        widget = BlockWidget(block)
        widget.resize(720, 520)
        widget.show()
        self.app.processEvents()

        source_widget, target_widget = widget._tile_widgets
        source_handle = source_widget._connection_handle
        target_handle = target_widget._connection_handle
        target_global = target_handle.mapToGlobal(
            target_handle.rect().center()
        )
        target_global.setX(target_handle.mapToGlobal(target_handle.rect().topLeft()).x() + 2)
        widget._start_connection_drag(source_widget, source_handle)
        widget._update_connection_drag(source_widget, source_handle, target_global)
        self.assertTrue(widget._connection_overlay.isVisible())
        self.assertNotEqual(target_handle.text(), "🔗")
        widget._finish_connection_drag(source_widget, source_handle, target_global)
        self.assertFalse(widget._connection_overlay.isVisible())
        self.assertEqual(block.tiles[0].tiles, [a, b])

        second = GroupTile(
            name="Second",
            group_type="connection",
            tiles=[self._text(f"long-item-{i}") for i in range(12)],
        )
        second.ui_expanded = True
        block.tiles.append(second)
        widget.reload()
        self.app.processEvents()
        first_widget, second_widget = widget._tile_widgets
        self.assertGreater(second_widget.y(), first_widget.y())
        self.assertEqual(second_widget.x(), first_widget.x())
        self.assertGreater(
            second_widget._chain_scroll.horizontalScrollBar().maximum(), 0
        )
        widget.close()


if __name__ == "__main__":
    unittest.main()
