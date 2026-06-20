"""
テンプレート関連ダイアログ

  - TemplateChooserDialog: モデル選択時、複数テンプレートから選ばせる
  - FetchTemplateDialog:   InvokeAIから取得する際、新規/上書き/名前を指定
  - choose_template_for_model(): 外部から呼ぶヘルパー
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QDialogButtonBox, QMessageBox, QRadioButton, QLineEdit, QButtonGroup,
)
from PySide6.QtCore import Qt

import db.env_db as _env_db
from core.i18n import tr


class TemplateChooserDialog(QDialog):
    """ベースに複数テンプレートがある時の選択ダイアログ。Default が初期選択。"""

    def __init__(self, parent, templates: list[dict], model_name: str, base: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("template_dialog.chooser_title"))
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)

        info = QLabel(tr("template_dialog.chooser_info", name=model_name, base=base))
        info.setWordWrap(True)
        lay.addWidget(info)

        lay.addSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("template_dialog.chooser_template_label")))
        self._combo = QComboBox()
        default_idx = 0
        for i, t in enumerate(templates):
            mark = "  ★" if t["is_base_default"] else ""
            self._combo.addItem(f"{t['name']}{mark}", t["id"])
            if t["is_base_default"]:
                default_idx = i
        self._combo.setCurrentIndex(default_idx)
        row.addWidget(self._combo, 1)
        lay.addLayout(row)

        lay.addSpacing(8)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_template_id(self) -> int | None:
        return self._combo.currentData()


def choose_template_for_model(
    parent, model_name: str, base: str
) -> int | None:
    """
    モデル選択時のテンプレート解決。

    Returns:
        template_id (int) = 採用されたテンプレート
        None = ユーザーキャンセル or テンプレート未登録（呼び出し側でエラー扱い）
    """
    rows = _env_db.fetchall(
        "SELECT id, name, cache_key, is_base_default FROM templates "
        "WHERE base=? ORDER BY is_base_default DESC, name ASC",
        (base,),
    )
    templates = [dict(r) for r in rows]

    # 0個: 取得方法を案内
    if not templates:
        QMessageBox.information(
            parent,
            tr("template_dialog.no_template_title"),
            tr("template_dialog.no_template_msg", base=base),
        )
        return None

    # 1個: 無条件採用
    if len(templates) == 1:
        return templates[0]["id"]

    # 複数: 選択ダイアログ
    dlg = TemplateChooserDialog(parent, templates, model_name, base)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.selected_template_id()
    return None


class FetchTemplateDialog(QDialog):
    """InvokeAIから取得する際の保存先指定ダイアログ。"""

    MODE_NEW = "new"
    MODE_OVERWRITE = "overwrite"

    def __init__(self, parent, existing_templates: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("template_dialog.fetch_title"))
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)

        info = QLabel(tr("template_dialog.fetch_info"))
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addSpacing(6)

        self._group = QButtonGroup(self)

        # 新規作成
        self._rb_new = QRadioButton(tr("template_dialog.fetch_rb_new"))
        self._rb_new.setChecked(True)
        self._group.addButton(self._rb_new)
        lay.addWidget(self._rb_new)

        new_row = QHBoxLayout()
        new_row.addSpacing(24)
        new_row.addWidget(QLabel(tr("template_dialog.fetch_name_label")))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("template_dialog.fetch_name_placeholder"))
        new_row.addWidget(self._name_edit, 1)
        lay.addLayout(new_row)

        lay.addSpacing(6)

        # 既存に上書き
        self._rb_overwrite = QRadioButton(tr("template_dialog.fetch_rb_overwrite"))
        self._rb_overwrite.setEnabled(bool(existing_templates))
        self._group.addButton(self._rb_overwrite)
        lay.addWidget(self._rb_overwrite)

        ow_row = QHBoxLayout()
        ow_row.addSpacing(24)
        ow_row.addWidget(QLabel(tr("template_dialog.fetch_overwrite_label")))
        self._overwrite_combo = QComboBox()
        for t in existing_templates:
            label = f"{t['name']}  ({t['base']})"
            if t.get("is_base_default"):
                label += "  ★"
            self._overwrite_combo.addItem(label, t["id"])
        self._overwrite_combo.setEnabled(bool(existing_templates))
        ow_row.addWidget(self._overwrite_combo, 1)
        lay.addLayout(ow_row)

        # コンボ操作で「上書き」ラジオに切り替える（disable切替はポップアップ詰まりの原因になるため使わない）
        self._overwrite_combo.activated.connect(
            lambda _i: self._rb_overwrite.setChecked(True)
        )
        self._name_edit.textEdited.connect(
            lambda _t: self._rb_new.setChecked(True)
        )

        lay.addSpacing(10)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def result_mode(self) -> str:
        return self.MODE_OVERWRITE if self._rb_overwrite.isChecked() else self.MODE_NEW

    def result_name(self) -> str:
        return self._name_edit.text().strip()

    def result_overwrite_id(self) -> int | None:
        return self._overwrite_combo.currentData() if self._rb_overwrite.isChecked() else None
