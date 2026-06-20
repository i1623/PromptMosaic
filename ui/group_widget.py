"""
グループタイルウィジェット

GroupTile を視覚的に表示・編集するウィジェット。
・折りたたみ（▶）/ 展開（▼）でサブタイルの表示切替
・ダブルクリックでグループ編集ダイアログ（名前・モード・個数）
・ON/OFF・削除ボタン
・サブタイルはドラッグ&ドロップで追加/移動/取り出しが可能
・2階層まで入れ子対応（nesting_depth パラメータで制御）
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDialog, QComboBox, QLineEdit, QDialogButtonBox, QSizePolicy, QMessageBox,
)
from PySide6.QtCore import Signal, Qt, QPoint, QMimeData, QTimer
from PySide6.QtGui import QDrag, QFont

from core.prompt_builder import GroupTile, TagTile, NaturalTextTile
from core.i18n import tr
from core.text_sanitize import single_line_text
from ui.flow_layout import FlowLayout
from ui.tile_widget import TileWidget, TILE_MIME
from ui.styles import SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT, ACCENT, GREEN, RED, ui_font

_MAX_DEPTH = 3   # ネストの最大深度


class GroupWidget(QFrame):
    """
    1 GroupTile を表示・編集するウィジェット。

    Signals:
        delete_requested(GroupWidget): 削除ボタン
        tile_changed():                タイルデータが変更された
        move_requested(GroupWidget, delta): 並び順の上下移動
    """

    delete_requested = Signal(object)
    ungroup_requested = Signal(object)
    tile_changed     = Signal()
    move_requested   = Signal(object, int)
    geometry_changed = Signal()   # 展開/折りたたみで高さが変わったとき

    def __init__(self, group: GroupTile, parent=None, nesting_depth: int = 0, *, readonly: bool = False):
        super().__init__(parent)
        self.tile = group          # BlockWidget と共通の .tile 属性
        self._depth = nesting_depth
        self._readonly = bool(readonly)
        self._expanded = False
        self._drag_start: QPoint | None = None
        self._sub_widgets: list[QWidget] = []   # TileWidget | GroupWidget
        self._drop_index: int = -1
        self._build_ui()
        self._refresh_sub_tiles()
        # _refresh_tiles による再構築後も展開状態を復元
        if group.ui_expanded:
            self._expanded = True
            self._inner.setVisible(True)
            self._expand_btn.setText("▼")
            self._update_inner_height()
        self._update_stable_width()
        self._apply_edit_lock_state()
        self._apply_readonly_state()

    # ── UI構築 ──────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            f"GroupWidget {{ background: {SURFACE1}; border: 1px solid {ACCENT}; "
            f"border-radius: 4px; }}"
        )
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── ヘッダー行 ──────────────────────────────────
        hdr = QWidget()
        hdr.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        hdr.setStyleSheet(f"background: {SURFACE2}; border-radius: 4px 4px 0 0;")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(4, 2, 4, 2)
        hdr_lay.setSpacing(4)

        self._expand_btn = QPushButton("▶")
        self._expand_btn.setFixedSize(18, 18)
        self._expand_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ACCENT}; border: none; padding: 0; }}"
            f"QPushButton:hover {{ color: #cdd6f4; }}"
        )
        self._expand_btn.clicked.connect(self._toggle_expand)
        hdr_lay.addWidget(self._expand_btn)

        self._name_lbl = QLabel(self.tile.name)
        self._name_lbl.setFont(ui_font(bold=True))
        self._name_lbl.setStyleSheet(f"color: {ACCENT}; background: transparent; border: none;")
        hdr_lay.addWidget(self._name_lbl)

        # モードバッジ
        self._mode_lbl = QLabel(self._mode_badge())
        self._mode_lbl.setFont(ui_font(-2))
        self._mode_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent; border: none;")
        hdr_lay.addWidget(self._mode_lbl)

        hdr_lay.addStretch()

        # ON/OFF
        self._toggle_btn = QPushButton("ON")
        self._toggle_btn.setFixedSize(36, 17)
        self._toggle_btn.setFont(ui_font(-2, bold=True))
        self._toggle_btn.setToolTip(tr("group.toggle_tooltip"))
        self._toggle_btn.clicked.connect(self._toggle_enabled)
        hdr_lay.addWidget(self._toggle_btn)
        self._apply_toggle_style()

        # 削除
        self._delete_btn = QPushButton("✕")
        self._delete_btn.setFixedSize(16, 16)
        self._delete_btn.setFont(ui_font(-1, bold=True))
        self._delete_btn.setStyleSheet(
            "QPushButton { background: #3a2a2a; color: #f38ba8; "
            "border: 1px solid #8a4a4a; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #f38ba8; color: #1e1e2e; }"
            "QPushButton:disabled { background: #242432; color: #585b70; border-color: #313244; }"
        )
        self._delete_btn.clicked.connect(self._confirm_delete)
        hdr_lay.addWidget(self._delete_btn)

        self._plus_btn = self._make_strength_button("+", tr("tile.strength_plus_tooltip"))
        self._minus_btn = self._make_strength_button("−", tr("tile.strength_minus_tooltip"))
        self._plus_btn.clicked.connect(self._strength_plus)
        self._minus_btn.clicked.connect(self._strength_minus)
        hdr_lay.addWidget(self._plus_btn)
        hdr_lay.addWidget(self._minus_btn)

        # グループ編集ロック
        self._lock_btn = QPushButton("🔓")
        self._lock_btn.setFixedSize(20, 17)
        self._lock_btn.setFont(ui_font(-2, bold=True))
        self._lock_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {TEXT}; border-radius: 2px; padding: 0; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; border-color: {TEXT}; }}"
        )
        self._lock_btn.clicked.connect(self._toggle_edit_locked)
        hdr_lay.addWidget(self._lock_btn)

        # グループ化解除
        self._ungroup_btn = QPushButton("💥")
        self._ungroup_btn.setFixedSize(20, 17)
        self._ungroup_btn.setFont(ui_font(-2, bold=True))
        self._ungroup_btn.setToolTip(tr("group.ungroup_tooltip"))
        self._ungroup_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {TEXT}; border-radius: 2px; padding: 0; }}"
            f"QPushButton:hover {{ background: {SURFACE2}; border-color: {TEXT}; }}"
            f"QPushButton:disabled {{ background: #242432; color: #585b70; border-color: #313244; }}"
        )
        self._ungroup_btn.clicked.connect(self._confirm_ungroup)
        hdr_lay.addWidget(self._ungroup_btn)

        # タグブラウザに保存
        self._save_btn = QPushButton("💾")
        self._save_btn.setFixedSize(20, 17)
        self._save_btn.setFont(ui_font(-2))
        self._save_btn.setToolTip(tr("group.save_tooltip"))
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE1}; color: {ACCENT}; "
            f"border: 1px solid {ACCENT}; border-radius: 2px; padding: 0; }}"
            f"QPushButton:hover {{ background: {ACCENT}; color: {SURFACE0}; }}"
        )
        self._save_btn.clicked.connect(self._save_to_browser)
        hdr_lay.addWidget(self._save_btn)

        self._hdr = hdr
        hdr.setFixedHeight(hdr.sizeHint().height())
        root.addWidget(hdr)

        # ── サブタイルエリア（折りたたみ時は非表示）──────
        self._inner = QWidget()
        self._inner.setStyleSheet(
            f"background: {SURFACE0}; border-top: 1px solid {SURFACE2};"
        )
        self._inner.setVisible(False)
        inner_lay = QVBoxLayout(self._inner)
        inner_lay.setContentsMargins(4, 4, 4, 4)
        inner_lay.setSpacing(2)

        self._flow = FlowLayout(h_spacing=4, v_spacing=4)
        inner_lay.addLayout(self._flow)

        self._drop_indicator = QFrame(self._inner)
        self._drop_indicator.setFixedWidth(3)
        self._drop_indicator.setStyleSheet(
            f"background-color: {ACCENT}; border-radius: 1px; border: none;"
        )
        self._drop_indicator.hide()

        # ドロップゾーンラベル（空のとき）
        self._empty_hint = QLabel(tr("group.empty_hint"))
        self._empty_hint.setFont(ui_font(-2))
        self._empty_hint.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner_lay.addWidget(self._empty_hint)

        root.addWidget(self._inner)

    @staticmethod
    def _make_order_button(text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(16, 16)
        btn.setFont(ui_font(-3, bold=True))
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            "QPushButton { background: transparent; color: #cdd6f4; "
            "border: 1px solid #585b70; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #45475a; border-color: #89b4fa; }"
            "QPushButton:disabled { color: #45475a; border-color: #313244; }"
        )
        return btn

    @staticmethod
    def _make_strength_button(text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(14, 14)
        btn.setFont(ui_font(-3, bold=True))
        btn.setToolTip(tooltip)
        if text == "+":
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #a6e3a1;"
                " border: 1px solid #4a8a4a; border-radius: 2px; padding: 0; }"
                "QPushButton:hover { background: #2a5a2a; border-color: #a6e3a1; }"
                "QPushButton:disabled { color: #585b70; border-color: #313244; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #f38ba8;"
                " border: 1px solid #8a4a4a; border-radius: 2px; padding: 0; }"
                "QPushButton:hover { background: #5a2a2a; border-color: #f38ba8; }"
                "QPushButton:disabled { color: #585b70; border-color: #313244; }"
            )
        return btn

    def _confirm_delete(self) -> None:
        if self._is_edit_locked():
            return
        if not self.tile.tiles:
            self.delete_requested.emit(self)
            return
        ret = QMessageBox.question(
            self,
            tr("group.delete_confirm_title"),
            tr("group.delete_confirm_msg", name=self.tile.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(self)

    def _confirm_ungroup(self) -> None:
        if self._is_edit_locked():
            return
        ret = QMessageBox.question(
            self,
            tr("group.ungroup_confirm_title"),
            tr("group.ungroup_confirm_msg", name=self.tile.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self.ungroup_requested.emit(self)

    def _is_edit_locked(self) -> bool:
        return bool(getattr(self.tile, "edit_locked", False))

    def _toggle_edit_locked(self) -> None:
        self.tile.edit_locked = not self._is_edit_locked()
        self._apply_edit_lock_state()
        self.tile_changed.emit()

    def _apply_edit_lock_state(self) -> None:
        locked = self._is_edit_locked()
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._lock_btn.setToolTip(
            tr("group.edit_lock_locked_tooltip" if locked else "group.edit_lock_unlocked_tooltip")
        )
        for btn in (
            self._toggle_btn,
            self._delete_btn,
            self._plus_btn,
            self._minus_btn,
            self._ungroup_btn,
        ):
            btn.setEnabled(not locked)
        # readonly（履歴タイルクローン等）では _apply_readonly_state の
        # setAcceptDrops(False) を上書きしない（クローンへのドロップ禁止を維持）
        self.setAcceptDrops(not locked and not self._readonly)
        for widget in self._sub_widgets:
            widget.setEnabled(not locked)

    # ── リサイズ ─────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._expanded:
            self._update_inner_height()

    # ── 展開 / 折りたたみ ────────────────────────────────

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self.tile.ui_expanded = self._expanded   # GroupTile に保持して再構築後も復元
        self._inner.setVisible(self._expanded)
        self._expand_btn.setText("▼" if self._expanded else "▶")
        self._update_stable_width()
        if self._expanded:
            self._update_inner_height()
        else:
            self._inner.setMinimumHeight(0)
        self.updateGeometry()
        self.geometry_changed.emit()

    def ensure_expanded(self) -> bool:
        """折りたたまれていれば展開し、状態が変わったかを返す。"""
        if self._expanded:
            return False
        self._expanded = True
        self.tile.ui_expanded = True
        self._inner.setVisible(True)
        self._expand_btn.setText("▼")
        self._update_stable_width()
        self._update_inner_height()
        self.updateGeometry()
        self.geometry_changed.emit()
        return True

    # ── サブタイル管理 ────────────────────────────────────

    def _refresh_sub_tiles(self) -> None:
        # FlowLayout は removeWidget を持たないので takeAt で全削除
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item and item.widget():
                w = item.widget()
                w.hide()           # 即座に非表示にして新タイルへの重なりを防ぐ
                w.deleteLater()
        self._sub_widgets.clear()

        for tile in self.tile.tiles:
            w = self._make_sub_widget(tile)
            self._flow.addWidget(w)
            self._sub_widgets.append(w)

        self._empty_hint.setVisible(len(self.tile.tiles) == 0)
        self._flow.invalidate()
        self._update_stable_width()
        self._update_inner_height()
        self._apply_edit_lock_state()
        self.updateGeometry()

    def _update_stable_width(self) -> None:
        """展開・折りたたみで横幅が変わらないよう、子タイル基準の幅に固定する。"""
        header_w = self._hdr.sizeHint().width() if hasattr(self, "_hdr") else 0
        inner_m = self._inner.layout().contentsMargins()
        widest_child = 0
        for widget in self._sub_widgets:
            widest_child = max(
                widest_child,
                widget.sizeHint().width(),
                widget.minimumSizeHint().width(),
                widget.minimumWidth(),
            )
        empty_w = self._empty_hint.sizeHint().width() if not self.tile.tiles else 0
        content_w = max(widest_child, empty_w) + inner_m.left() + inner_m.right()
        self.setFixedWidth(max(header_w, content_w, 80))

    def _update_inner_height(self) -> None:
        """FlowLayout の必要高さに合わせて _inner の最低高さを設定する。"""
        cw = self._inner.width()
        if cw <= 0:
            cw = self.width()
        if cw <= 0:
            return
        m = self._inner.layout().contentsMargins()
        inner_w = max(1, cw - m.left() - m.right())
        if self.tile.tiles:
            h = max(28, self._flow.heightForWidth(inner_w))
        else:
            h = self._empty_hint.sizeHint().height()
        self._inner.setMinimumHeight(h + m.top() + m.bottom())

    def _make_sub_widget(self, tile) -> QWidget:
        if isinstance(tile, GroupTile) and self._depth < _MAX_DEPTH - 1:
            gw = GroupWidget(tile, self._inner, nesting_depth=self._depth + 1, readonly=self._readonly)
            gw.delete_requested.connect(self._on_sub_delete)
            gw.ungroup_requested.connect(self._on_sub_ungroup)
            gw.tile_changed.connect(self._on_sub_changed)
            gw.geometry_changed.connect(self._on_sub_geometry_changed)
            gw.move_requested.connect(self._on_sub_move_requested)
            return gw
        else:
            # parent=self._inner を指定することで、_inner が表示されているとき
            # 自動的に表示される（親なしだと show() されない）
            tw = TileWidget(tile, parent=self._inner, readonly=self._readonly)
            tw.delete_requested.connect(self._on_sub_delete)
            tw.tile_changed.connect(self._on_sub_changed)
            tw.tile_replaced.connect(self._on_sub_replaced)
            tw.move_requested.connect(self._on_sub_move_requested)
            return tw

    def _on_sub_replaced(self, w: QWidget, new_tile) -> None:
        if self._is_edit_locked():
            return
        try:
            index = self._sub_widgets.index(w)
        except ValueError:
            return
        if not (0 <= index < len(self.tile.tiles)):
            return
        self.tile.tiles[index] = new_tile
        self._replace_sub_widget(index, new_tile)
        self.tile_changed.emit()

    def _on_sub_delete(self, w: QWidget) -> None:
        if self._is_edit_locked():
            return
        idx = -1
        try:
            idx = self._sub_widgets.index(w)
            if 0 <= idx < len(self.tile.tiles):
                self.tile.tiles.pop(idx)
        except ValueError:
            tile = w.tile
            idx = self._index_by_identity(self.tile.tiles, tile)
            if idx >= 0:
                self.tile.tiles.pop(idx)

        # タイルが1個になったらグループ解消
        if len(self.tile.tiles) == 1:
            self.tile_changed.emit()   # 親に通知して解消させる
        elif len(self.tile.tiles) == 0:
            self.delete_requested.emit(self)
        else:
            if idx >= 0:
                self._remove_sub_widget(idx)
            else:
                self._refresh_sub_tiles()
            self.tile_changed.emit()

    def _on_sub_ungroup(self, w: QWidget) -> None:
        if self._is_edit_locked():
            return
        idx = self._index_by_identity(self._sub_widgets, w)
        if idx < 0:
            idx = self._index_by_identity(self.tile.tiles, w.tile)
        if idx < 0 or not isinstance(getattr(w, "tile", None), GroupTile):
            return
        children = list(w.tile.tiles)
        self.tile.tiles.pop(idx)
        for offset, child in enumerate(children):
            self.tile.tiles.insert(idx + offset, child)
        self._refresh_sub_tiles()
        self.tile_changed.emit()
        self.geometry_changed.emit()

    def _remove_sub_widget(self, index: int) -> None:
        if not (0 <= index < len(self._sub_widgets)):
            return
        item = self._flow.takeAt(index)
        w = self._sub_widgets.pop(index)
        if item and item.widget() and item.widget() is not w:
            item.widget().hide()
            item.widget().deleteLater()
        w.hide()
        w.deleteLater()
        self._empty_hint.setVisible(len(self.tile.tiles) == 0)
        self._flow.invalidate()
        self._update_stable_width()
        if self._expanded:
            self._update_inner_height()
        self.updateGeometry()
        self.geometry_changed.emit()

    def _on_sub_changed(self) -> None:
        if self._is_edit_locked():
            return
        sender = self.sender()
        if isinstance(sender, QWidget) and isinstance(getattr(sender, "tile", None), GroupTile):
            idx = self._index_by_identity(self._sub_widgets, sender)
            if idx < 0:
                idx = self._index_by_identity(self.tile.tiles, sender.tile)
            if idx >= 0 and len(sender.tile.tiles) <= 1:
                self._sync_sub_group_after_child_removed(idx, sender)
                self.tile_changed.emit()
                return
        self._update_stable_width()
        self._on_sub_geometry_changed()
        self.tile_changed.emit()

    def _sync_sub_group_after_child_removed(self, index: int, group_widget) -> None:
        remaining = len(group_widget.tile.tiles)
        if remaining >= 2:
            group_widget._refresh_sub_tiles()
            group_widget._update_inner_height()
            self._on_sub_geometry_changed()
            return
        if remaining == 1:
            survivor = group_widget.tile.tiles[0]
            self.tile.tiles[index] = survivor
            self._replace_sub_widget(index, survivor)
            return
        self.tile.tiles.pop(index)
        self._remove_sub_widget(index)

    def _replace_sub_widget(self, index: int, tile) -> None:
        self._remove_sub_widget(index)
        w = self._make_sub_widget(tile)
        self._flow.insertWidget(index, w)
        self._sub_widgets.insert(index, w)
        w.show()
        self._flow.invalidate()
        self._update_stable_width()
        if self._expanded:
            self._update_inner_height()
        self.updateGeometry()
        self.geometry_changed.emit()

    def _on_sub_geometry_changed(self) -> None:
        """子グループの展開/折りたたみに合わせて、このグループの高さを再計算する。"""
        self._flow.invalidate()
        self._update_stable_width()
        if self._expanded:
            self._update_inner_height()
        self.updateGeometry()
        self.geometry_changed.emit()

    def _on_sub_move_requested(self, w: QWidget, delta: int) -> None:
        if self._is_edit_locked():
            return
        idx = self._index_by_identity(self._sub_widgets, w)
        if idx < 0:
            idx = self._index_by_identity(self.tile.tiles, w.tile)
        new_idx = idx + delta
        if idx < 0 or not (0 <= new_idx < len(self.tile.tiles)):
            return
        self.tile.tiles.insert(new_idx, self.tile.tiles.pop(idx))
        self._sub_widgets.insert(new_idx, self._sub_widgets.pop(idx))
        item = self._flow.takeAt(idx)
        if item is None:
            self._refresh_sub_tiles()
        else:
            self._flow.insertItem(new_idx, item)
            self._flow.invalidate()
            self._update_stable_width()
            self._update_inner_height()
            self.updateGeometry()
        self.geometry_changed.emit()
        self.tile_changed.emit()

    def _strength_plus(self) -> None:
        if self._readonly:
            return
        if self._is_edit_locked():
            return
        self.tile.strength_level += 1
        self.refresh()
        self.tile_changed.emit()

    def _strength_minus(self) -> None:
        if self._readonly:
            return
        if self._is_edit_locked():
            return
        self.tile.strength_level -= 1
        self.refresh()
        self.tile_changed.emit()

    # ── ON/OFF ───────────────────────────────────────────

    def _toggle_enabled(self) -> None:
        if self._readonly:
            return
        if self._is_edit_locked():
            return
        self.tile.enabled = not self.tile.enabled
        self._apply_toggle_style()
        self.tile_changed.emit()

    def _apply_toggle_style(self) -> None:
        if self.tile.enabled:
            self._toggle_btn.setText("ON")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: #1a3a1a; color: #a6e3a1; "
                "border: 1px solid #4a8a4a; border-radius: 2px; padding: 0 2px; }"
                "QPushButton:hover { background: #2a5a2a; }"
            )
        else:
            self._toggle_btn.setText("OFF")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: #2a2a3a; color: #a0a0c0; "
                "border: 1px solid #6060a0; border-radius: 2px; padding: 0 2px; }"
                "QPushButton:hover { color: #cdd6f4; border-color: #a6adc8; }"
            )
            self.setStyleSheet(
                f"GroupWidget {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; "
                f"border-radius: 4px; opacity: 0.6; }}"
            )
            return
        self.setStyleSheet(
            f"GroupWidget {{ background: {SURFACE1}; border: 1px solid {ACCENT}; "
            f"border-radius: 4px; }}"
        )

    # ── タグブラウザへの保存 ──────────────────────────────

    def _save_to_browser(self) -> None:
        """グループをタグブラウザの保存グループに登録する"""
        from PySide6.QtWidgets import QInputDialog
        import json
        import db.library_db as database
        from db.group_preset_db import unique_group_name

        name, ok = QInputDialog.getText(
            self, tr("group.save_dialog_title"), tr("group.save_dialog_label"),
            text=self.tile.name,
        )
        if not ok or not name.strip():
            return
        name = unique_group_name(name)
        group_data = self.tile.to_dict(include_ui_state=False)
        if isinstance(group_data, dict):
            group_data["name"] = name
        group_json = json.dumps(group_data, ensure_ascii=False)
        row = database.fetchone(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM group_presets"
        )
        sort_order = row["n"] if row else 10
        database.execute(
            "INSERT INTO group_presets (name, group_json, sort_order) VALUES (?, ?, ?)",
            (name, group_json, sort_order),
        )
        # TagBrowser に通知して保存グループリストを更新
        try:
            from ui.tag_browser import TagBrowser
            TagBrowser.notify_presets_changed()
            from ui.group_preset_browser import GroupPresetBrowser
            GroupPresetBrowser.notify_presets_changed()
        except Exception:
            pass
        # 保存ボタンに一時的フィードバック
        self._save_btn.setToolTip(tr("group.saved_feedback"))
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self._save_btn.setToolTip(tr("group.save_tooltip")))

    # ── ヘルパー ──────────────────────────────────────────

    def _mode_badge(self) -> str:
        strength = ""
        if getattr(self.tile, "strength_level", 0) > 0:
            strength = "+" * self.tile.strength_level
        elif getattr(self.tile, "strength_level", 0) < 0:
            strength = "-" * abs(self.tile.strength_level)
        if self.tile.mode == "random":
            return f"{strength} 🎲×{self.tile.count}".strip()
        elif self.tile.mode == "sequential":
            return f"{strength} 🔢×{self.tile.count}".strip()
        return strength

    def refresh(self) -> None:
        self._name_lbl.setText(self.tile.name)
        self._mode_lbl.setText(self._mode_badge())
        self._apply_toggle_style()
        self._refresh_sub_tiles()
        self._apply_edit_lock_state()

    def refresh_tile_styles(self) -> None:
        """タグカテゴリ変更後、子タイルの色だけを現在DBに合わせて更新する。"""
        for widget in self._sub_widgets:
            if isinstance(widget, GroupWidget):
                widget.refresh_tile_styles()
            elif hasattr(widget, "_apply_style"):
                widget._apply_style()
        self._update_stable_width()
        if self._expanded:
            self._update_inner_height()
        self.updateGeometry()

    def refresh_tile_display(self) -> None:
        """子タイルの1段/2段表示切替を即時反映する。"""
        for widget in self._sub_widgets:
            if isinstance(widget, GroupWidget):
                widget.refresh_tile_display()
            elif hasattr(widget, "refresh"):
                widget.refresh()
        self._update_stable_width()
        if self._expanded:
            self._update_inner_height()
        self.updateGeometry()

    def collapse_all_groups(self) -> bool:
        """このグループと配下の子グループをすべて畳む。"""
        changed = False
        for tile in self.tile.tiles:
            if isinstance(tile, GroupTile):
                if tile.ui_expanded:
                    changed = True
                tile.ui_expanded = False
        for widget in self._sub_widgets:
            if isinstance(widget, GroupWidget):
                changed = widget.collapse_all_groups() or changed
        if self.tile.ui_expanded or self._expanded:
            changed = True
        self.tile.ui_expanded = False
        self._expanded = False
        self._inner.setVisible(False)
        self._expand_btn.setText("▶")
        self._inner.setMinimumHeight(0)
        self._update_stable_width()
        self.updateGeometry()
        self.geometry_changed.emit()
        return changed

    def expand_all_groups(self) -> bool:
        """このグループと配下の子グループをすべて展開する。"""
        changed = False
        for tile in self.tile.tiles:
            if isinstance(tile, GroupTile):
                if not tile.ui_expanded:
                    changed = True
                tile.ui_expanded = True
        for widget in self._sub_widgets:
            if isinstance(widget, GroupWidget):
                changed = widget.expand_all_groups() or changed
        if not self.tile.ui_expanded or not self._expanded:
            changed = True
        self.tile.ui_expanded = True
        self._expanded = True
        self._inner.setVisible(True)
        self._expand_btn.setText("▼")
        self._update_stable_width()
        self._update_inner_height()
        self.updateGeometry()
        self.geometry_changed.emit()
        return changed

    def find_tag_matches(self, tag_name: str, *, exclude_tile=None) -> list[tuple[QWidget, list["GroupWidget"]]]:
        """配下から tag_name に一致する TagTile のウィジェットを探す。"""
        target = (tag_name or "").strip().lower()
        if not target:
            return []

        matches: list[tuple[QWidget, list["GroupWidget"]]] = []
        for tile, widget in zip(self.tile.tiles, self._sub_widgets):
            if isinstance(tile, TagTile):
                if tile is exclude_tile:
                    continue
                if (tile.tag_name or "").strip().lower() == target:
                    matches.append((widget, [self]))
                continue

            if isinstance(tile, GroupTile) and isinstance(widget, GroupWidget):
                for sub_widget, chain in widget.find_tag_matches(tag_name, exclude_tile=exclude_tile):
                    matches.append((sub_widget, [self, *chain]))
        return matches

    def find_widget_for_tile(self, target_tile) -> QWidget | None:
        """配下から target_tile に対応するウィジェットを返す。"""
        for tile, widget in zip(self.tile.tiles, self._sub_widgets):
            if tile is target_tile:
                return widget
            if isinstance(tile, GroupTile) and isinstance(widget, GroupWidget):
                found = widget.find_widget_for_tile(target_tile)
                if found is not None:
                    return found
        return None

    # ── ダブルクリック → 編集ダイアログ ─────────────────

    def mouseDoubleClickEvent(self, event) -> None:
        if self._readonly:
            event.ignore()
            return
        if self._is_edit_locked():
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            dlg = _GroupEditDialog(self.tile, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._name_lbl.setText(self.tile.name)
                self._mode_lbl.setText(self._mode_badge())
                self._update_stable_width()
                self.tile_changed.emit()

    # ── ドラッグ（グループ全体を移動）────────────────────

    def mousePressEvent(self, event) -> None:
        if self._is_edit_locked():
            self._drag_start = None
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._is_edit_locked():
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return
        from PySide6.QtWidgets import QApplication
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        self._begin_drag()

    def _begin_drag(self) -> None:
        if self._is_edit_locked():
            return
        import ui.tile_drag as tile_drag
        tile_drag.set_drag(self)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TILE_MIME, b"1")
        drag.setMimeData(mime)
        from ui.drag_pixmap import translucent_drag_pixmap
        drag.setPixmap(translucent_drag_pixmap(self.grab()))
        if self._drag_start:
            drag.setHotSpot(self._drag_start)
        # readonly（履歴タイルクローン等）からのドラッグはコピー（カーソル表示も合わせる）
        drag.exec(
            Qt.DropAction.CopyAction if self._readonly else Qt.DropAction.MoveAction
        )
        tile_drag.clear_drag()
        self._drag_start = None

    def _apply_readonly_state(self) -> None:
        if not self._readonly:
            return
        for btn_name in (
            "_toggle_btn", "_delete_btn", "_plus_btn", "_minus_btn",
            "_lock_btn", "_ungroup_btn", "_save_btn",
        ):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                btn.hide()
        self.setAcceptDrops(False)

    # ── ドロップ（サブタイル追加）────────────────────────

    def dragEnterEvent(self, event) -> None:
        if self._is_edit_locked():
            event.ignore()
            return
        if event.mimeData().hasFormat(TILE_MIME):
            import ui.tile_drag as tile_drag
            src = tile_drag.get_drag()
            if src is not None and src is not self and self._can_accept_drop_tile(src.tile):
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._is_edit_locked():
            self._hide_drop_indicator()
            event.ignore()
            return
        if event.mimeData().hasFormat(TILE_MIME):
            import ui.tile_drag as tile_drag
            src = tile_drag.get_drag()
            if src is None or src is self or not self._can_accept_drop_tile(src.tile):
                self._hide_drop_indicator()
                event.ignore()
                return
            shift_down = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self._expanded:
                pos_in_inner = self._inner.mapFrom(self, event.position().toPoint())
                target = self._find_sub_widget_at(pos_in_inner)
                group_to_group = (
                    target is not None
                    and isinstance(getattr(src, "tile", None), GroupTile)
                    and isinstance(getattr(target, "tile", None), GroupTile)
                )
                if target is not None and target is not src and shift_down:
                    pos_in_target = target.mapFrom(self._inner, pos_in_inner)
                    if group_to_group or self._is_drop_on_label(target, pos_in_target):
                        self._hide_drop_indicator()
                        event.acceptProposedAction()
                        return
                idx = self._find_drop_index(pos_in_inner)
                self._drop_index = idx
                self._show_drop_indicator(idx)
            else:
                self._hide_drop_indicator()
            event.acceptProposedAction()
        else:
            self._hide_drop_indicator()
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._hide_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._hide_drop_indicator()
        if self._is_edit_locked():
            event.ignore()
            return
        if not event.mimeData().hasFormat(TILE_MIME):
            event.ignore()
            return
        import ui.tile_drag as tile_drag
        src = tile_drag.get_drag()
        if src is None or src is self:
            event.ignore()
            return

        tile = src.tile
        if not self._can_accept_drop_tile(tile):
            event.ignore()
            return

        # readonly エディタ（履歴タイルクローン等）からのドラッグはコピー扱い:
        # 複製を取り込み、ソースからは削除しない（BlockWidget.dropEvent と同じ扱い）
        src_readonly = bool(getattr(src, "_readonly", False))
        if src_readonly:
            from ui.block_widget import BlockWidget
            tile = BlockWidget._clone_tile_for_drop(tile)

        shift_down = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if isinstance(tile, GroupTile) and not self._expanded:
            if not shift_down:
                event.ignore()
                return
            if self._group_closed_self_with_source(src):
                tile_drag.clear_drag()
                event.acceptProposedAction()
            else:
                event.ignore()
            return

        source_owner = self._find_source_owner(src)

        pos_in_inner = self._inner.mapFrom(self, event.position().toPoint())

        if self._expanded and shift_down:
            target = self._find_sub_widget_at(pos_in_inner)
            if target is not None and target is not src:
                pos_in_target = target.mapFrom(self._inner, pos_in_inner)
                if self._is_drop_on_label(target, pos_in_target):
                    if self._drop_into_or_group_with_target(src, target):
                        tile_drag.clear_drag()
                        event.acceptProposedAction()
                    else:
                        event.ignore()
                    return

        insert_idx = self._find_drop_index(pos_in_inner) if self._expanded else len(self.tile.tiles)

        if source_owner is self:
            src_idx = self._index_by_identity(self.tile.tiles, tile)
            if src_idx < 0:
                event.ignore()
                return
            if src_idx == insert_idx or src_idx + 1 == insert_idx:
                tile_drag.clear_drag()
                event.acceptProposedAction()
                return
            self.tile.tiles.pop(src_idx)
            adjusted = insert_idx if insert_idx <= src_idx else insert_idx - 1
            self.tile.tiles.insert(adjusted, tile)
            widget_idx = self._index_by_identity(self._sub_widgets, src)
            if widget_idx >= 0:
                self._sub_widgets.pop(widget_idx)
                self._sub_widgets.insert(adjusted, src)
                item = self._flow.takeAt(widget_idx)
                if item is not None:
                    self._flow.insertItem(adjusted, item)
                else:
                    self._refresh_sub_tiles()
            else:
                self._refresh_sub_tiles()
            self._flow.invalidate()
            self._update_stable_width()
            if self._expanded:
                self._update_inner_height()
            self.updateGeometry()
            self.geometry_changed.emit()
            self.tile_changed.emit()
            tile_drag.clear_drag()
            event.acceptProposedAction()
            return

        # 先にグループに追加してからソース削除する
        # （_remove_tile_from_source が _refresh_tiles を呼ぶ場合、
        #   新しい GroupWidget が正しいタイルリストで構築されるようにするため）
        if isinstance(tile, GroupTile):
            if not self._can_add_group_child(self._depth, tile):
                self._show_max_depth_warning()
                event.ignore()
                return
            tile.ui_expanded = False
        self.tile.tiles.insert(insert_idx, tile)

        block_source_index = -1
        try:
            from ui.block_widget import BlockWidget
            p = src.parent()
            while p is not None and not isinstance(p, (BlockWidget, GroupWidget)):
                p = p.parent()
            if isinstance(p, BlockWidget):
                block_source_index = p._tile_widgets.index(src)
        except Exception:
            block_source_index = -1

        # ソースから削除（readonly ソースはコピーなので削除しない）
        source_owner = None if src_readonly else self._remove_tile_from_source(src, refresh=False)

        self._refresh_sub_tiles()

        # 展開していなければ展開（_toggle_expand は使わず直接設定して収納を防ぐ）
        if not self._expanded:
            self._expanded = True
            self.tile.ui_expanded = True
            self._inner.setVisible(True)
            self._expand_btn.setText("▼")
            # setVisible(True) 後に改めて高さを計算（非表示中は幅が 0 のため）
            self._update_inner_height()

        # ドロップ後に親レイアウトの幅が確定してから、もう一度高さを取り直す。
        QTimer.singleShot(0, self._update_inner_height)

        if source_owner is not None and source_owner is not self:
            if isinstance(source_owner, GroupWidget):
                if len(source_owner.tile.tiles) >= 2:
                    source_owner._refresh_sub_tiles()
                    source_owner.geometry_changed.emit()
                else:
                    source_owner.tile_changed.emit()
            else:
                # BlockWidget はここで import して循環 import を避ける
                from ui.block_widget import BlockWidget
                if isinstance(source_owner, BlockWidget):
                    if block_source_index >= 0:
                        source_owner._remove_tile_widget(block_source_index)
                    else:
                        source_owner._refresh_tiles()
                    source_owner._update_tile_container_height()
                    source_owner.block_changed.emit()

        self.updateGeometry()
        self.geometry_changed.emit()   # 親BlockWidgetの高さ再計算を促す
        self.tile_changed.emit()

        tile_drag.clear_drag()
        event.acceptProposedAction()

    def _drop_into_or_group_with_target(self, src: QWidget, target: QWidget) -> bool:
        """Shift+D&D で、グループ内でもタイル同士を新しい子グループへまとめる。"""
        tile = getattr(src, "tile", None)
        target_tile = getattr(target, "tile", None)
        if tile is None or target_tile is None or tile is target_tile:
            return False

        # readonly ソースからはコピー扱い（複製を取り込み、ソースは削除しない）
        src_readonly = bool(getattr(src, "_readonly", False))
        if src_readonly:
            from ui.block_widget import BlockWidget
            tile = BlockWidget._clone_tile_for_drop(tile)

        if isinstance(target_tile, GroupTile):
            if not target._can_accept_drop_tile(tile):
                self._show_max_depth_warning()
                return False
            if self._find_source_owner(src) is target:
                return True
            target_has_group = self._contains_group(target_tile)
            if getattr(target, "_expanded", False) or target_has_group:
                if isinstance(tile, GroupTile) and not self._can_add_group_child(target._depth, tile):
                    self._show_max_depth_warning()
                    return False
                if isinstance(tile, GroupTile):
                    tile.ui_expanded = False
                target_tile.tiles.append(tile)
                target.ensure_expanded()
            else:
                target_idx = self._index_by_identity(self.tile.tiles, target_tile)
                if target_idx < 0:
                    return False
                if isinstance(tile, GroupTile) and not self._can_wrap_groups(self._depth + 1, target_tile, tile):
                    self._show_max_depth_warning()
                    return False
                target_tile.ui_expanded = False
                if isinstance(tile, GroupTile):
                    tile.ui_expanded = False
                group = GroupTile(name=self._short_tile_name(target_tile) or self._short_tile_name(tile))
                group.tiles = [target_tile, tile]
                group.ui_expanded = True
                self.tile.tiles[target_idx] = group
        else:
            if self._depth >= _MAX_DEPTH - 1:
                self._show_max_depth_warning()
                return False
            if isinstance(tile, GroupTile) and not self._can_wrap_groups(self._depth + 1, target_tile, tile):
                self._show_max_depth_warning()
                return False
            target_idx = self._index_by_identity(self.tile.tiles, target_tile)
            if target_idx < 0:
                return False
            name = self._short_tile_name(target_tile) or self._short_tile_name(tile)
            group = GroupTile(name=name)
            group.tiles = [target_tile, tile]
            group.ui_expanded = True
            self.tile.tiles[target_idx] = group

        source_owner = None if src_readonly else self._remove_tile_from_source(src, refresh=False)
        self._refresh_sub_tiles()
        self._expand_after_drop()
        self._notify_source_after_removal(source_owner)
        self.updateGeometry()
        self.geometry_changed.emit()
        self.tile_changed.emit()
        return True

    def _group_closed_self_with_source(self, src: QWidget) -> bool:
        """閉じているグループへグループを落とした時は、両者を新しい親グループにする。"""
        tile = getattr(src, "tile", None)
        if not isinstance(tile, GroupTile):
            return False
        # readonly ソースからはコピー扱い（複製を取り込み、ソースは削除しない）
        src_readonly = bool(getattr(src, "_readonly", False))
        if src_readonly:
            from ui.block_widget import BlockWidget
            tile = BlockWidget._clone_tile_for_drop(tile)
        if self._contains_group(self.tile):
            if not self._can_add_group_child(self._depth, tile):
                self._show_max_depth_warning()
                return False
            source_owner = self._find_source_owner(src)
            if source_owner is self:
                return False
            if src_readonly:
                source_owner = None
            else:
                source_owner = self._remove_tile_from_source(src, refresh=False)
                if source_owner is None:
                    return False
            tile.ui_expanded = False
            self.tile.tiles.append(tile)
            self.ensure_expanded()
            self._refresh_sub_tiles()
            self.geometry_changed.emit()
            self.tile_changed.emit()
            self._notify_source_after_removal(source_owner)
            return True

        source_owner = self._find_source_owner(src)
        if source_owner is self:
            return False
        target_owner = self._find_source_owner(self)
        if target_owner is None:
            return False
        from ui.block_widget import BlockWidget
        new_group_depth = target_owner._depth + 1 if isinstance(target_owner, GroupWidget) else 0
        if not self._can_wrap_groups(new_group_depth, self.tile, tile):
            self._show_max_depth_warning()
            return False

        if src_readonly:
            source_owner = None
        else:
            source_owner = self._remove_tile_from_source(src, refresh=False)
            if source_owner is None:
                return False

        self.tile.ui_expanded = False
        tile.ui_expanded = False
        group = GroupTile(name=self._short_tile_name(self.tile) or self._short_tile_name(tile))
        group.tiles = [self.tile, tile]
        group.ui_expanded = True

        if isinstance(target_owner, GroupWidget):
            idx = self._index_by_identity(target_owner.tile.tiles, self.tile)
            if idx < 0:
                return False
            target_owner.tile.tiles[idx] = group
            target_owner._refresh_sub_tiles()
            target_owner.geometry_changed.emit()
            target_owner.tile_changed.emit()
        elif isinstance(target_owner, BlockWidget):
            idx = self._index_by_identity(target_owner.block.tiles, self.tile)
            if idx < 0:
                return False
            target_owner.block.tiles[idx] = group
            target_owner._refresh_tiles()
            target_owner.block_changed.emit()
        else:
            return False

        if source_owner is not target_owner:
            self._notify_source_after_removal(source_owner)
        return True

    @staticmethod
    def _group_depth(tile: GroupTile) -> int:
        child_depths = [
            GroupWidget._group_depth(child)
            for child in tile.tiles
            if isinstance(child, GroupTile)
        ]
        return 1 + (max(child_depths) if child_depths else 0)

    @staticmethod
    def _contains_group(tile: GroupTile) -> bool:
        return any(isinstance(child, GroupTile) for child in tile.tiles)

    @staticmethod
    def _can_add_group_child(parent_depth: int, tile: GroupTile) -> bool:
        return parent_depth + GroupWidget._group_depth(tile) <= _MAX_DEPTH - 1

    @staticmethod
    def _can_wrap_groups(new_group_depth: int, *tiles) -> bool:
        child_depths = [
            GroupWidget._group_depth(tile)
            for tile in tiles
            if isinstance(tile, GroupTile)
        ]
        new_group_levels = 1 + (max(child_depths) if child_depths else 0)
        return new_group_depth + new_group_levels <= _MAX_DEPTH

    def _show_max_depth_warning(self) -> None:
        QMessageBox.warning(self, tr("group.max_depth_title"), tr("group.max_depth_msg"))

    def _expand_after_drop(self) -> None:
        if not self._expanded:
            self._expanded = True
            self.tile.ui_expanded = True
            self._inner.setVisible(True)
            self._expand_btn.setText("▼")
        self._update_inner_height()
        QTimer.singleShot(0, self._update_inner_height)

    def _notify_source_after_removal(self, source_owner: QWidget | None) -> None:
        if source_owner is None or source_owner is self:
            return
        if isinstance(source_owner, GroupWidget):
            if len(source_owner.tile.tiles) >= 2:
                source_owner._refresh_sub_tiles()
                source_owner.geometry_changed.emit()
            else:
                source_owner.tile_changed.emit()
            return
        from ui.block_widget import BlockWidget
        if isinstance(source_owner, BlockWidget):
            source_owner._normalize_groups()
            source_owner._refresh_tiles()
            source_owner._update_tile_container_height()
            source_owner.block_changed.emit()

    def _can_accept_drop_tile(self, tile) -> bool:
        """このグループへ tile を入れてよいかを判定する。循環ネストを防ぐ。"""
        if self._is_edit_locked():
            return False
        if isinstance(tile, GroupTile):
            # 親グループを自分の子孫グループへ入れると循環して表示ツリーが壊れる。
            if self._group_contains(tile, self.tile):
                return False
        return True

    def _find_sub_widget_at(self, pos: QPoint) -> QWidget | None:
        """_inner 座標上のマウス位置にある子タイル/子グループを返す。"""
        for widget in self._sub_widgets:
            if (widget.x() <= pos.x() < widget.x() + widget.width() and
                    widget.y() <= pos.y() < widget.y() + widget.height()):
                return widget
        return None

    @staticmethod
    def _is_drop_on_label(widget: QWidget, pos_in_widget: QPoint) -> bool:
        if isinstance(widget, TileWidget):
            return widget.is_over_label(pos_in_widget)
        return True

    @staticmethod
    def _short_tile_name(tile) -> str:
        if isinstance(tile, TagTile):
            text = tile.tag_local or tile.tag_name
        elif isinstance(tile, NaturalTextTile):
            text = tile.source_text or tile.translated_text or tile.text
        elif isinstance(tile, GroupTile):
            text = tile.name
        else:
            text = ""
        return single_line_text(text).strip()[:24] or "Grp"

    def _find_drop_index(self, pos: QPoint) -> int:
        """_inner 座標上のマウス位置から、子タイルの挿入インデックスを返す。"""
        px, py = pos.x(), pos.y()
        n = len(self._sub_widgets)
        if n == 0:
            return 0

        for i, widget in enumerate(self._sub_widgets):
            ty = widget.y()
            th = widget.height()
            tx = widget.x()
            tw = widget.width()

            if py < ty:
                return i
            if py < ty + th:
                if px < tx + tw // 2:
                    return i
                next_same_row = (
                    i + 1 < n and
                    self._sub_widgets[i + 1].y() == ty
                )
                if not next_same_row:
                    return i + 1

        return n

    def _show_drop_indicator(self, idx: int) -> None:
        """グループ内の挿入位置を示す縦線インジケータを表示する。"""
        n = len(self._sub_widgets)
        if n == 0 or not self._expanded:
            self._hide_drop_indicator()
            return
        if idx < n:
            ref = self._sub_widgets[idx]
            x = max(0, ref.x() - 3)
            y = ref.y()
            h = ref.height()
        else:
            ref = self._sub_widgets[-1]
            x = ref.x() + ref.width() + 1
            y = ref.y()
            h = ref.height()
        self._drop_indicator.setGeometry(x, y, 3, h)
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _hide_drop_indicator(self) -> None:
        self._drop_index = -1
        if hasattr(self, "_drop_indicator"):
            self._drop_indicator.hide()

    @staticmethod
    def _find_source_owner(src_widget: QWidget) -> QWidget | None:
        """ドラッグ元タイル/グループを直接保持している BlockWidget または GroupWidget を探す。"""
        tile = getattr(src_widget, "tile", None)
        p = src_widget.parent()
        while p is not None:
            if isinstance(p, GroupWidget) and GroupWidget._index_by_identity(p.tile.tiles, tile) >= 0:
                return p
            from ui.block_widget import BlockWidget
            if isinstance(p, BlockWidget) and GroupWidget._index_by_identity(p.block.tiles, tile) >= 0:
                return p
            p = p.parent()
        return None

    @staticmethod
    def _group_contains(root: GroupTile, target: GroupTile) -> bool:
        if root is target:
            return True
        for child in root.tiles:
            if isinstance(child, GroupTile) and GroupWidget._group_contains(child, target):
                return True
        return False

    @staticmethod
    def _remove_tile_from_source(src_widget: QWidget, refresh: bool = True) -> QWidget | None:
        """ドラッグ元からタイルを削除する"""
        tile = src_widget.tile
        p = src_widget.parent()
        while p is not None:
            # GroupWidget の内側にある場合
            if isinstance(p, GroupWidget):
                if p._is_edit_locked():
                    return None
                idx = GroupWidget._index_by_identity(p.tile.tiles, tile)
                if idx >= 0:
                    p.tile.tiles.pop(idx)
                    if refresh:
                        p._refresh_sub_tiles()
                        p.tile_changed.emit()
                    return p
            # BlockWidget の tiles に直接ある場合
            from ui.block_widget import BlockWidget
            if isinstance(p, BlockWidget):
                idx = GroupWidget._index_by_identity(p.block.tiles, tile)
                if idx >= 0:
                    p.block.tiles.pop(idx)
                    if refresh:
                        p._refresh_tiles()
                        p.block_changed.emit()
                    return p
            p = p.parent()
        return None

    @staticmethod
    def _index_by_identity(items: list, target) -> int:
        for i, item in enumerate(items):
            if item is target:
                return i
        return -1


# ── グループ編集ダイアログ ────────────────────────────────────────────────────

class _GroupEditDialog(QDialog):
    def __init__(self, group: GroupTile, parent=None):
        super().__init__(parent)
        self._group = group
        self.setWindowTitle(tr("group.edit_title"))
        self.setFixedWidth(340)
        self.setStyleSheet(f"QDialog {{ background: {SURFACE0}; color: {TEXT}; }}")
        self._build()

    @staticmethod
    def _section(label_text: str) -> tuple:
        """ラベル付き枠セクションを返す (frame, inner_layout)"""
        from ui.styles import SURFACE1, SURFACE2, TEXT
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; "
            f"border-radius: 4px; }}"
        )
        vlay = QVBoxLayout(frame)
        vlay.setContentsMargins(8, 6, 8, 6)
        vlay.setSpacing(4)
        lbl = QLabel(label_text)
        lbl.setFont(ui_font(-1))
        lbl.setStyleSheet(f"color: {TEXT}; border: none; background: transparent;")
        vlay.addWidget(lbl)
        return frame, vlay

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.setContentsMargins(10, 10, 10, 10)

        # ── グループ名 ────────────────────────────────────
        frame, sec = self._section(tr("group.name_label"))
        self._name_edit = QLineEdit(self._group.name)
        sec.addWidget(self._name_edit)
        lay.addWidget(frame)

        # ── 選択モード ────────────────────────────────────
        frame, sec = self._section(tr("group.mode_label"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem(tr("group.mode_none"), "none")
        self._mode_combo.addItem(tr("group.mode_random"), "random")
        self._mode_combo.addItem(tr("group.mode_sequential"), "sequential")
        idx = self._mode_combo.findData(self._group.mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        sec.addWidget(self._mode_combo)
        lay.addWidget(frame)

        # ── 選択個数 ──────────────────────────────────────
        frame, sec = self._section(tr("group.count_label"))
        count_row = QHBoxLayout()
        count_row.setSpacing(4)
        self._count_edit = QLineEdit(str(self._group.count))
        self._count_edit.setFixedWidth(52)
        self._count_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._count_edit.setPlaceholderText("1")
        count_row.addWidget(self._count_edit)

        _ss = (
            "QPushButton { background: transparent; color: #cdd6f4; "
            "border: 1px solid #45475a; border-radius: 3px; padding: 0 4px; }"
            "QPushButton:hover { background: #45475a; }"
        )
        btn_up = QPushButton("▲")
        btn_up.setFixedSize(28, 26)
        btn_up.setFont(ui_font(0))
        btn_up.setStyleSheet(_ss)
        def _count_up():
            try:
                v = int(self._count_edit.text() or "1")
            except ValueError:
                v = 1
            self._count_edit.setText(str(min(99, v + 1)))
        btn_up.clicked.connect(_count_up)
        btn_dn = QPushButton("▼")
        btn_dn.setFixedSize(28, 26)
        btn_dn.setFont(ui_font(0))
        btn_dn.setStyleSheet(_ss)
        def _count_dn():
            try:
                v = int(self._count_edit.text() or "1")
            except ValueError:
                v = 1
            self._count_edit.setText(str(max(1, v - 1)))
        btn_dn.clicked.connect(_count_dn)
        count_row.addWidget(btn_up)
        count_row.addWidget(btn_dn)
        count_row.addStretch()
        sec.addLayout(count_row)
        self._count_edit.setEnabled(self._group.mode != "none")
        lay.addWidget(frame)

        # ── シーケンスリセット ────────────────────────────
        reset_btn = QPushButton(tr("group.sequence_reset"))
        reset_btn.setFont(ui_font(-1))
        reset_btn.clicked.connect(lambda: self._group.reset_seq())
        lay.addWidget(reset_btn)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_mode_changed(self) -> None:
        self._count_edit.setEnabled(self._mode_combo.currentData() != "none")

    def _apply(self) -> None:
        name = single_line_text(self._name_edit.text())
        if name:
            self._group.name = name
        self._group.mode  = self._mode_combo.currentData()
        try:
            self._group.count = max(1, int(self._count_edit.text().strip() or "1"))
        except ValueError:
            self._group.count = 1
        self.accept()
