from __future__ import annotations

from enum import Enum
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QDoubleSpinBox, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

import db.env_db as _env_db
import db.generation_plan_db as _plan_db
from core.i18n import tr
from ui.flow_layout import FlowLayout
from ui.prompt_editor import PromptEditor
from ui.styles import RED, SUBTEXT, SURFACE0, SURFACE1, SURFACE2, TEXT, themed_button_style


_SCHEDULERS: dict[str, list[str]] = {
    "sdxl": ["euler", "euler_a", "dpmpp_2m", "dpmpp_2m_sde", "ddim", "lms", "heun", "unipc"],
    "sd-1": ["euler", "euler_a", "dpmpp_2m", "dpmpp_2m_sde", "ddim", "lms", "heun", "unipc"],
    "flux": ["euler", "heun", "lcm"],
    "flux2": ["euler", "heun", "lcm"],
    "z-image": ["euler", "heun", "lcm"],
    "anima": ["euler", "heun", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "lcm"],
}


class _RowState(Enum):
    EMPTY = "empty"
    EDITING = "editing"
    COMPLETE = "complete"


def _style_btn(btn: QPushButton, kind: str = "normal") -> None:
    btn.setStyleSheet(themed_button_style(kind))


def _bases() -> list[str]:
    rows = _env_db.fetchall(
        "SELECT DISTINCT base FROM models "
        "WHERE type='main' AND available=1 AND COALESCE(base,'')!='sdxl-refiner' "
        "ORDER BY base"
    )
    return [r["base"] or "sdxl" for r in rows]


def _model_rows(base: str) -> list[dict[str, Any]]:
    rows = _env_db.fetchall(
        """SELECT invoke_key, name, base, default_steps, default_cfg, default_scheduler
           FROM models
           WHERE type='main' AND available=1 AND COALESCE(base,'')!='sdxl-refiner'
             AND (?='' OR base=?)
           ORDER BY name""",
        (base or "", base or ""),
    )
    return [dict(r) for r in rows]


def _model_row(model_key: str) -> dict[str, Any] | None:
    row = _env_db.fetchone(
        """SELECT invoke_key, name, base, default_steps, default_cfg, default_scheduler
           FROM models WHERE invoke_key=? AND type='main'""",
        (model_key,),
    )
    return dict(row) if row else None


def _lora_rows(base: str) -> list[dict[str, Any]]:
    if not base:
        return []
    rows = _env_db.fetchall(
        "SELECT invoke_key, name, base FROM models "
        "WHERE type='lora' AND available=1 AND base=? "
        "ORDER BY name",
        (base,),
    )
    return [dict(r) for r in rows]


def _scheduler_values(base: str, current: str = "") -> list[str]:
    values = list(_SCHEDULERS.get(base or "sdxl", _SCHEDULERS["sdxl"]))
    current = (current or "").strip()
    if current and current not in values:
        values.insert(0, current)
    return values


def _set_combo_text(combo: QComboBox, text: str) -> None:
    idx = combo.findText(text)
    if idx < 0:
        combo.addItem(text, text)
        idx = combo.findText(text)
    combo.setCurrentIndex(idx)


class _PromptExtraDialog(QDialog):
    def __init__(self, positive: str, negative: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("plan.extra_prompt_title"))
        self.resize(980, 720)
        root = QVBoxLayout(self)
        note = QLabel(tr("plan.extra_prompt_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {SUBTEXT};")
        root.addWidget(note)
        self._editor = PromptEditor()
        self._editor.load_prompts(positive or "", negative or "")
        root.addWidget(self._editor, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def result_prompts(self) -> tuple[str, str]:
        return self._editor.compile_positive(), self._editor.compile_negative()


class _LoRASelectDialog(QDialog):
    def __init__(self, base: str, loras: list[dict[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("plan.lora_select_title"))
        self.resize(620, 420)
        self._base = base or ""
        self._loras = [dict(l) for l in loras if not l.get("missing")]

        root = QVBoxLayout(self)
        add_row = QHBoxLayout()
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._combo.addItem(tr("plan.lora_placeholder"), None)
        for row in _lora_rows(self._base):
            self._combo.addItem(f"{row['name']} ({row['base']})", row)
        completer = QCompleter(self._combo.model(), self._combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.activated.connect(self._add_lora_from_completion)
        self._combo.setCompleter(completer)
        if self._combo.lineEdit() is not None:
            self._combo.lineEdit().setPlaceholderText(tr("plan.lora_placeholder"))
        self._combo.activated.connect(self._add_lora_from_combo)
        add_row.addWidget(self._combo, stretch=1)
        root.addLayout(add_row)

        self._list_lay = QVBoxLayout()
        self._list_lay.setSpacing(4)
        root.addLayout(self._list_lay)
        root.addStretch()
        self._refresh_list()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _add_lora_from_completion(self, value) -> None:
        text = value.data() if hasattr(value, "data") else str(value)
        idx = self._combo.findText(text)
        if idx < 0:
            return
        self._combo.setCurrentIndex(idx)
        self._add_lora_from_combo()

    def _add_lora_from_combo(self, *_args) -> None:
        data = self._combo.currentData()
        self._combo.setCurrentIndex(0)
        if not isinstance(data, dict):
            return
        key = data["invoke_key"]
        existing_idx = next(
            (idx for idx, lora in enumerate(self._loras)
             if (lora.get("lora_key") or lora.get("invoke_key")) == key),
            -1,
        )
        if existing_idx >= 0:
            self._focus_lora_weight(existing_idx)
            return
        self._loras.append({
            "lora_key": key,
            "invoke_key": key,
            "name": data["name"] or key,
            "base": data["base"] or self._base,
            "weight": 0.75,
            "enabled": True,
        })
        self._refresh_list()
        self._focus_lora_weight(len(self._loras) - 1)

    def _refresh_list(self) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for idx, lora in enumerate(self._loras):
            row = QFrame()
            row.setFrameShape(QFrame.Shape.StyledPanel)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(6, 4, 6, 4)
            name = QLabel(lora.get("name") or lora.get("lora_key") or "")
            lay.addWidget(name, stretch=1)
            spin = QDoubleSpinBox()
            spin.setRange(-1.0, 2.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setValue(float(lora.get("weight") if lora.get("weight") is not None else 0.75))
            spin.valueChanged.connect(lambda value, i=idx: self._set_weight(i, value))
            spin.setProperty("lora_index", idx)
            lay.addWidget(spin)
            rm = QPushButton(tr("plan.btn_remove"))
            _style_btn(rm, "danger")
            rm.clicked.connect(lambda _=False, i=idx: self._remove_lora(i))
            lay.addWidget(rm)
            self._list_lay.addWidget(row)

    def _focus_lora_weight(self, idx: int) -> None:
        for n in range(self._list_lay.count()):
            row = self._list_lay.itemAt(n).widget()
            if row is None:
                continue
            for spin in row.findChildren(QDoubleSpinBox):
                if spin.property("lora_index") == idx:
                    QTimer.singleShot(0, spin.setFocus)
                    QTimer.singleShot(0, spin.selectAll)
                    return

    def _set_weight(self, idx: int, value: float) -> None:
        if 0 <= idx < len(self._loras):
            self._loras[idx]["weight"] = value

    def _remove_lora(self, idx: int) -> None:
        if 0 <= idx < len(self._loras):
            del self._loras[idx]
            self._refresh_list()

    def result_loras(self) -> list[dict[str, Any]]:
        return [dict(l) for l in self._loras]


class _PlanFormRow(QFrame):
    changed = Signal()
    request_delete = Signal(object)
    request_move = Signal(object, int)

    def __init__(self, row: dict[str, Any] | None, defaults: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._defaults = defaults or {}
        self._is_placeholder = row is None
        self._row = dict(row or {})
        self._initializing = True
        self._touched = bool(row)
        self._loras: list[dict[str, Any]] = [dict(l) for l in self._row.get("loras") or []]
        self._missing = bool(self._row.get("model_missing"))
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"QFrame {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; }}")
        self._build_ui()
        self._load_row()
        self._initializing = False
        self._apply_placeholder_enabled()
        self._refresh_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        top = FlowLayout(h_spacing=6, v_spacing=4)
        top.setSpacing(6)
        self._enabled = QCheckBox(tr("plan.col_enabled"))
        self._enabled.setChecked(True)
        self._enabled.stateChanged.connect(lambda *_args: self._mark_touched(auto_enable=False))
        top.addWidget(self._enabled)

        self._base = QComboBox()
        self._base.setFixedWidth(110)
        self._base.addItem(tr("plan.base_placeholder"), "")
        for base in _bases():
            self._base.addItem(base, base)
        self._base.currentIndexChanged.connect(self._on_base_changed)
        top.addWidget(self._base)

        self._model = QComboBox()
        self._model.setMinimumWidth(260)
        self._model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._model.currentIndexChanged.connect(self._on_model_changed)
        top.addWidget(self._model)

        self._prompt_btn = QPushButton("🧩")
        self._prompt_btn.setToolTip(tr("plan.extra_prompt_tooltip"))
        _style_btn(self._prompt_btn, "normal")
        self._prompt_btn.clicked.connect(self._edit_extra_prompt)
        top.addWidget(self._prompt_btn)

        self._delete_btn = QPushButton("×")
        _style_btn(self._delete_btn, "danger")
        self._delete_btn.clicked.connect(lambda: self.request_delete.emit(self))
        top.addWidget(self._delete_btn)
        root.addLayout(top)

        params = FlowLayout(h_spacing=8, v_spacing=4)
        params.setSpacing(8)
        params.addWidget(QLabel(tr("plan.col_count")))
        self._count = QSpinBox()
        self._count.setRange(1, 999)
        self._count.setSingleStep(1)
        self._count.setFixedWidth(70)
        self._count.valueChanged.connect(self._mark_touched)
        params.addWidget(self._count)

        params.addWidget(QLabel(tr("plan.col_steps")))
        self._steps = QSpinBox()
        self._steps.setRange(1, 100)
        self._steps.setSingleStep(1)
        self._steps.setFixedWidth(76)
        self._steps.valueChanged.connect(self._mark_touched)
        params.addWidget(self._steps)

        params.addWidget(QLabel(tr("plan.col_cfg")))
        self._cfg = QDoubleSpinBox()
        # CFG は全モデルで 1.0 未満は不正（1.0=ガイダンスなし）。メインUIと同じ下限1.0に統一。
        self._cfg.setRange(1.0, 20.0)
        self._cfg.setSingleStep(0.5)
        self._cfg.setDecimals(1)
        self._cfg.setFixedWidth(76)
        self._cfg.valueChanged.connect(self._mark_touched)
        params.addWidget(self._cfg)

        params.addWidget(QLabel(tr("plan.col_scheduler")))
        self._scheduler = QComboBox()
        self._scheduler.setFixedWidth(150)
        self._scheduler.currentIndexChanged.connect(self._mark_touched)
        params.addWidget(self._scheduler)
        up = QPushButton(tr("plan.btn_up"))
        _style_btn(up)
        up.clicked.connect(lambda: self.request_move.emit(self, -1))
        params.addWidget(up)
        down = QPushButton(tr("plan.btn_down"))
        _style_btn(down)
        down.clicked.connect(lambda: self.request_move.emit(self, 1))
        params.addWidget(down)
        root.addLayout(params)

        lora_row = FlowLayout(h_spacing=6, v_spacing=4)
        lora_row.setSpacing(6)
        lora_row.addWidget(QLabel(tr("plan.col_loras")))
        self._lora_box = QWidget()
        self._lora_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._lora_lay = FlowLayout(self._lora_box, h_spacing=4, v_spacing=4)
        self._lora_lay.setContentsMargins(0, 0, 0, 0)
        lora_row.addWidget(self._lora_box)
        self._lora_btn = QPushButton(tr("plan.lora_add"))
        _style_btn(self._lora_btn, "accent")
        self._lora_btn.clicked.connect(self._edit_loras)
        lora_row.addWidget(self._lora_btn)
        root.addLayout(lora_row)

    def _load_row(self) -> None:
        if self._is_placeholder:
            self._populate_models()
            self._set_scheduler_items("")
            self._enabled.setChecked(False)
            self._refresh_lora_chips()
            return
        base = self._row.get("model_base") or self._defaults.get("model_base") or ""
        if not base and self._defaults.get("model_key"):
            m = _model_row(str(self._defaults["model_key"]))
            base = m["base"] if m else ""
        if base:
            idx = self._base.findData(base)
            if idx >= 0:
                self._base.setCurrentIndex(idx)
        self._populate_models()
        model_key = self._row.get("model_key") or self._defaults.get("model_key") or ""
        if model_key:
            idx = self._model.findData(model_key)
            if idx >= 0:
                self._model.setCurrentIndex(idx)
            elif self._row.get("model_name"):
                self._model.addItem(tr("plan.missing_model", name=self._row["model_name"]), model_key)
                self._model.setCurrentIndex(self._model.count() - 1)
        self._count.setValue(int(self._row.get("image_count") or 1))
        self._steps.setValue(int(self._row.get("steps") or self._defaults.get("steps") or 30))
        self._cfg.setValue(float(self._row.get("cfg_scale") or self._defaults.get("cfg_scale") or 4.5))
        self._set_scheduler_items(self._row.get("scheduler") or self._defaults.get("scheduler") or "")
        self._enabled.setChecked(bool(self._row.get("enabled", True)))
        self._refresh_lora_chips()

    def _mark_touched(self, *_args, auto_enable: bool = True) -> None:
        if self._initializing:
            return
        self._touch(auto_enable=auto_enable)
        self._refresh_state()
        self.changed.emit()

    def _touch(self, *, auto_enable: bool = True) -> None:
        self._touched = True
        if auto_enable and not self._enabled.isChecked():
            self._enabled.blockSignals(True)
            self._enabled.setChecked(True)
            self._enabled.blockSignals(False)

    def _on_base_changed(self) -> None:
        if not self._initializing:
            self._touch()
        self._populate_models()
        self._set_scheduler_items("")
        if not self._initializing:
            self._loras = []
            self._refresh_lora_chips()
            self._apply_placeholder_enabled()
            self._refresh_state()
            self.changed.emit()

    def _on_model_changed(self) -> None:
        if self._initializing:
            return
        self._touch()
        model = self._current_model_row()
        if model:
            if model.get("default_steps") is not None:
                self._steps.setValue(int(model["default_steps"]))
            else:
                self._steps.setValue(30)
            if model.get("default_cfg") is not None and float(model["default_cfg"]) > 0:
                self._cfg.setValue(float(model["default_cfg"]))
            else:
                self._cfg.setValue(4.5)
            self._set_scheduler_items(model.get("default_scheduler") or "")
        self._refresh_state()
        self.changed.emit()

    def _populate_models(self) -> None:
        current = self._model.currentData()
        base = self._base.currentData() or ""
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItem(tr("plan.model_placeholder"), "")
        if not base:
            self._model.blockSignals(False)
            return
        for row in _model_rows(base):
            self._model.addItem(row["name"] or row["invoke_key"], row["invoke_key"])
        if current:
            idx = self._model.findData(current)
            if idx >= 0:
                self._model.setCurrentIndex(idx)
        self._model.blockSignals(False)

    def _set_scheduler_items(self, current: str) -> None:
        base = self._base.currentData() or ""
        values = _scheduler_values(base, current) if base or current else []
        self._scheduler.blockSignals(True)
        self._scheduler.clear()
        self._scheduler.addItem("", "")
        for value in values:
            self._scheduler.addItem(value, value)
        idx = self._scheduler.findText(current) if current else -1
        self._scheduler.setCurrentIndex(idx if idx >= 0 else 0)
        self._scheduler.blockSignals(False)

    def _current_model_row(self) -> dict[str, Any] | None:
        key = self._model.currentData()
        return _model_row(str(key)) if key else None

    def _edit_extra_prompt(self) -> None:
        self._touch()
        dlg = _PromptExtraDialog(
            self._row.get("extra_positive", ""),
            self._row.get("extra_negative", ""),
            self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pos, neg = dlg.result_prompts()
            self._row["extra_positive"] = pos
            self._row["extra_negative"] = neg
            self._refresh_state()
            self.changed.emit()

    def _edit_loras(self) -> None:
        if not self._base.currentData():
            QMessageBox.warning(self, tr("plan.invalid_title"), tr("plan.base_required_msg"))
            return
        self._touch()
        dlg = _LoRASelectDialog(str(self._base.currentData() or ""), self._loras, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._loras = dlg.result_loras()
            self._refresh_lora_chips()
            self._refresh_state()
            self.changed.emit()

    def _refresh_lora_chips(self) -> None:
        while self._lora_lay.count():
            item = self._lora_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for lora in self._loras:
            label = f"{lora.get('name') or lora.get('lora_key')} {float(lora.get('weight') or 0.75):g}"
            chip = QPushButton(label)
            _style_btn(chip)
            chip.clicked.connect(self._edit_loras)
            self._lora_lay.addWidget(chip)

    def _refresh_state(self) -> None:
        self._apply_placeholder_enabled()
        state = self.state()
        if state == _RowState.EDITING:
            self.setStyleSheet(f"QFrame {{ background: {SURFACE0}; border: 2px solid {RED}; }}")
        elif state == _RowState.EMPTY:
            self.setStyleSheet(f"QFrame {{ background: {SURFACE1}; border: 1px dashed {SURFACE2}; }}")
        else:
            self.setStyleSheet(f"QFrame {{ background: {SURFACE0}; border: 1px solid {SURFACE2}; }}")
        self._delete_btn.setVisible(state != _RowState.EMPTY)

    def _apply_placeholder_enabled(self) -> None:
        enabled = bool(self._base.currentData()) or not self._is_placeholder
        for widget in (self._count, self._steps, self._cfg, self._scheduler, self._prompt_btn, self._lora_btn):
            widget.setEnabled(enabled)
        self._apply_cfg_lock()

    def _apply_cfg_lock(self) -> None:
        """ベース別 CFG ポリシー: flux2 等は CFG を 1.0 固定・編集不可にする。

        メインUIと同じ状態制御をプラン行にも適用する（入力段階で確定）。
        無効化は _apply_placeholder_enabled の後に最終決定する必要があるため、
        その末尾から呼ぶ。
        """
        from core.gen_params import cfg_is_locked, LOCKED_CFG_VALUE
        base = self._base.currentData() or ""
        if cfg_is_locked(base):
            if abs(self._cfg.value() - LOCKED_CFG_VALUE) > 1e-9:
                self._cfg.blockSignals(True)
                self._cfg.setValue(LOCKED_CFG_VALUE)
                self._cfg.blockSignals(False)
            self._cfg.setEnabled(False)
            self._cfg.setToolTip(tr("main.cfg_locked_tooltip"))
        else:
            self._cfg.setToolTip("")

    def state(self) -> _RowState:
        if not self._touched and not self._model.currentData():
            return _RowState.EMPTY
        if not self._base.currentData() or not self._model.currentData() or not self._scheduler.currentText():
            return _RowState.EDITING
        return _RowState.COMPLETE

    def validation_error(self) -> str | None:
        if self.state() != _RowState.EDITING:
            return None
        if not self._base.currentData():
            return tr("plan.base_required_msg")
        if not self._model.currentData():
            return tr("plan.model_required_msg")
        if not self._scheduler.currentText():
            return tr("plan.scheduler_required_msg")
        return tr("plan.row_incomplete_msg")

    def result_row(self) -> dict[str, Any] | None:
        if self.state() != _RowState.COMPLETE:
            return None
        model = self._current_model_row()
        if not model:
            return None
        from core.gen_params import cfg_is_locked, LOCKED_CFG_VALUE
        cfg_value = (
            LOCKED_CFG_VALUE if cfg_is_locked(model["base"] or "")
            else self._cfg.value()
        )
        return {
            "enabled": self._enabled.isChecked(),
            "model_key": model["invoke_key"],
            "model_name": model["name"] or model["invoke_key"],
            "model_base": model["base"] or "",
            "image_count": self._count.value(),
            "steps": self._steps.value(),
            "cfg_scale": cfg_value,
            "scheduler": self._scheduler.currentText(),
            "extra_positive": self._row.get("extra_positive", ""),
            "extra_negative": self._row.get("extra_negative", ""),
            "loras": [dict(l) for l in self._loras],
        }


class GenerationPlanEditor(QWidget):
    plans_changed = Signal()
    current_plan_changed = Signal(object)
    save_close_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None, *, defaults: dict[str, Any] | None = None):
        super().__init__(parent)
        self._defaults = defaults or {}
        self._plan_id: int | None = None
        self._row_widgets: list[_PlanFormRow] = []
        self._pending_rows: dict[int, list[dict[str, Any]]] = {}
        self._loading = False

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self._plan_combo = QComboBox()
        self._plan_combo.currentIndexChanged.connect(self._on_plan_changed)
        top.addWidget(QLabel(tr("plan.plan_label")))
        top.addWidget(self._plan_combo, stretch=1)
        for text, handler, kind in [
            (tr("plan.btn_new"), self._new_plan, "accent"),
            (tr("plan.btn_rename"), self._rename_plan, "normal"),
            (tr("plan.btn_delete"), self._delete_plan, "danger"),
        ]:
            btn = QPushButton(text)
            _style_btn(btn, kind)
            btn.clicked.connect(handler)
            top.addWidget(btn)
        top.addSpacing(12)
        save_close_btn = QPushButton(tr("plan.btn_save_close"))
        _style_btn(save_close_btn, "success")
        save_close_btn.clicked.connect(self.save_close_requested.emit)
        top.addWidget(save_close_btn)
        cancel_btn = QPushButton(tr("plan.btn_cancel"))
        _style_btn(cancel_btn, "normal")
        cancel_btn.clicked.connect(self.cancel_requested.emit)
        top.addWidget(cancel_btn)
        root.addLayout(top)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._rows_lay = QVBoxLayout(self._body)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(8)
        self._scroll.setWidget(self._body)
        root.addWidget(self._scroll, stretch=1)

        self.refresh_plans()

    def set_defaults(self, defaults: dict[str, Any]) -> None:
        self._defaults = defaults or {}

    def refresh_plans(self, select_id: int | None = None) -> None:
        old_id = select_id if select_id is not None else self.current_plan_id()
        self._loading = True
        self._plan_combo.clear()
        for plan in _plan_db.list_plans():
            self._plan_combo.addItem(str(plan["name"]), int(plan["id"]))
        if old_id:
            idx = self._plan_combo.findData(old_id)
            if idx >= 0:
                self._plan_combo.setCurrentIndex(idx)
        self._loading = False
        self._load_current_plan()

    def current_plan_id(self) -> int | None:
        data = self._plan_combo.currentData()
        return int(data) if data is not None else None

    def _on_plan_changed(self) -> None:
        if not self._loading:
            if not self._validate_rows():
                self._loading = True
                idx = self._plan_combo.findData(self._plan_id) if self._plan_id else -1
                if idx >= 0:
                    self._plan_combo.setCurrentIndex(idx)
                self._loading = False
                return
            self._cache_current_rows()
            self._load_current_plan()

    def _load_current_plan(self) -> None:
        self._plan_id = self.current_plan_id()
        rows: list[dict[str, Any]] = []
        if self._plan_id is not None:
            if self._plan_id in self._pending_rows:
                rows = [dict(row) for row in self._pending_rows[self._plan_id]]
            else:
                plan = _plan_db.get_plan(self._plan_id)
                if plan:
                    rows = list(plan["rows"])
        self._set_rows(rows)
        self.current_plan_changed.emit(self._plan_id)

    def _set_rows(self, rows: list[dict[str, Any]]) -> None:
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._row_widgets = []
        for row in rows:
            self._append_row(row)
        self._append_empty_row()
        self._rows_lay.addStretch()

    def _append_row(self, row: dict[str, Any] | None) -> _PlanFormRow:
        self._remove_tail_stretch()
        widget = _PlanFormRow(row, self._defaults, self)
        widget.changed.connect(self._on_row_changed)
        widget.request_delete.connect(self._delete_row)
        widget.request_move.connect(self._move_row)
        self._row_widgets.append(widget)
        self._rows_lay.addWidget(widget)
        return widget

    def _remove_tail_stretch(self) -> None:
        if not self._rows_lay.count():
            return
        item = self._rows_lay.itemAt(self._rows_lay.count() - 1)
        if item is not None and item.spacerItem() is not None:
            self._rows_lay.takeAt(self._rows_lay.count() - 1)

    def _append_empty_row(self) -> None:
        if self._row_widgets and self._row_widgets[-1].state() == _RowState.EMPTY:
            return
        self._append_row(None)

    def _on_row_changed(self) -> None:
        if self._row_widgets and self._row_widgets[-1].state() == _RowState.COMPLETE:
            self._append_empty_row()
            self._rows_lay.addStretch()

    def _delete_row(self, row: _PlanFormRow) -> None:
        if row not in self._row_widgets:
            return
        self._row_widgets.remove(row)
        self._rows_lay.removeWidget(row)
        row.deleteLater()
        self._append_empty_row()

    def _move_row(self, row: _PlanFormRow, delta: int) -> None:
        if row not in self._row_widgets or row.state() != _RowState.COMPLETE:
            return
        rows = [w for w in self._row_widgets if w.state() == _RowState.COMPLETE]
        idx = rows.index(row)
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(rows):
            return
        data = [w.result_row() for w in rows]
        data[idx], data[new_idx] = data[new_idx], data[idx]
        self._set_rows([r for r in data if r])

    def _validate_rows(self) -> bool:
        for row in self._row_widgets:
            msg = row.validation_error()
            if msg:
                QMessageBox.warning(self, tr("plan.invalid_title"), msg)
                return False
        return True

    def _new_plan(self) -> None:
        if not self._validate_rows():
            return
        self._cache_current_rows()
        name, ok = QInputDialog.getText(self, tr("plan.new_title"), tr("plan.name_prompt"))
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if _plan_db.duplicate_name_exists(name):
            QMessageBox.warning(self, tr("plan.name_dup_title"), tr("plan.name_dup_msg"))
            return
        plan_id = _plan_db.create_plan(name)
        self.plans_changed.emit()
        self.refresh_plans(plan_id)

    def _rename_plan(self) -> None:
        plan_id = self.current_plan_id()
        if plan_id is None:
            return
        if not self._validate_rows():
            return
        self._cache_current_rows()
        current = self._plan_combo.currentText().rsplit(" (", 1)[0]
        name, ok = QInputDialog.getText(self, tr("plan.rename_title"), tr("plan.name_prompt"), text=current)
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if _plan_db.duplicate_name_exists(name, exclude_id=plan_id):
            QMessageBox.warning(self, tr("plan.name_dup_title"), tr("plan.name_dup_msg"))
            return
        _plan_db.rename_plan(plan_id, name)
        self.plans_changed.emit()
        self.refresh_plans(plan_id)

    def _delete_plan(self) -> None:
        plan_id = self.current_plan_id()
        if plan_id is None:
            return
        if QMessageBox.question(
            self, tr("plan.delete_title"), tr("plan.delete_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        _plan_db.delete_plan(plan_id)
        self._pending_rows.pop(plan_id, None)
        self.plans_changed.emit()
        self.refresh_plans()

    def _cache_current_rows(self) -> None:
        if self._plan_id is None:
            return
        self._pending_rows[self._plan_id] = [
            row for row in (w.result_row() for w in self._row_widgets)
            if row is not None
        ]

    def _save_rows(self) -> bool:
        if not self._validate_rows():
            return False
        self._cache_current_rows()
        if not self._pending_rows:
            return False
        for plan_id, rows in self._pending_rows.items():
            _plan_db.save_rows(plan_id, rows)
        self._pending_rows.clear()
        self.plans_changed.emit()
        return True


class GenerationPlanDialog(QDialog):
    plan_selected = Signal(object)

    def __init__(self, parent=None, *, defaults: dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("plan.dialog_title"))
        self.resize(980, 720)
        root = QVBoxLayout(self)
        self.editor = GenerationPlanEditor(self, defaults=defaults)
        self.editor.current_plan_changed.connect(self.plan_selected.emit)
        self.editor.save_close_requested.connect(self._save_and_close)
        self.editor.cancel_requested.connect(self.reject)
        root.addWidget(self.editor)

    def _save_and_close(self) -> None:
        if self.editor._validate_rows():
            self.editor._save_rows()
            self.accept()
