from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QByteArray, QEasingCurve, QElapsedTimer, QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal,
    QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QCursor, QGuiApplication, QPainter,
    QPainterPath, QPen, QPixmap, QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRubberBand,
    QToolButton,
    QVBoxLayout,
    QAbstractScrollArea,
    QMenu,
    QSizePolicy,
    QWidget,
)

from core.i18n import tr
from ui.styles import (
    ACCENT, EMOJI_ICON_SS, RED, SURFACE0, SURFACE1, SURFACE2, TEXT, SUBTEXT,
    themed_button_style, ui_font,
)

ZOOM_MIN = 0.2
ZOOM_MAX = 2.0
ZOOM_STEP = 1.2  # Ctrl+ホイール1ノッチあたりの倍率

NodeKey = tuple[str, int]  # (history_db_name, history_id)


def _read_app_setting(key: str, default: str = "") -> str:
    try:
        import db.app_db as _app_db
        row = _app_db.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
        return str(row["value"]) if row else default
    except Exception:
        return default


def _write_app_setting(key: str, value: str) -> None:
    try:
        import db.app_db as _app_db
        _app_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    except Exception:
        pass


def _available_rect_for(widget: QWidget):
    screen = widget.screen() or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def _restore_saved_geometry(widget: QWidget, key: str) -> bool:
    geo_hex = _read_app_setting(key, "")
    if not geo_hex:
        return False
    try:
        if not widget.restoreGeometry(QByteArray.fromHex(geo_hex.encode("ascii"))):
            return False
    except Exception:
        return False
    _clamp_widget_to_visible_screen(widget)
    return True


def _save_geometry(widget: QWidget, key: str) -> None:
    try:
        _write_app_setting(key, bytes(widget.saveGeometry().toHex()).decode("ascii"))
    except Exception:
        pass


def _clamp_widget_to_visible_screen(widget: QWidget) -> None:
    app = QApplication.instance()
    screens = app.screens() if app is not None else []
    if not screens:
        return
    frame = widget.frameGeometry()
    for screen in screens:
        if screen.availableGeometry().intersects(frame):
            return
    avail = screens[0].availableGeometry()
    frame.moveCenter(avail.center())
    widget.move(frame.topLeft())


def _reveal_first_show(widget: QWidget) -> None:
    """ダイアログ初回表示時の白いウィンドウのちらつきを避けるためフェードインする。

    __init__ で setWindowOpacity(0.0) しておき、最初の showEvent でここを呼ぶと、
    レイアウト確定後に不透明へフェードする。2回目以降は何もしない。
    （子ダイアログはタスクバーボタンを持たないので、メインウィンドウと違い
      透明な層化ウィンドウによるアイコン取りこぼし問題は起きない）
    """
    if getattr(widget, "_did_reveal", False):
        return
    widget._did_reveal = True
    QTimer.singleShot(90, lambda: widget.setWindowOpacity(1.0))


@dataclass(frozen=True)
class HistoryMapNode:
    history_db: str
    history_id: int
    parent_db: str | None
    parent_id: int | None
    created_at: str
    deleted_at: str | None
    rating: int = 0  # image_reviews.rating（0=未評価）

    @property
    def gen_id(self) -> int:
        return self.history_id

    @property
    def parent_gen_id(self) -> int | None:
        return self.parent_id

    @property
    def key(self) -> NodeKey:
        return (self.history_db, self.history_id)

    @property
    def parent_key(self) -> NodeKey | None:
        if self.parent_db is None:
            return None
        return (self.parent_db, self.parent_id)  # type: ignore[return-value]


NODE_W = 82
NODE_H = 110  # 64サムネ + ラベル行 + ★行

GLOW_PERIOD_MS = 3000  # 現在地の回転発光: 3秒で一回転


class _CurrentGlowItem(QGraphicsItem):
    """
    現在地ノードの周囲を回る発光枠。

    円錐グラデーション（アクセント色⇔明色の繰り返し・暗部なし）の角度を
    外部タイマーで回し、「色味の違う光が枠をぐるぐる回る」見た目にする。
    """

    _MARGIN = 4  # ノード枠の外側に出す量

    def __init__(self, node_rect: QRectF, parent: QGraphicsItem, *, color: QColor | None = None):
        super().__init__(parent)
        self._angle = 0.0
        self._base_color = QColor(color) if color is not None else QColor(ACCENT)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(10)
        m = self._MARGIN
        self._rect = QRectF(
            node_rect.x() - m, node_rect.y() - m,
            node_rect.width() + 2 * m, node_rect.height() + 2 * m,
        )

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-3, -3, 3, 3)

    def set_angle(self, angle: float) -> None:
        self._angle = angle % 360.0
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        base = QColor(self._base_color)
        bright = QColor(self._base_color).lighter(180)
        grad = QConicalGradient(self._rect.center(), -self._angle)
        # 暗い区間を作らず、2色の帯がシームレスに巡るストップ配置
        grad.setColorAt(0.00, base)
        grad.setColorAt(0.25, bright)
        grad.setColorAt(0.50, base)
        grad.setColorAt(0.75, bright)
        grad.setColorAt(1.00, base)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QBrush(grad), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self._rect, 6, 6)


class _NodeItem(QGraphicsRectItem):
    def __init__(
        self,
        node: HistoryMapNode,
        *,
        is_current: bool,
        is_opened_target: bool,
        is_root: bool,
        text_color: str = TEXT,
    ):
        super().__init__(QRectF(0, 0, NODE_W, NODE_H))
        self.node = node
        self.is_root = is_root
        self._is_current = is_current
        self._is_opened_target = is_opened_target
        self._text_color = text_color  # ラベル文字色（設定/ツリー上書きで解決済み）
        self._is_removed = node.deleted_at is not None
        self._selected = False
        self._is_drop_target = False
        self._hovered = False
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        # 注: アイテムに明示カーソルを設定しない。QGraphicsView がアイテムカーソルの
        # 保存/復元を行うため、パン中の手のひらカーソル制御と干渉する
        if node.deleted_at is not None:
            self.setToolTip(tr("history_map.target_removed"))
        elif is_current:
            self.setToolTip(tr("history_map.current_node"))
        else:
            self.setToolTip(tr("history_map.jump_tooltip"))

        thumb_frame = QGraphicsRectItem(QRectF(0, 0, 64, 64), self)
        thumb_frame.setPos(9, 8)
        thumb_frame.setPen(QPen(QColor(SURFACE1), 1))
        thumb_frame.setBrush(QBrush(QColor(SURFACE0)))
        pix = self._load_thumbnail(node.history_db, node.history_id)
        if pix is not None and not pix.isNull():
            pix_item = QGraphicsPixmapItem(
                pix.scaled(
                    64,
                    64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ),
                self,
            )
            pix_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            pix_item.setPos(
                9 + max(0, (64 - pix_item.pixmap().width()) / 2),
                8 + max(0, (64 - pix_item.pixmap().height()) / 2),
            )

        self._label = QGraphicsSimpleTextItem(tr("history_map.node_label", n=node.history_id), self)
        self._label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        label_rect = self._label.boundingRect()
        self._label.setPos(
            (self.rect().width() - label_rect.width()) / 2,
            74 + (16 - label_rect.height()) / 2,
        )

        # ★評価（未評価は薄い☆5つで揃える）
        rating = max(0, min(5, int(node.rating or 0)))
        self._stars = QGraphicsSimpleTextItem("★" * rating + "☆" * (5 - rating), self)
        self._stars.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        star_font = self._stars.font()
        star_font.setPointSize(max(7, star_font.pointSize() - 2))
        self._stars.setFont(star_font)
        self._has_rating = rating > 0
        stars_rect = self._stars.boundingRect()
        self._stars.setPos(
            (self.rect().width() - stars_rect.width()) / 2,
            92 + (14 - stars_rect.height()) / 2,
        )

        # 現在地マーカー（▼）: ノード上端の真上に表示
        if is_current:
            marker = QGraphicsSimpleTextItem("▼", self)
            marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            marker.setBrush(QBrush(QColor(ACCENT)))
            mfont = marker.font()
            mfont.setPointSize(mfont.pointSize() + 2)
            marker.setFont(mfont)
            mrect = marker.boundingRect()
            marker.setPos((self.rect().width() - mrect.width()) / 2, -mrect.height() - 2)

        self._apply_visual(False)

    @staticmethod
    def _load_thumbnail(history_db: str, history_id: int) -> QPixmap | None:
        from db.connections import get_history_conn, history_db_path
        db_path = history_db_path(history_db)
        if not db_path.exists():
            return None
        try:
            conn = get_history_conn(history_db)
            row = conn.execute(
                "SELECT thumbnail_data, local_path FROM generations WHERE id=?",
                (history_id,),
            ).fetchone()
        except Exception:
            return None
        if row and row["thumbnail_data"]:
            pix = QPixmap()
            if pix.loadFromData(bytes(row["thumbnail_data"])) and not pix.isNull():
                return pix
        local_path = str(row["local_path"] or "") if row else ""
        if local_path:
            pix = QPixmap(local_path)
            if not pix.isNull():
                return pix
        return None

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
            self._apply_visual(False)

    def set_drop_target(self, enabled: bool) -> None:
        if self._is_drop_target != enabled:
            self._is_drop_target = enabled
            self._apply_visual(False)

    def _view_is_panning(self) -> bool:
        """このシーンを表示しているビューがパン中かどうか。"""
        scene = self.scene()
        if scene is None:
            return False
        for view in scene.views():
            if getattr(view, "_pan_last", None) is not None:
                return True
        return False

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        # パン中はホバー表示しない。パン中はマウス移動イベントをビューが消費する
        # ため、シーンは「パン開始前のマウス位置」を記憶したままになる。スクロールで
        # その古い位置にノードが差しかかると hoverEnter が発火してしまう（誤点灯）
        if not self._view_is_panning():
            self._apply_visual(True)
            self._schedule_view_hover_badge()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self._hide_view_hover_badge()
        self._apply_visual(False)  # 消灯は常に安全
        super().hoverLeaveEvent(event)

    def _schedule_view_hover_badge(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        for view in scene.views():
            if hasattr(view, "schedule_hover_badge"):
                view.schedule_hover_badge(self)

    def _hide_view_hover_badge(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        for view in scene.views():
            if hasattr(view, "hide_hover_badge"):
                view.hide_hover_badge(self)

    def _apply_visual(self, hovered: bool) -> None:
        if self._is_drop_target:
            pen = QPen(QColor(ACCENT).lighter(150), 4)
        elif self._is_current:
            pen = QPen(QColor(ACCENT), 3)
        elif self._is_opened_target:
            pen = QPen(QColor(ACCENT), 2, Qt.PenStyle.DashLine)
        elif self._selected:
            pen = QPen(QColor(ACCENT), 2, Qt.PenStyle.DashLine)
        elif hovered:
            pen = QPen(QColor(ACCENT), 2)
        else:
            pen = QPen(QColor(SUBTEXT), 1)
        if self._is_removed:
            brush_color = QColor(SURFACE1 if not hovered else SURFACE2)
            label_color = SUBTEXT if not hovered else TEXT
            self.setOpacity(0.48 if not hovered else 0.72)
        elif hovered:
            brush_color = QColor(ACCENT)
            label_color = SURFACE0
            self.setOpacity(1.0)
        elif self._is_current:
            # 現在地: アクセント色の半透明塗りで背景を強調
            brush_color = QColor(ACCENT)
            brush_color.setAlpha(70)
            label_color = self._text_color
            self.setOpacity(1.0)
        else:
            brush_color = QColor(SURFACE2)
            label_color = self._text_color
            self.setOpacity(1.0)
        self.setPen(pen)
        self.setBrush(QBrush(brush_color))
        if hasattr(self, "_label"):
            self._label.setBrush(QBrush(QColor(label_color)))
        if hasattr(self, "_stars"):
            if self._has_rating:
                self._stars.setBrush(QBrush(QColor(SURFACE0 if hovered and not self._is_removed else ACCENT)))
            else:
                self._stars.setBrush(QBrush(QColor(label_color if hovered else SUBTEXT)))


class _HistoryMapScene(QGraphicsScene):
    """
    接続線を paint-only で描くシーン。

    線はノード位置から毎回導出される「描画結果」であり、独立した操作対象ではない。
    アイテムとして scene に追加しないため、items()/itemAt()/マウス/ホバー/選択の
    対象に一切ならず、クリック等で消える事故が構造的に起こらない。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edge_path = QPainterPath()
        self._edge_color = SUBTEXT          # 系統線の色（テーマ/設定/ツリー上書きで解決）
        self._node_text_color = TEXT        # ノードのラベル文字色（同上）

    def set_edge_path(self, path: QPainterPath) -> None:
        self._edge_path = path
        self.update()

    def set_edge_color(self, color: str) -> None:
        c = QColor(color)
        if c.isValid():
            self._edge_color = c.name()
            self.update()

    def set_node_text_color(self, color: str) -> None:
        c = QColor(color)
        if c.isValid():
            self._node_text_color = c.name()

    def drawBackground(self, painter, rect) -> None:
        super().drawBackground(painter, rect)  # 背景色
        if self._edge_path.isEmpty():
            return
        # ノードアイテムより下のレイヤーとして接続線を描く
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(self._edge_color), 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._edge_path)


class _HistoryMapView(QGraphicsView):
    node_clicked = Signal(str, int)              # 左クリック（履歴側の位置確認）
    preview_requested = Signal(str, int)         # 画像ウィンドウ表示
    apply_requested = Signal(str, int)           # 中央ペインに反映
    stack_requested = Signal(str, int)           # 現在地スタックへ追加
    color_requested = Signal(str, int)           # 履歴背景色の変更
    text_color_requested = Signal(str, int)      # 履歴の文字色の変更（ツリー単位）
    line_color_requested = Signal(str, int)      # 履歴の系統線色の変更（ツリー単位）
    edit_requested = Signal(str, int)            # 編集（履歴の編集ダイアログ）
    show_subtree_requested = Signal(str, int)    # ここ以下のみ表示
    show_full_requested = Signal()               # 全体表示
    detach_requested = Signal(str, int)          # 別系統にして移動（フォーカスも移る）
    erase_requested = Signal(str, int)           # 消去（マップからのみ・警告なし）
    delete_requested = Signal(str, int)          # 削除（ゴミ箱行き・警告あり）
    bulk_erase_requested = Signal(list)          # [(history_db, history_id), ...]
    bulk_delete_requested = Signal(list)         # [(history_db, history_id), ...]
    reparent_requested = Signal(str, int, str, int)  # child_db, child_id, parent_db, parent_id
    center_current_requested = Signal()          # 空白部メニュー「現在地へ」

    zoom_changed = Signal(float)                 # 現在の倍率（1.0=100%）

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._pan_last = None
        self._current_node_key = None  # 空白部メニューの色変更対象（現在地ツリー）
        self._is_enlarged = False      # 拡大ダイアログのビューか（フィット/全体表示の出し分け）
        self._pan_button = Qt.MouseButton.NoButton  # パンを開始したボタン
        self._selected_item: _NodeItem | None = None
        self._selected_items: set[_NodeItem] = set()
        self._rubber_origin: QPoint | None = None
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._drag_item: _NodeItem | None = None
        self._drag_start: QPoint | None = None
        self._dragging_node = False
        self._drag_ghost: QGraphicsRectItem | None = None
        self._drop_target_item: _NodeItem | None = None
        self._drop_target_glow: _CurrentGlowItem | None = None
        self._drop_glow_clock = QElapsedTimer()
        self._drop_glow_timer = QTimer(self)
        self._drop_glow_timer.setInterval(33)
        self._drop_glow_timer.timeout.connect(self._on_drop_glow_tick)
        self._view_restricted = False  # 「ここ以下のみ表示」中か
        self._active_history = ""      # アクティブ履歴名（編集メニューの有効判定用）
        self._zoom = 1.0
        self._hover_badge_item: _NodeItem | None = None
        self._hover_badge_token = 0
        self._hover_badge = QLabel(self.viewport())
        self._hover_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hover_badge.setFont(ui_font(delta=1, bold=True))
        self._hover_badge.setStyleSheet(
            f"QLabel {{ background: {SURFACE0}; color: {TEXT}; "
            f"border: 1px solid {ACCENT}; border-radius: 4px; padding: 3px 7px; }}"
        )
        self._hover_badge.hide()
        # ズームはカーソル位置を中心に行う
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def schedule_hover_badge(self, item: _NodeItem) -> None:
        self._hover_badge_item = item
        self._hover_badge_token += 1
        token = self._hover_badge_token
        self._hover_badge.hide()
        QTimer.singleShot(320, lambda: self._show_hover_badge_if_current(item, token))

    def hide_hover_badge(self, item: _NodeItem | None = None) -> None:
        if item is None or self._hover_badge_item is item:
            self._hover_badge_item = None
            self._hover_badge_token += 1
            self._hover_badge.hide()

    def _hover_badge_text(self, item: _NodeItem) -> str:
        label = tr("history_map.node_label", n=item.node.history_id)
        if getattr(item, "_is_current", False):
            return f"{tr('history_map.current_node')} {label}"
        return label

    def _show_hover_badge_if_current(self, item: _NodeItem, token: int) -> None:
        if token != self._hover_badge_token or self._hover_badge_item is not item:
            return
        try:
            if not item._hovered or item._view_is_panning():
                return
        except RuntimeError:
            return
        self._hover_badge.setText(self._hover_badge_text(item))
        self._position_hover_badge(item)
        self._hover_badge.show()
        self._hover_badge.raise_()

    def _position_hover_badge(self, item: _NodeItem | None = None) -> None:
        item = item or self._hover_badge_item
        if item is None:
            return
        if item.scene() is not self.scene():
            self.hide_hover_badge(item)
            return
        try:
            points = self.mapFromScene(item.sceneBoundingRect())
        except RuntimeError:
            self.hide_hover_badge(item)
            return
        rect = points.boundingRect()
        self._hover_badge.adjustSize()
        margin = 6
        x = rect.center().x() - self._hover_badge.width() // 2
        y = rect.top() - self._hover_badge.height() - margin
        x = max(4, min(x, self.viewport().width() - self._hover_badge.width() - 4))
        if y < 4:
            y = min(rect.bottom() + margin, self.viewport().height() - self._hover_badge.height() - 4)
        self._hover_badge.move(int(x), int(max(4, y)))

    def set_view_restricted(self, restricted: bool) -> None:
        self._view_restricted = bool(restricted)

    def set_active_history_name(self, name: str) -> None:
        self._active_history = str(name or "")

    # ── ズーム ──────────────────────────────────────────

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        """倍率を設定する（20%〜200%にクランプ）。"""
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))
        if abs(zoom - self._zoom) < 1e-9:
            return
        self._zoom = zoom
        self.setTransform(QTransform().scale(zoom, zoom))
        self._position_hover_badge()
        self.zoom_changed.emit(zoom)

    def zoom_in_step(self) -> None:
        """＋ボタン: 1段階拡大（ビュー中央を基準にズーム）。"""
        self._zoom_step(ZOOM_STEP)

    def zoom_out_step(self) -> None:
        """－ボタン: 1段階縮小（ビュー中央を基準にズーム）。"""
        self._zoom_step(1.0 / ZOOM_STEP)

    def _zoom_step(self, factor: float) -> None:
        # ボタン操作時はカーソルがビュー外にあるため、中央基準に切り替えて適用する
        prev = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        try:
            self.set_zoom(self._zoom * factor)
        finally:
            self.setTransformationAnchor(prev)

    def zoom_fit(self) -> None:
        """ツリー全体が収まる倍率にする（拡大はしない=上限100%）。"""
        scene = self.scene()
        if scene is None:
            return
        rect = scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        rect = rect.adjusted(-20, -20, 20, 20)
        vp = self.viewport().rect()
        if vp.isEmpty():
            return
        fit = min(vp.width() / rect.width(), vp.height() / rect.height())
        self.set_zoom(min(1.0, fit))
        self.centerOn(rect.center())

    # ── 現在地へのスムーズスクロール ──────────────────────
    def stop_center_anim(self) -> None:
        anim = getattr(self, "_center_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass
            self._center_anim = None

    def animate_center_on(self, x: float, y: float, *, duration: int = 480,
                          on_step=None, on_done=None) -> None:
        """ビュー中心をシーン座標(x,y)へ滑らかにスクロールする。

        いきなり centerOn でジャンプすると UX が悪いので、現在の中心から目標まで
        イージングで補間する。on_step は各フレーム前に呼ばれる（保存抑止フラグの
        制御用）。on_done は完了時。
        """
        self.stop_center_anim()
        start = self.mapToScene(self.viewport().rect().center())
        end = QPointF(float(x), float(y))
        if self.viewport().width() <= 1 or self.viewport().height() <= 1:
            # まだサイズ未確定: アニメせず即時に合わせる
            if on_step is not None:
                on_step()
            self.centerOn(end)
            if on_done is not None:
                on_done()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(int(duration))
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def _step(v) -> None:
            if on_step is not None:
                on_step()
            try:
                self.centerOn(v)
            except RuntimeError:
                pass

        def _finish() -> None:
            self._center_anim = None
            if on_done is not None:
                on_done()

        anim.valueChanged.connect(_step)
        anim.finished.connect(_finish)
        self._center_anim = anim
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def reset_selection(self) -> None:
        """rebuild で旧シーンのアイテムが破棄されるため参照だけ捨てる。"""
        self.hide_hover_badge()
        self._selected_item = None
        self._selected_items.clear()
        self._rubber_origin = None
        self._rubber_band.hide()
        self._drag_item = None
        self._drag_start = None
        self._dragging_node = False
        self._clear_drag_visuals()

    def _node_item_at(self, pos) -> _NodeItem | None:
        for item in self.items(pos):
            if item is self._drag_ghost or item is self._drop_target_glow:
                continue
            current = item
            while current is not None and not isinstance(current, _NodeItem):
                current = current.parentItem()
            if isinstance(current, _NodeItem):
                return current
        return None

    def _select_item(self, item: _NodeItem) -> None:
        self._set_selected_items([item])

    def _set_selected_items(self, items: list[_NodeItem]) -> None:
        new_items = set(items)
        for item in list(self._selected_items - new_items):
            try:
                item.set_selected(False)
                item.setSelected(False)
            except RuntimeError:
                pass
        for item in list(new_items - self._selected_items):
            try:
                item.set_selected(True)
                item.setSelected(True)
            except RuntimeError:
                pass
        self._selected_items = new_items
        self._selected_item = items[0] if len(items) == 1 else None

    def _sync_selected_from_scene(self) -> None:
        items = [
            item for item in self.scene().selectedItems()
            if isinstance(item, _NodeItem)
        ]
        self._set_selected_items(items)

    def _select_items_in_view_rect(self, rect: QRect) -> None:
        scene_rect = self.mapToScene(rect).boundingRect()
        items: list[_NodeItem] = []
        for item in self.scene().items(scene_rect):
            while item is not None and not isinstance(item, _NodeItem):
                item = item.parentItem()
            if isinstance(item, _NodeItem) and item.sceneBoundingRect().intersects(scene_rect):
                if item not in items:
                    items.append(item)
        self._set_selected_items(items)

    def _selected_node_keys(self) -> list[NodeKey]:
        keys: list[NodeKey] = []
        for item in list(self._selected_items):
            try:
                keys.append(item.node.key)
            except RuntimeError:
                pass
        return keys

    def node_mouse_press(self, item: _NodeItem, pos: QPoint, *, drag_enabled: bool = False) -> None:
        self._select_item(item)
        if drag_enabled:
            self._drag_item = item
            self._drag_start = pos
            self._dragging_node = False
        else:
            self._drag_item = None
            self._drag_start = None
            self._dragging_node = False
            self.node_clicked.emit(item.node.history_db, item.node.history_id)

    def node_mouse_move(self, item: _NodeItem, pos: QPoint) -> None:
        if self._drag_item is not item or self._drag_start is None:
            return
        if not self._dragging_node:
            dist = (pos - self._drag_start).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._dragging_node = True
                self.viewport().setCursor(Qt.CursorShape.DragMoveCursor)
                self._create_drag_ghost(item)
        if self._dragging_node:
            self._update_drag_visuals(pos)

    def node_mouse_release(self, item: _NodeItem, pos: QPoint) -> None:
        if self._drag_item is not item:
            return
        dragging = self._dragging_node
        self._drag_item = None
        self._drag_start = None
        self._dragging_node = False
        self.viewport().unsetCursor()
        self._clear_drag_visuals()
        if not dragging:
            return
        target = self._node_item_at(pos)
        if target is not None and target is not item:
            self.reparent_requested.emit(
                item.node.history_db,
                item.node.history_id,
                target.node.history_db,
                target.node.history_id,
            )

    def _create_drag_ghost(self, item: _NodeItem) -> None:
        self._clear_drag_visuals()
        ghost = QGraphicsRectItem(QRectF(0, 0, NODE_W, NODE_H))
        ghost.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        ghost.setZValue(1000)
        color = QColor(ACCENT)
        color.setAlpha(85)
        ghost.setBrush(QBrush(color))
        ghost.setPen(QPen(QColor(ACCENT).lighter(160), 2, Qt.PenStyle.DashLine))
        self.scene().addItem(ghost)
        self._drag_ghost = ghost

    def _update_drag_visuals(self, pos: QPoint) -> None:
        scene_pos = self.mapToScene(pos)
        if self._drag_ghost is not None:
            self._drag_ghost.setPos(scene_pos.x() - NODE_W / 2, scene_pos.y() - NODE_H / 2)
        target = self._node_item_at(pos)
        if target is self._drag_item:
            target = None
        self._set_drop_target_item(target)

    def _clear_drag_visuals(self) -> None:
        if self._drop_target_item is not None:
            try:
                self._drop_target_item.set_drop_target(False)
            except RuntimeError:
                pass
            self._drop_target_item = None
        if self._drop_target_glow is not None:
            try:
                scene = self._drop_target_glow.scene()
                if scene is not None:
                    scene.removeItem(self._drop_target_glow)
            except RuntimeError:
                pass
            self._drop_target_glow = None
        self._drop_glow_timer.stop()
        if self._drag_ghost is not None:
            try:
                scene = self._drag_ghost.scene()
                if scene is not None:
                    scene.removeItem(self._drag_ghost)
            except RuntimeError:
                pass
            self._drag_ghost = None

    def cancel_drag(self) -> bool:
        if self._drag_item is None and not self._dragging_node:
            return False
        self._drag_item = None
        self._drag_start = None
        self._dragging_node = False
        self.viewport().unsetCursor()
        self._clear_drag_visuals()
        return True

    def _set_drop_target_item(self, target: _NodeItem | None) -> None:
        if target is self._drop_target_item:
            return
        if self._drop_target_item is not None:
            self._drop_target_item.set_drop_target(False)
        if self._drop_target_glow is not None:
            try:
                scene = self._drop_target_glow.scene()
                if scene is not None:
                    scene.removeItem(self._drop_target_glow)
            except RuntimeError:
                pass
            self._drop_target_glow = None
        self._drop_target_item = target
        if target is None:
            self._drop_glow_timer.stop()
            return
        target.set_drop_target(True)
        glow = _CurrentGlowItem(target.rect(), target, color=QColor(RED))
        glow.setZValue(5)
        self._drop_target_glow = glow
        self._drop_glow_clock.start()
        self._drop_glow_timer.start()

    def _on_drop_glow_tick(self) -> None:
        glow = self._drop_target_glow
        if glow is None:
            self._drop_glow_timer.stop()
            return
        angle = (self._drop_glow_clock.elapsed() % GLOW_PERIOD_MS) / GLOW_PERIOD_MS * 360.0
        try:
            glow.set_angle(angle)
        except RuntimeError:
            self._drop_target_glow = None
            self._drop_glow_timer.stop()

    def viewportEvent(self, event) -> bool:
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress:
            button = event.button()
            pos = event.position().toPoint()
            if button == Qt.MouseButton.MiddleButton:
                self._begin_pan(event, Qt.MouseButton.MiddleButton)
                return True
            if button == Qt.MouseButton.LeftButton:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._rubber_origin = pos
                    self._rubber_band.setGeometry(QRect(pos, pos))
                    self._rubber_band.show()
                    return True
                item = self._node_item_at(pos)
                if item is not None:
                    self.node_mouse_press(
                        item,
                        pos,
                        drag_enabled=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
                    )
                    return True
                self._begin_pan(event, Qt.MouseButton.LeftButton)
                return True
        elif etype == QEvent.Type.MouseMove:
            pos = event.position().toPoint()
            if self._rubber_origin is not None:
                self._rubber_band.setGeometry(QRect(self._rubber_origin, pos).normalized())
                return True
            if self._drag_item is not None:
                self.node_mouse_move(self._drag_item, pos)
                return True
            if self._pan_last is not None:
                if not (event.buttons() & self._pan_button):
                    self._end_pan()
                else:
                    delta = pos - self._pan_last
                    self._pan_last = pos
                    self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                    self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
                return True
        elif etype == QEvent.Type.MouseButtonRelease:
            button = event.button()
            pos = event.position().toPoint()
            if self._rubber_origin is not None and button == Qt.MouseButton.LeftButton:
                rect = self._rubber_band.geometry()
                self._rubber_band.hide()
                self._rubber_origin = None
                self._select_items_in_view_rect(rect)
                return True
            if self._drag_item is not None and button == Qt.MouseButton.LeftButton:
                self.node_mouse_release(self._drag_item, pos)
                return True
            if self._pan_last is not None and (
                button == self._pan_button or not (event.buttons() & self._pan_button)
            ):
                self._end_pan()
                return True
        elif etype == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                if self.cancel_drag():
                    return True
                return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._begin_pan(event, Qt.MouseButton.MiddleButton)
            event.accept()
            return
        super().mousePressEvent(event)

    def _begin_pan(self, event, button) -> None:
        self.hide_hover_badge()
        self._pan_last = event.position().toPoint()
        self._pan_button = button
        # カーソルは viewport に対して設定する（ビュー本体では効かない環境がある）
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def _end_pan(self) -> None:
        self._pan_last = None
        self._pan_button = Qt.MouseButton.NoButton
        self.viewport().unsetCursor()
        # パン中はホバー表示を抑制しているため、終了時にカーソル直下のノードへ
        # ホバー表示を同期する（次の hoverLeave で通常の管理に戻る）
        item = self._node_item_at(self.viewport().mapFromGlobal(QCursor.pos()))
        if item is not None:
            item._apply_visual(True)
            item._schedule_view_hover_badge()

    def mouseMoveEvent(self, event) -> None:
        if self._pan_last is not None:
            if not (event.buttons() & self._pan_button):
                # 開始ボタンのリリースを取り逃した場合の自己修復
                # （手のひらカーソルが残り続けるのを防ぐ）
                self._end_pan()
            else:
                pos = event.position().toPoint()
                delta = pos - self._pan_last
                self._pan_last = pos
                hbar = self.horizontalScrollBar()
                vbar = self.verticalScrollBar()
                hbar.setValue(hbar.value() - delta.x())
                vbar.setValue(vbar.value() - delta.y())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_last is not None and (
            event.button() == self._pan_button
            or not (event.buttons() & self._pan_button)
        ):
            was_pan_button = event.button() == self._pan_button
            self._end_pan()
            if was_pan_button:
                event.accept()
                return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._sync_selected_from_scene()

    def wheelEvent(self, event) -> None:
        # ホイール＝拡大縮小（カーソル位置を中心に。修飾キー不要）。
        # マップ内の移動はホイールズーム＋左/中ドラッグパンで完結する
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta > 0:
            self.set_zoom(self._zoom * ZOOM_STEP)
        elif delta < 0:
            self.set_zoom(self._zoom / ZOOM_STEP)
        event.accept()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._position_hover_badge()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_hover_badge()

    def _show_blank_context_menu(self, event) -> None:
        """ノード以外（空白部）の右クリックメニュー。現在の系統に対する色変更と
        表示操作を出す。フィット/全体表示は拡大ダイアログのみ（埋め込みでは無意味）。"""
        menu = QMenu(self)
        key = self._current_node_key
        act_bg = act_text = act_line = None
        if key is not None:
            act_bg = menu.addAction(tr("history_map.menu_history_bg_color"))
            act_text = menu.addAction(tr("history_map.menu_history_text_color"))
            act_line = menu.addAction(tr("history_map.menu_history_line_color"))
            menu.addSeparator()
        act_center = menu.addAction(tr("history_map.menu_goto_current"))
        act_fit = act_full = None
        if self._is_enlarged:
            act_fit = menu.addAction(tr("history_map.menu_zoom_fit"))
            act_full = menu.addAction(tr("history_map.menu_show_full"))
            act_full.setEnabled(self._view_restricted)
        if menu.isEmpty():
            return
        chosen = menu.exec(event.globalPos())
        event.accept()
        if chosen is None:
            return
        if chosen is act_bg and key is not None:
            self.color_requested.emit(*key)
        elif chosen is act_text and key is not None:
            self.text_color_requested.emit(*key)
        elif chosen is act_line and key is not None:
            self.line_color_requested.emit(*key)
        elif chosen is act_center:
            self.center_current_requested.emit()
        elif chosen is act_fit:
            self.zoom_fit()
        elif chosen is act_full:
            self.show_full_requested.emit()

    def contextMenuEvent(self, event) -> None:
        item = self._node_item_at(event.pos())
        if item is None:
            keys = self._selected_node_keys()
            if keys:
                menu = QMenu(self)
                act_bulk_erase = menu.addAction(tr("history_map.menu_bulk_erase", n=len(keys)))
                act_bulk_delete = menu.addAction(tr("history_map.menu_bulk_delete", n=len(keys)))
                chosen = menu.exec(event.globalPos())
                if chosen is act_bulk_erase:
                    self.bulk_erase_requested.emit(keys)
                elif chosen is act_bulk_delete:
                    self.bulk_delete_requested.emit(keys)
                event.accept()
                return
            self._show_blank_context_menu(event)
            return
        if item not in self._selected_items:
            self._select_item(item)
        node = item.node
        deleted = node.deleted_at is not None
        menu = QMenu(self)
        selected_keys = self._selected_node_keys()

        # ── 編集（履歴の編集ダイアログ）──────────────────
        act_edit = menu.addAction(tr("history_map.menu_edit"))
        # アクティブ履歴のノードのみ編集可能（DB消失ノードも不可）
        act_edit.setEnabled(
            node.history_db == self._active_history
            and node.deleted_at != "missing_db"
        )
        act_open_image = menu.addAction(tr("history_map.menu_open_image_window"))
        act_open_image.setEnabled(node.deleted_at != "missing_db")
        menu.addSeparator()

        act_apply = menu.addAction(tr("history_map.menu_apply_center"))
        act_apply.setEnabled(node.deleted_at != "missing_db")
        act_stack = menu.addAction(tr("history_map.menu_stack_current"))
        act_stack.setEnabled(node.deleted_at != "missing_db")
        act_color = menu.addAction(tr("history_map.menu_history_bg_color"))
        act_color.setEnabled(node.deleted_at != "missing_db")
        act_text_color = menu.addAction(tr("history_map.menu_history_text_color"))
        act_text_color.setEnabled(node.deleted_at != "missing_db")
        act_line_color = menu.addAction(tr("history_map.menu_history_line_color"))
        act_line_color.setEnabled(node.deleted_at != "missing_db")
        menu.addSeparator()

        # ── 表示系 ──────────────────────────────────────
        act_subtree = menu.addAction(tr("history_map.menu_show_subtree"))
        act_full = menu.addAction(tr("history_map.menu_show_full"))
        act_full.setEnabled(self._view_restricted)  # 制限中のみ有効、普段はグレーアウト

        # ── 系譜操作（開祖には出さない＝開祖は消せないルール）──
        act_detach = act_erase = act_delete = None
        act_bulk_erase = act_bulk_delete = None
        if len(selected_keys) > 1:
            menu.addSeparator()
            act_bulk_erase = menu.addAction(tr("history_map.menu_bulk_erase", n=len(selected_keys)))
            act_bulk_delete = menu.addAction(tr("history_map.menu_bulk_delete", n=len(selected_keys)))
            menu.addSeparator()
        if not item.is_root:
            menu.addSeparator()
            if not deleted:
                act_detach = menu.addAction(tr("history_map.detach_subtree"))
                menu.addSeparator()
            # 消去=マップからのみ除去（警告なし）。グレーアウトノードにも出す
            act_erase = menu.addAction(tr("history_map.menu_erase"))
            if not deleted:
                # 削除=消去＋ゴミ箱行き（警告あり）。ゴミ箱済み行には出さない
                act_delete = menu.addAction(tr("history_map.menu_delete"))

        chosen = menu.exec(event.globalPos())
        args = (node.history_db, node.history_id)
        if chosen is None:
            pass
        elif chosen is act_edit:
            self.edit_requested.emit(*args)
        elif chosen is act_open_image:
            self.preview_requested.emit(*args)
        elif chosen is act_apply:
            self.apply_requested.emit(*args)
        elif chosen is act_stack:
            self.stack_requested.emit(*args)
        elif chosen is act_color:
            self.color_requested.emit(*args)
        elif chosen is act_text_color:
            self.text_color_requested.emit(*args)
        elif chosen is act_line_color:
            self.line_color_requested.emit(*args)
        elif chosen is act_subtree:
            self.show_subtree_requested.emit(*args)
        elif chosen is act_full:
            self.show_full_requested.emit()
        elif chosen is act_bulk_erase:
            self.bulk_erase_requested.emit(selected_keys)
        elif chosen is act_bulk_delete:
            self.bulk_delete_requested.emit(selected_keys)
        elif chosen is act_detach:
            self.detach_requested.emit(*args)
        elif chosen is act_erase:
            self.erase_requested.emit(*args)
        elif chosen is act_delete:
            self.delete_requested.emit(*args)
        event.accept()


class _ImagePreviewView(QGraphicsView):
    zoom_changed = Signal(float, str)  # zoom, mode

    def __init__(self, parent=None):
        self._scene = QGraphicsScene(parent)
        super().__init__(self._scene, parent)
        self._pix_item = QGraphicsPixmapItem()
        self._pix_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._pix_item)
        self._zoom = 1.0
        self._zoom_mode = "fit"  # fit / original / manual
        self._pan_last: QPoint | None = None
        self.setBackgroundBrush(QBrush(QColor(SURFACE0)))
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def set_background_color(self, color: str) -> None:
        """画像まわり（レターボックス）の背景色を設定する。"""
        c = QColor(color)
        if c.isValid():
            self.setBackgroundBrush(QBrush(c))

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix_item.setPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))
        if self._zoom_mode == "fit":
            QTimer.singleShot(0, self.fit_image)
        elif self._zoom_mode == "original":
            self.original_size()
        else:
            self._apply_zoom(self._zoom)

    def fit_image(self) -> None:
        pix = self._pix_item.pixmap()
        if pix.isNull():
            return
        self._zoom_mode = "fit"
        self.resetTransform()
        self.fitInView(QRectF(pix.rect()), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = float(self.transform().m11() or 1.0)
        self.zoom_changed.emit(self._zoom, self._zoom_mode)

    def original_size(self) -> None:
        self._zoom_mode = "original"
        self._apply_zoom(1.0)

    def _apply_zoom(self, zoom: float) -> None:
        self._zoom = max(0.05, min(8.0, float(zoom)))
        self.setTransform(QTransform().scale(self._zoom, self._zoom))
        self.zoom_changed.emit(self._zoom, self._zoom_mode)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        self._zoom_mode = "manual"
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self._apply_zoom(self._zoom * factor)
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._zoom_mode == "fit":
            self.fit_image()

    def mousePressEvent(self, event) -> None:
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._pan_last = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_last is not None:
            pos = event.position().toPoint()
            delta = pos - self._pan_last
            self._pan_last = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_last is not None and event.button() in (
            Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
        ):
            self._pan_last = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        event.accept()


class _HistoryImageViewerDialog(QDialog):
    jump_requested = Signal(str, int)
    node_requested = Signal(str, int)

    _GEOMETRY_KEY = "history_image_viewer_geometry"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("history_map.preview_title"))
        self.setModal(False)
        self.resize(720, 560)
        self._node_key: NodeKey | None = None
        self._nav: dict[str, NodeKey | None] = {}
        self._restored = _restore_saved_geometry(self, self._GEOMETRY_KEY)
        self._raise_on_next_show = True
        # 初回表示の白いちらつき防止（showEvent でフェードイン）
        self.setWindowOpacity(0.0)

        self.setStyleSheet(f"QDialog {{ background: {SURFACE0}; color: {TEXT}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = QLabel(self)
        self._title.setStyleSheet(f"color: {TEXT}; font-weight: bold;")
        header.addWidget(self._title, stretch=1)

        btn_style = themed_button_style("neutral")
        self._apply_btn = QPushButton(tr("history_map.preview_apply_btn"))
        self._apply_btn.setStyleSheet(themed_button_style("accent"))
        self._apply_btn.clicked.connect(self._emit_jump)
        header.addWidget(self._apply_btn)

        self._fit_btn = QPushButton(tr("history_map.preview_fit_btn"))
        self._fit_btn.setStyleSheet(btn_style)
        self._fit_btn.clicked.connect(lambda: self._view.fit_image())
        header.addWidget(self._fit_btn)

        self._actual_btn = QPushButton(tr("history_map.preview_actual_btn"))
        self._actual_btn.setStyleSheet(btn_style)
        self._actual_btn.clicked.connect(lambda: self._view.original_size())
        header.addWidget(self._actual_btn)

        self._close_btn = QPushButton(tr("history_map.preview_close_btn"))
        self._close_btn.setStyleSheet(btn_style)
        self._close_btn.clicked.connect(self.close)
        header.addWidget(self._close_btn)
        root.addLayout(header)

        self._zoom_label = QLabel("", self)
        self._zoom_label.setStyleSheet(f"color: {SUBTEXT};")
        root.addWidget(self._zoom_label)

        nav_grid = QGridLayout()
        nav_grid.setContentsMargins(0, 0, 0, 0)
        nav_grid.setHorizontalSpacing(4)
        nav_grid.setVerticalSpacing(4)
        self._view = _ImagePreviewView(self)
        self._view.zoom_changed.connect(self._update_zoom_label)

        self._nav_buttons: dict[str, QToolButton] = {}
        for direction, text, row, col in (
            ("parent", "▲", 0, 1),
            ("prev", "◀", 1, 0),
            ("next", "▶", 1, 2),
            ("child", "▼", 2, 1),
        ):
            btn = QToolButton()
            btn.setText(text)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                f"QToolButton {{ color: {ACCENT}; background: {SURFACE1}; "
                f"border: 1px solid {SUBTEXT}; border-radius: 3px; }}"
                f"QToolButton:disabled {{ color: transparent; background: {SURFACE0}; "
                f"border: 1px solid transparent; }}"
            )
            btn.clicked.connect(lambda _=False, d=direction: self._navigate(d))
            self._nav_buttons[direction] = btn
            nav_grid.addWidget(btn, row, col, Qt.AlignmentFlag.AlignCenter)
        nav_grid.addWidget(self._view, 1, 1)
        nav_grid.setRowStretch(1, 1)
        nav_grid.setColumnStretch(1, 1)
        root.addLayout(nav_grid, stretch=1)

    def set_node(
        self,
        header: str,
        pix: QPixmap,
        node_key: NodeKey,
        nav: dict[str, NodeKey | None],
    ) -> None:
        self._node_key = node_key
        self._nav = nav
        self._title.setText(header)
        self.setWindowTitle(header)
        self._view.set_pixmap(pix)
        self._apply_nav_state()

    def set_background_color(self, color: str) -> None:
        """画像エリアの背景色を履歴（マップ）の背景色に合わせる。"""
        self._view.set_background_color(color)

    def update_image(self, node_key: NodeKey, pix: QPixmap) -> None:
        """表示中のノードが一致する場合だけ画像を差し替える（非同期フル画像用）。

        ノードを切り替えた後に古いリクエストが返ってきても、現在のノードと一致
        しなければ無視するので誤差し替えしない。
        """
        if self._node_key == node_key and pix is not None and not pix.isNull():
            self._view.set_pixmap(pix)

    def clear_image(self) -> None:
        self._node_key = None
        self._nav = {}
        self._title.setText(tr("history_map.preview_blank"))
        self.setWindowTitle(tr("history_map.preview_blank"))
        self._view.set_pixmap(QPixmap())
        self._apply_nav_state()
        self._apply_btn.setEnabled(False)

    def _apply_nav_state(self) -> None:
        for direction, btn in self._nav_buttons.items():
            btn.setEnabled(self._nav.get(direction) is not None)
        self._apply_btn.setEnabled(self._node_key is not None)

    def _navigate(self, direction: str) -> None:
        target = self._nav.get(direction)
        if target is None:
            return
        self.node_requested.emit(*target)

    def _emit_jump(self) -> None:
        if self._node_key is not None:
            self.jump_requested.emit(*self._node_key)

    def _update_zoom_label(self, zoom: float, mode: str) -> None:
        key = {
            "fit": "history_map.preview_zoom_fit",
            "original": "history_map.preview_zoom_original",
            "manual": "history_map.preview_zoom_manual",
        }.get(mode, "history_map.preview_zoom_manual")
        self._zoom_label.setText(f"{tr(key)} {round(zoom * 100)}%")

    def set_raise_on_next_show(self, enabled: bool) -> None:
        self._raise_on_next_show = bool(enabled)

    def set_show_without_activating(self, enabled: bool) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, bool(enabled))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _reveal_first_show(self)
        if not self._restored:
            self.reset_window_geometry()
            self._restored = True
        else:
            _clamp_widget_to_visible_screen(self)
        if self._raise_on_next_show:
            self.raise_()
        self._raise_on_next_show = True

    def closeEvent(self, event) -> None:
        _save_geometry(self, self._GEOMETRY_KEY)
        super().closeEvent(event)

    def reset_window_geometry(self) -> None:
        avail = _available_rect_for(self)
        if avail is None:
            self.resize(720, 560)
            return
        w = min(760, max(560, int(avail.width() * 0.45)))
        h = min(620, max(420, int(avail.height() * 0.55)))
        self.resize(w, h)
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())


class HistoryMapDialog(QDialog):
    node_clicked = Signal(str, int)              # 左クリック
    jump_requested = Signal(str, int)            # 現在位置に設定
    preview_requested = Signal(str, int)         # 画像ウィンドウ表示
    stack_requested = Signal(str, int)
    color_requested = Signal(str, int)
    text_color_requested = Signal(str, int)      # 履歴の文字色（ツリー単位）
    line_color_requested = Signal(str, int)      # 履歴の系統線色（ツリー単位）
    edit_requested = Signal(str, int)            # 編集（履歴の編集ダイアログ）
    show_subtree_requested = Signal(str, int)    # ここ以下のみ表示
    show_full_requested = Signal()               # 全体表示
    detach_requested = Signal(str, int)          # 別系統にして移動
    erase_requested = Signal(str, int)           # 消去（マップからのみ）
    delete_requested = Signal(str, int)          # 削除（ゴミ箱行き）
    bulk_erase_requested = Signal(list)
    bulk_delete_requested = Signal(list)
    reparent_requested = Signal(str, int, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("history_map.title"))
        self.setModal(False)
        self.resize(760, 520)
        self._apply_default_geometry()
        # 初回表示の白いちらつき防止（showEvent でフェードイン）
        self.setWindowOpacity(0.0)
        self._current_item: _NodeItem | None = None
        self._opened_item: _NodeItem | None = None
        self._image_viewer: _HistoryImageViewerDialog | None = None
        self._raise_on_next_show = True
        # 現在地の回転発光（表示中のみタイマー駆動）
        self._current_glow: _CurrentGlowItem | None = None
        self._glow_clock = QElapsedTimer()
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(33)  # 約30fps
        self._glow_timer.timeout.connect(self._on_glow_tick)

        self._scene = _HistoryMapScene(self)
        self._scene.setBackgroundBrush(QBrush(QColor(SURFACE0)))
        self._view = _HistoryMapView(self._scene)
        self._view.node_clicked.connect(self.node_clicked.emit)
        self._view.preview_requested.connect(self.preview_requested.emit)
        self._view.apply_requested.connect(self.jump_requested.emit)
        self._view.stack_requested.connect(self.stack_requested.emit)
        self._view.color_requested.connect(self.color_requested.emit)
        self._view.text_color_requested.connect(self.text_color_requested.emit)
        self._view.line_color_requested.connect(self.line_color_requested.emit)
        self._view.edit_requested.connect(self.edit_requested.emit)
        self._view.show_subtree_requested.connect(self.show_subtree_requested.emit)
        self._view.show_full_requested.connect(self.show_full_requested.emit)
        self._view.detach_requested.connect(self.detach_requested.emit)
        self._view.erase_requested.connect(self.erase_requested.emit)
        self._view.delete_requested.connect(self.delete_requested.emit)
        self._view.bulk_erase_requested.connect(self.bulk_erase_requested.emit)
        self._view.bulk_delete_requested.connect(self.bulk_delete_requested.emit)
        self._view.reparent_requested.connect(self.reparent_requested.emit)
        self._view._is_enlarged = True  # 拡大ダイアログ: 空白メニューにフィット/全体表示も出す
        self._view.center_current_requested.connect(self.scroll_to_current_animated)
        self._view.setRenderHints(
            self._view.renderHints() | QPainter.RenderHint.Antialiasing
        )
        self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)

        # ── ズームバー（⛶全体フィット / 1:1 / 倍率表示）──────
        zoom_bar = QHBoxLayout()
        zoom_bar.setContentsMargins(0, 0, 0, 4)
        zoom_bar.setSpacing(4)
        _zoom_btn_ss = (
            "QToolButton { background: transparent; color: " + SUBTEXT + "; "
            "border: 1px solid " + SUBTEXT + "; border-radius: 3px; padding: 0; "
            + EMOJI_ICON_SS + " }"
            "QToolButton:hover { background: " + SUBTEXT + "; color: " + SURFACE0 + "; }"
        )
        self._goto_current_btn = QToolButton()
        self._goto_current_btn.setText("📍")
        self._goto_current_btn.setFixedSize(28, 28)
        self._goto_current_btn.setToolTip(tr("history_map.goto_current_tooltip"))
        self._goto_current_btn.setStyleSheet(_zoom_btn_ss)
        self._goto_current_btn.clicked.connect(self.center_on_current)
        zoom_bar.addWidget(self._goto_current_btn)

        self._zoom_fit_btn = QToolButton()
        self._zoom_fit_btn.setText("⛶")
        self._zoom_fit_btn.setFixedSize(28, 28)
        self._zoom_fit_btn.setToolTip(tr("history_map.zoom_fit_tooltip"))
        self._zoom_fit_btn.setStyleSheet(_zoom_btn_ss)
        self._zoom_fit_btn.clicked.connect(self._view.zoom_fit)
        zoom_bar.addWidget(self._zoom_fit_btn)

        self._zoom_out_btn = QToolButton()
        self._zoom_out_btn.setText("－")
        self._zoom_out_btn.setFixedSize(28, 28)
        self._zoom_out_btn.setToolTip(tr("history_map.zoom_out_tooltip"))
        self._zoom_out_btn.setStyleSheet(_zoom_btn_ss)
        self._zoom_out_btn.clicked.connect(self._view.zoom_out_step)
        zoom_bar.addWidget(self._zoom_out_btn)

        self._zoom_in_btn = QToolButton()
        self._zoom_in_btn.setText("＋")
        self._zoom_in_btn.setFixedSize(28, 28)
        self._zoom_in_btn.setToolTip(tr("history_map.zoom_in_tooltip"))
        self._zoom_in_btn.setStyleSheet(_zoom_btn_ss)
        self._zoom_in_btn.clicked.connect(self._view.zoom_in_step)
        zoom_bar.addWidget(self._zoom_in_btn)

        self._zoom_reset_btn = QToolButton()
        self._zoom_reset_btn.setText("1:1")
        self._zoom_reset_btn.setFixedSize(34, 28)
        self._zoom_reset_btn.setToolTip(tr("history_map.zoom_reset_tooltip"))
        self._zoom_reset_btn.setStyleSheet(_zoom_btn_ss)
        self._zoom_reset_btn.clicked.connect(self._reset_map_zoom_to_actual)
        zoom_bar.addWidget(self._zoom_reset_btn)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet(f"color: {SUBTEXT};")
        zoom_bar.addWidget(self._zoom_label)
        self._view.zoom_changed.connect(
            lambda z: self._zoom_label.setText(f"{round(z * 100)}%")
        )
        zoom_bar.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addLayout(zoom_bar)
        root.addWidget(self._view)

    def reset_zoom(self) -> None:
        """倍率を100%に戻す（マップを開き直したときの初期化用）。"""
        self._view.set_zoom(1.0)

    def map_view_state(self) -> dict[str, float | int]:
        return {
            "zoom": float(self._view.zoom()),
            "hscroll": int(self._view.horizontalScrollBar().value()),
            "vscroll": int(self._view.verticalScrollBar().value()),
        }

    def restore_map_view_state(
        self,
        *,
        zoom: float | None = None,
        hscroll: int | None = None,
        vscroll: int | None = None,
    ) -> None:
        if zoom is not None:
            self._view.set_zoom(float(zoom))

        def _restore_scroll() -> None:
            if hscroll is not None:
                self._view.horizontalScrollBar().setValue(int(hscroll))
            if vscroll is not None:
                self._view.verticalScrollBar().setValue(int(vscroll))

        QTimer.singleShot(0, _restore_scroll)

    def _reset_map_zoom_to_actual(self) -> None:
        self._view.set_zoom(1.0)
        self.center_on_current()

    def rebuild(
        self,
        nodes: list[HistoryMapNode],
        current_node: NodeKey | None,
        opened_node: NodeKey | None = None,
    ) -> None:
        """状態遷移: nodes → レイアウト座標 → ノードアイテム / 座標 → 接続線パス。"""
        self._current_item = None
        self._opened_item = None
        self._current_glow = None  # 旧シーンと一緒に破棄される
        self._items_by_key: dict[NodeKey, _NodeItem] = {}
        self._view._current_node_key = current_node  # 空白部メニューの色変更対象
        self._view.reset_selection()
        self._scene.clear()
        self._scene.set_edge_path(QPainterPath())
        if not nodes:
            label = QGraphicsSimpleTextItem(tr("history_map.empty"))
            label.setBrush(QBrush(QColor(SUBTEXT)))
            label.setPos(24, 24)
            self._scene.addItem(label)
            self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
            return

        by_key: dict[NodeKey, HistoryMapNode] = {node.key: node for node in nodes}
        depths: dict[NodeKey, int] = {}

        def depth_for(key: NodeKey, seen: set[NodeKey] | None = None) -> int:
            if key in depths:
                return depths[key]
            seen = set() if seen is None else seen
            if key in seen:
                depths[key] = 0
                return 0
            seen.add(key)
            node = by_key[key]
            pk = node.parent_key
            if pk is None or pk not in by_key:
                depths[key] = 0
            else:
                depths[key] = depth_for(pk, seen) + 1
            return depths[key]

        for node in nodes:
            depth_for(node.key)

        x_spacing = 132
        y_spacing = 138
        positions: dict[NodeKey, tuple[float, float]] = {}
        items: dict[NodeKey, _NodeItem] = {}
        siblings_by_parent: dict[NodeKey | None, list[HistoryMapNode]] = {}
        for node in nodes:
            siblings_by_parent.setdefault(node.parent_key, []).append(node)
        for siblings in siblings_by_parent.values():
            siblings.sort(key=lambda n: (n.created_at or "", n.history_id), reverse=True)

        def sort_newest_first(lst: list[HistoryMapNode]) -> list[HistoryMapNode]:
            return sorted(lst, key=lambda n: (n.created_at or "", n.history_id), reverse=True)

        # 横位置(列)は Reingold–Tilford の輪郭(contour)法で決める。
        # naive な「部分木の葉数ぶん列を予約」方式だと、深い子孫(孫・ひ孫)の幅まで
        # 上位へ伝播し、段(深さ)が違って本来ぶつからない叔父まで横へ押しのけてしまう。
        # 輪郭法は「隣り合う部分木の輪郭が当たらない最小距離」だけ離すので、重なりを
        # 出さずに横幅を最小化する＝直接の子のためには避けるが、孫以降は実際に同じ段で
        # 衝突する時だけ避ける。
        SIBLING_GAP = 1.0  # 隣接部分木の最小間隔(列単位。後段で *x_spacing)
        column_by_key: dict[NodeKey, float] = {}
        placed: set[NodeKey] = set()

        def children_of(node: HistoryMapNode) -> list[HistoryMapNode]:
            return sort_newest_first(siblings_by_parent.get(node.key, []))

        def shift_subtree(node: HistoryMapNode, dx: float) -> None:
            column_by_key[node.key] = column_by_key.get(node.key, 0.0) + dx
            for child in children_of(node):
                shift_subtree(child, dx)

        def aug_right(c: list[float]) -> list[float]:
            """右輪郭に「1段だけの影」を付けた包絡線。各ノードは自分の真下1段の
            スロットも占有する(= max(自段, 1段上))。末尾に葉の1段下の影を1段足す。
            2段以上下は占有しない(他系統の潜り込みを許す)。"""
            if not c:
                return c
            return [c[0]] + [max(c[i], c[i - 1]) for i in range(1, len(c))] + [c[-1]]

        def aug_left(c: list[float]) -> list[float]:
            """左輪郭の1段影版(= min(自段, 1段上))。末尾に葉の1段下の影を足す。"""
            if not c:
                return c
            return [c[0]] + [min(c[i], c[i - 1]) for i in range(1, len(c))] + [c[-1]]

        def place_forest(
            sibs: list[HistoryMapNode],
        ) -> tuple[list[float], list[float], list[float]]:
            """兄弟(または複数root)を左→右に、輪郭が当たらない最小距離で配置。
            戻り値: (各兄弟rootの列, ブロック左輪郭, ブロック右輪郭)。輪郭は段(相対深さ)
            ごとの最左/最右 の「実ノード」列値リスト(真のシルエット)。

            分離判定は「1段だけの影」付き包絡線(aug_right/aug_left)で行う。これにより
            あるノードの真下1段に別系統が来ない(=直系と見間違えない)。一方、2段以上
            下は譲るので、深い子孫どうしは横で交差してよく、横幅の無駄な拡大を防ぐ。"""
            xs: list[float] = []
            lc_block: list[float] = []  # ブロック左シルエット(実ノード・最左)
            rc_block: list[float] = []  # ブロック右シルエット(実ノード・最右)
            for sib in sibs:
                clc, crc = place_node(sib)
                if rc_block:
                    ar = aug_right(rc_block)  # 既存ブロック右(影1段)
                    al = aug_left(clc)        # 追加sib左(影1段)
                    n = min(len(ar), len(al))
                    shift = max(0.0, max(ar[i] + SIBLING_GAP - al[i] for i in range(n)))
                    if shift:
                        shift_subtree(sib, shift)
                        clc = [v + shift for v in clc]
                        crc = [v + shift for v in crc]
                xs.append(column_by_key[sib.key])
                # 真のシルエット更新(影は判定時のみ。ここでは実ノード列のみ保持)
                for i in range(len(crc)):  # 右端: 最右(直近)sibが到達段を支配
                    if i < len(rc_block):
                        rc_block[i] = crc[i]
                    else:
                        rc_block.append(crc[i])
                for i in range(len(clc)):  # 左端: 先頭(最左)sibが浅い段を支配。深い段だけ延長
                    if i >= len(lc_block):
                        lc_block.append(clc[i])
            return xs, lc_block, rc_block

        def place_node(node: HistoryMapNode) -> tuple[list[float], list[float]]:
            """部分木を配置し column_by_key を埋める。戻り値は (左輪郭, 右輪郭)
            (相対深さ 0 = この node)。"""
            if node.key in placed:  # データ不整合(循環)での無限再帰を防ぐ
                return [column_by_key.get(node.key, 0.0)], [column_by_key.get(node.key, 0.0)]
            placed.add(node.key)
            children = children_of(node)
            if not children:
                column_by_key[node.key] = 0.0
                return [0.0], [0.0]
            xs, lc_block, rc_block = place_forest(children)
            mid = (xs[0] + xs[-1]) / 2.0  # 子の両端の中央に親を置く
            column_by_key[node.key] = mid
            return [mid] + lc_block, [mid] + rc_block

        roots = sort_newest_first([
            node for node in nodes
            if node.parent_key is None or node.parent_key not in by_key
        ])
        place_forest(roots)

        node_text_color = getattr(self._scene, "_node_text_color", TEXT)
        for node in nodes:
            depth = depths.get(node.key, 0)
            x = column_by_key.get(node.key, 0.0) * x_spacing
            y = depth * y_spacing
            item = _NodeItem(
                node,
                is_current=(node.key == current_node),
                is_opened_target=(node.key == opened_node and node.key != current_node),
                is_root=(node.parent_key is None or node.parent_key not in by_key),
                text_color=node_text_color,
            )
            item.setPos(x, y)
            self._scene.addItem(item)
            positions[node.key] = (x, y)
            items[node.key] = item
            if node.key == current_node:
                self._current_item = item
                # 現在地は常時明滅（枠を回る発光、2秒で一回転）
                self._current_glow = _CurrentGlowItem(item.rect(), item)
            if node.key == opened_node and node.key != current_node:
                self._opened_item = item

        # 接続線: ノード座標から導出してシーンの paint-only レイヤーへ渡す。
        # アイテムとして追加しない（操作対象はノードのみ。線はクリック等で消えない）
        edge_path = QPainterPath()
        for node in nodes:
            pk = node.parent_key
            if pk not in positions or node.key not in positions:
                continue
            child_x, child_y = positions[node.key]
            parent_x, parent_y = positions[pk]
            start_x = parent_x + NODE_W / 2
            start_y = parent_y + NODE_H
            end_x = child_x + NODE_W / 2
            end_y = child_y
            mid_y = (start_y + end_y) / 2
            edge_path.moveTo(start_x, start_y)
            edge_path.cubicTo(start_x, mid_y, end_x, mid_y, end_x, end_y)
        self._scene.set_edge_path(edge_path)

        self._items_by_key = items
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        # ここでは自動スクロールしない: マップを開いたまま生成が完了すると
        # rebuild が走るが、その都度 current へスクロールするのは UX を損なう。
        # スクロールはマップを開いた時のみ（showEvent / scroll_to_current）。

    def set_view_restricted(self, restricted: bool) -> None:
        """「ここ以下のみ表示」中かどうか（コンテキストメニューの全体表示の有効化に使う）。"""
        self._view.set_view_restricted(restricted)

    def set_active_history_name(self, name: str) -> None:
        """アクティブ履歴名（コンテキストメニューの編集の有効判定に使う）。"""
        self._view.set_active_history_name(name)

    def set_history_background_color(self, color: str) -> None:
        bg = QColor(color)
        if bg.isValid():
            self._scene.setBackgroundBrush(QBrush(bg))

    def set_history_text_color(self, color: str) -> None:
        self._scene.set_node_text_color(color)

    def set_history_line_color(self, color: str) -> None:
        self._scene.set_edge_color(color)

    def show_node_preview(
        self,
        header: str,
        pix: QPixmap,
        node_key: NodeKey,
        nav: dict[str, NodeKey | None] | None = None,
        activate: bool = True,
    ) -> None:
        """閉じない画像ビューアを表示し、履歴マップの選択と連動させる。"""
        if self._image_viewer is None:
            self._image_viewer = _HistoryImageViewerDialog(self)
            self._image_viewer.jump_requested.connect(self.jump_requested.emit)
            self._image_viewer.node_requested.connect(self.preview_requested.emit)
        self._image_viewer.set_node(header, pix, node_key, nav or {})
        self._image_viewer.set_raise_on_next_show(activate)
        self._image_viewer.set_show_without_activating(not activate)
        self._image_viewer.show()
        if activate:
            self._image_viewer.raise_()
        else:
            self._image_viewer.set_show_without_activating(False)

    def scroll_to_current(self) -> None:
        """表示開始時の対象（なければ現在地）をビュー中央に表示する。"""
        item = self._opened_item or self._current_item
        if item is None:
            return
        try:
            self._view.centerOn(item)
        except RuntimeError:
            self._current_item = None

    def center_on_current(self) -> None:
        """📍ボタン: 現在地ノードへ直行（ビュー中央に表示）する。"""
        item = self._current_item
        if item is None:
            return
        try:
            self._view.centerOn(item)
        except RuntimeError:
            self._current_item = None

    def scroll_to_current_animated(self) -> None:
        """現在地ノードへ滑らかにスクロールする（急なジャンプを避ける）。"""
        item = self._current_item
        if item is None:
            return
        try:
            c = item.sceneBoundingRect().center()
        except RuntimeError:
            self._current_item = None
            return
        self._view.animate_center_on(c.x(), c.y())

    def play_move_animation(
        self, old_key: NodeKey | None, new_key: NodeKey | None
    ) -> None:
        """
        現在地の移動を可視化する: 青枠が旧ノードから移動先ノードへ、
        ノードの後ろ（z<0）を滑っていく。rebuild 後に呼ぶこと（新レイアウト基準）。
        """
        if old_key is None or new_key is None or old_key == new_key:
            return
        items = getattr(self, "_items_by_key", {})
        src = items.get(old_key)
        dst = items.get(new_key)
        if src is None or dst is None:
            return
        try:
            start, end = src.pos(), dst.pos()
        except RuntimeError:
            return

        m = 4
        frame = QGraphicsRectItem(QRectF(-m, -m, NODE_W + 2 * m, NODE_H + 2 * m))
        frame.setPen(QPen(QColor(ACCENT), 3))
        frame.setBrush(Qt.BrushStyle.NoBrush)
        frame.setZValue(-0.5)  # ノードの後ろ・接続線（背景）より上
        frame.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        frame.setPos(start)
        self._scene.addItem(frame)

        anim = QVariantAnimation(self)
        anim.setDuration(500)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        def _on_value(v) -> None:
            try:
                frame.setPos(v)
            except RuntimeError:
                anim.stop()  # rebuild でフレームが破棄された場合

        def _on_done() -> None:
            try:
                if frame.scene() is not None:
                    self._scene.removeItem(frame)
            except RuntimeError:
                pass

        anim.valueChanged.connect(_on_value)
        anim.finished.connect(_on_done)
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_glow_tick(self) -> None:
        """現在地の回転発光を進める（GLOW_PERIOD_MS で一回転）。"""
        glow = self._current_glow
        if glow is None:
            return
        angle = (self._glow_clock.elapsed() % GLOW_PERIOD_MS) / GLOW_PERIOD_MS * 360.0
        try:
            glow.set_angle(angle)
        except RuntimeError:
            self._current_glow = None

    def set_raise_on_next_show(self, enabled: bool) -> None:
        self._raise_on_next_show = bool(enabled)

    def set_show_without_activating(self, enabled: bool) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, bool(enabled))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _reveal_first_show(self)
        self.setMaximumWidth(16777215)
        _clamp_widget_to_visible_screen(self)
        if self._raise_on_next_show:
            self.raise_()
        self._raise_on_next_show = True
        # 現在地の回転発光は表示中のみ駆動（非表示時はCPUを使わない）
        self._glow_clock.start()
        self._glow_timer.start()
        # 表示時はレイアウト確定後にスクロールさせる
        QTimer.singleShot(0, self.scroll_to_current)

    def hideEvent(self, event) -> None:
        _save_geometry(self, "history_map_geometry")
        self._glow_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if hasattr(self, "_view"):
                self._view.cancel_drag()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        _save_geometry(self, "history_map_geometry")
        super().closeEvent(event)

    def restore_saved_geometry(self) -> None:
        if not _restore_saved_geometry(self, "history_map_geometry"):
            self._apply_default_geometry()

    def reset_window_geometry(self) -> None:
        _write_app_setting("history_map_geometry", "")
        self._apply_default_geometry()

    def _apply_default_geometry(self) -> None:
        avail = _available_rect_for(self)
        if avail is not None:
            self.resize(max(320, avail.width() // 2), max(280, avail.height() // 2))
            frame = self.frameGeometry()
            frame.moveCenter(avail.center())
            self.move(frame.topLeft())
        else:
            self.resize(760, 520)

    def reset_image_viewer_geometry(self) -> None:
        _write_app_setting("history_image_viewer_geometry", "")
        if self._image_viewer is not None:
            self._image_viewer.reset_window_geometry()

    def clear_node_preview(self) -> None:
        if self._image_viewer is not None and self._image_viewer.isVisible():
            self._image_viewer.clear_image()

    def update_node_image(self, node_key: NodeKey, pix: QPixmap) -> None:
        """非同期取得したフル画像で、ビューアが同じノードを表示中なら差し替える。"""
        if self._image_viewer is not None and self._image_viewer.isVisible():
            self._image_viewer.update_image(node_key, pix)

    def image_viewer_state(self) -> tuple[bool, NodeKey | None]:
        if self._image_viewer is None or not self._image_viewer.isVisible():
            return False, None
        _save_geometry(self._image_viewer, "history_image_viewer_geometry")
        return True, self._image_viewer._node_key


class HistoryMapPanel(QWidget):
    """中央ペインに埋め込む親子マップ。履歴マップと同じビュー実装を使う。"""

    jump_requested = Signal(str, int)
    node_clicked = Signal(str, int)
    preview_requested = Signal(str, int)
    stack_requested = Signal(str, int)
    color_requested = Signal(str, int)
    text_color_requested = Signal(str, int)      # 履歴の文字色（ツリー単位）
    line_color_requested = Signal(str, int)      # 履歴の系統線色（ツリー単位）
    edit_requested = Signal(str, int)
    show_subtree_requested = Signal(str, int)
    show_full_requested = Signal()
    detach_requested = Signal(str, int)
    erase_requested = Signal(str, int)
    delete_requested = Signal(str, int)
    bulk_erase_requested = Signal(list)
    bulk_delete_requested = Signal(list)
    reparent_requested = Signal(str, int, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("parentChildMap")
        self._current_item: _NodeItem | None = None
        self._opened_item: _NodeItem | None = None
        self._current_glow: _CurrentGlowItem | None = None
        self._items_by_key: dict[NodeKey, _NodeItem] = {}
        self._image_viewer: _HistoryImageViewerDialog | None = None
        self._restoring_view = False
        self._restore_when_shown = False
        # 表示したい中心(シーン座標)。rebuild でスクロールが左上に戻らないよう
        # 再適用して位置を保つための値。ユーザーのパン、初回の現在地センタリング、
        # 現在地への追従アニメ完了時に更新される。ズームは別途記憶し終了時に永続化。
        self._desired_center: tuple[float, float] | None = None
        self._desired_zoom: float | None = None
        # 現在地への追従スクロールをアニメ中かどうか。アニメ中は rebuild の
        # 位置再適用（_apply_desired_center）と保存（_save_view_state）を抑止する。
        self._animating_center = False
        self._expanded_height = 260
        self._collapsed = _read_app_setting("parent_child_map_collapsed", "0") == "1"

        self._glow_clock = QElapsedTimer()
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(33)
        self._glow_timer.timeout.connect(self._on_glow_tick)

        self._scene = _HistoryMapScene(self)
        self._scene.setBackgroundBrush(QBrush(QColor(SURFACE0)))
        self._view = _HistoryMapView(self._scene)
        self._view.node_clicked.connect(self.node_clicked.emit)
        self._view.preview_requested.connect(self.preview_requested.emit)
        self._view.apply_requested.connect(self.jump_requested.emit)
        self._view.stack_requested.connect(self.stack_requested.emit)
        self._view.color_requested.connect(self.color_requested.emit)
        self._view.text_color_requested.connect(self.text_color_requested.emit)
        self._view.line_color_requested.connect(self.line_color_requested.emit)
        self._view.edit_requested.connect(self.edit_requested.emit)
        self._view.show_subtree_requested.connect(self.show_subtree_requested.emit)
        self._view.show_full_requested.connect(self.show_full_requested.emit)
        self._view.detach_requested.connect(self.detach_requested.emit)
        self._view.erase_requested.connect(self.erase_requested.emit)
        self._view.delete_requested.connect(self.delete_requested.emit)
        self._view.bulk_erase_requested.connect(self.bulk_erase_requested.emit)
        self._view.bulk_delete_requested.connect(self.bulk_delete_requested.emit)
        self._view.reparent_requested.connect(self.reparent_requested.emit)
        self._view.center_current_requested.connect(self.center_on_current)
        self._view.setRenderHints(self._view.renderHints() | QPainter.RenderHint.Antialiasing)
        self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self._view.zoom_changed.connect(lambda *_: self._save_view_state())
        self._view.horizontalScrollBar().valueChanged.connect(lambda *_: self._save_view_state())
        self._view.verticalScrollBar().valueChanged.connect(lambda *_: self._save_view_state())

        btn_ss = (
            "QToolButton { background: transparent; color: " + SUBTEXT + "; "
            "border: 1px solid " + SUBTEXT + "; border-radius: 3px; padding: 0; "
            + EMOJI_ICON_SS + " }"
            "QToolButton:hover { background: " + SUBTEXT + "; color: " + SURFACE0 + "; }"
        )
        self._toggle_btn = QToolButton()
        self._toggle_btn.setFixedSize(22, 22)
        self._toggle_btn.setStyleSheet(btn_ss)
        self._toggle_btn.clicked.connect(lambda: self._set_collapsed(not self._collapsed))
        title = QLabel(tr("editor.parent_child_map_title"))
        title.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")

        self._goto_current_btn = QToolButton()
        self._goto_current_btn.setText("📍")
        self._goto_current_btn.setFixedSize(28, 28)
        self._goto_current_btn.setToolTip(tr("history_map.goto_current_tooltip"))
        self._goto_current_btn.setStyleSheet(btn_ss)
        self._goto_current_btn.clicked.connect(self.center_on_current)

        self._zoom_fit_btn = QToolButton()
        self._zoom_fit_btn.setText("⛶")
        self._zoom_fit_btn.setFixedSize(28, 28)
        self._zoom_fit_btn.setToolTip(tr("history_map.zoom_fit_tooltip"))
        self._zoom_fit_btn.setStyleSheet(btn_ss)
        self._zoom_fit_btn.clicked.connect(self._view.zoom_fit)

        self._zoom_out_btn = QToolButton()
        self._zoom_out_btn.setText("－")
        self._zoom_out_btn.setFixedSize(28, 28)
        self._zoom_out_btn.setToolTip(tr("history_map.zoom_out_tooltip"))
        self._zoom_out_btn.setStyleSheet(btn_ss)
        self._zoom_out_btn.clicked.connect(self._view.zoom_out_step)

        self._zoom_in_btn = QToolButton()
        self._zoom_in_btn.setText("＋")
        self._zoom_in_btn.setFixedSize(28, 28)
        self._zoom_in_btn.setToolTip(tr("history_map.zoom_in_tooltip"))
        self._zoom_in_btn.setStyleSheet(btn_ss)
        self._zoom_in_btn.clicked.connect(self._view.zoom_in_step)

        self._zoom_reset_btn = QToolButton()
        self._zoom_reset_btn.setText("1:1")
        self._zoom_reset_btn.setFixedSize(34, 28)
        self._zoom_reset_btn.setToolTip(tr("history_map.zoom_reset_tooltip"))
        self._zoom_reset_btn.setStyleSheet(btn_ss)
        self._zoom_reset_btn.clicked.connect(self._reset_map_zoom_to_actual)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet(f"color: {SUBTEXT};")
        self._view.zoom_changed.connect(lambda z: self._zoom_label.setText(f"{round(z * 100)}%"))

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 2)
        bar.setSpacing(4)
        bar.addWidget(self._toggle_btn)
        bar.addWidget(title)
        bar.addStretch()
        for w in (
            self._goto_current_btn,
            self._zoom_fit_btn,
            self._zoom_out_btn,
            self._zoom_in_btn,
            self._zoom_reset_btn,
            self._zoom_label,
        ):
            bar.addWidget(w)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        root.addLayout(bar)
        root.addWidget(self._view)
        self.setFixedHeight(self._expanded_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"QWidget#parentChildMap {{ background: {SURFACE1}; border: 1px solid {SURFACE2}; border-radius: 4px; }}"
            f"QGraphicsView {{ background: {SURFACE0}; border-top: 1px solid {SURFACE2}; border-left: none; border-right: none; border-bottom: none; }}"
        )
        self._collapsible_widgets = [
            self._goto_current_btn,
            self._zoom_fit_btn,
            self._zoom_out_btn,
            self._zoom_in_btn,
            self._zoom_reset_btn,
            self._zoom_label,
            self._view,
        ]
        self._set_collapsed(self._collapsed, persist=False)

        self._glow_clock.start()
        self._glow_timer.start()

    def _set_collapsed(self, collapsed: bool, *, persist: bool = True) -> None:
        self._collapsed = bool(collapsed)
        self._toggle_btn.setText("▶" if self._collapsed else "▼")
        for widget in getattr(self, "_collapsible_widgets", []):
            widget.setVisible(not self._collapsed)
        if self._collapsed:
            self.setFixedHeight(34)
        else:
            self.setFixedHeight(self._expanded_height)
        if persist:
            _write_app_setting("parent_child_map_collapsed", "1" if self._collapsed else "0")

    def rebuild(
        self,
        nodes: list[HistoryMapNode],
        current_node: NodeKey | None,
        opened_node: NodeKey | None = None,
    ) -> None:
        # rebuild() は scene.clear() と setSceneRect() を行うため、ビューの
        # スクロールバーが valueChanged を発火し、ズームも再クランプされうる。
        # これらはユーザーのパン/ズームではなくプログラム由来の副作用なので、
        # 再構築中はビュー状態の保存を抑止する。抑止しないと rebuild のたび
        # （＝生成のたび）に保存済みのズーム/中心が上書きされ、次回起動時に
        # 「前回ユーザーが見ていた位置」ではなく上書き後の位置を復元してしまう。
        prev_restoring = self._restoring_view
        self._restoring_view = True
        try:
            HistoryMapDialog.rebuild(self, nodes, current_node, opened_node)
            # 初回（起動直後など、まだ表示位置が未設定）は現在地ノードを中央に。
            # 以降は現在地への移動は外部からの scroll_to_current_animated() で行い、
            # rebuild 自体はスクロールを左上に戻さないよう位置を保つだけにする。
            if self._desired_center is None and self._current_item is not None:
                c = self._current_item.sceneBoundingRect().center()
                self._desired_center = (c.x(), c.y())
            # scene.clear()+setSceneRect() でスクロールが左上に戻るため、直後に
            # 目標中心（前回の表示位置）へ復帰させる。アニメ追従中は触らない。
            self._apply_desired_center()
        finally:
            self._restoring_view = prev_restoring

    def set_view_restricted(self, restricted: bool) -> None:
        self._view.set_view_restricted(restricted)

    def set_active_history_name(self, name: str) -> None:
        self._view.set_active_history_name(name)

    def set_history_background_color(self, color: str) -> None:
        bg = QColor(color)
        if bg.isValid():
            self._scene.setBackgroundBrush(QBrush(bg))

    def set_history_text_color(self, color: str) -> None:
        self._scene.set_node_text_color(color)

    def set_history_line_color(self, color: str) -> None:
        self._scene.set_edge_color(color)

    def scroll_to_current(self) -> None:
        HistoryMapDialog.scroll_to_current(self)

    def center_on_current(self) -> None:
        HistoryMapDialog.center_on_current(self)
        self._save_view_state()

    def scroll_to_current_animated(self) -> None:
        """現在地ノードへ滑らかにスクロールして追従する。

        アニメ中は _apply_desired_center / _save_view_state を抑止し、完了時に
        目標（現在地）を _desired_center として確定する（以降の rebuild が左上に
        戻さず、現在地を保てる）。
        """
        item = self._current_item
        if item is None:
            return
        try:
            c = item.sceneBoundingRect().center()
        except RuntimeError:
            return
        target = (c.x(), c.y())

        def _on_done() -> None:
            self._animating_center = False
            self._desired_center = target

        self._animating_center = True
        self._view.animate_center_on(
            target[0], target[1],
            on_step=lambda: None,  # 各フレームの centerOn は _animating_center で保存抑止済み
            on_done=_on_done,
        )

    def update_node_image(self, node_key: NodeKey, pix: QPixmap) -> None:
        """非同期取得したフル画像で、ビューアが同じノードを表示中なら差し替える。"""
        if self._image_viewer is not None and self._image_viewer.isVisible():
            self._image_viewer.update_image(node_key, pix)

    def show_node_preview(
        self,
        header: str,
        pix: QPixmap,
        node_key: NodeKey,
        nav: dict[str, NodeKey | None] | None = None,
        activate: bool = True,
    ) -> None:
        if self._image_viewer is None:
            self._image_viewer = _HistoryImageViewerDialog(self)
            self._image_viewer.jump_requested.connect(self.jump_requested.emit)
            self._image_viewer.node_requested.connect(self.preview_requested.emit)
        self._image_viewer.set_node(header, pix, node_key, nav or {})
        self._image_viewer.set_raise_on_next_show(activate)
        self._image_viewer.set_show_without_activating(not activate)
        self._image_viewer.show()
        if activate:
            self._image_viewer.raise_()
        else:
            self._image_viewer.set_show_without_activating(False)

    def play_move_animation(
        self, old_key: NodeKey | None, new_key: NodeKey | None
    ) -> None:
        HistoryMapDialog.play_move_animation(self, old_key, new_key)

    def _reset_map_zoom_to_actual(self) -> None:
        self._view.set_zoom(1.0)
        self.center_on_current()

    def _on_glow_tick(self) -> None:
        HistoryMapDialog._on_glow_tick(self)

    def _restore_view_state(self) -> None:
        # 永続化するのはズームのみ。表示位置は現在地ノードへ追従させるため、
        # 直後の rebuild が現在地へセンタリングする（ここでは中心を復元しない）。
        self._restoring_view = True
        try:
            try:
                self._desired_zoom = float(_read_app_setting("parent_child_map_zoom", "1.0"))
            except Exception:
                self._desired_zoom = 1.0
            self._view.set_zoom(self._desired_zoom)
        finally:
            self._restoring_view = False

    def _apply_desired_center(self) -> None:
        """保存中心（シーン座標）へビューを合わせる。

        ビューの実サイズが未確定、またはシーンが空の間は何もしない。
        埋め込みマップはスプリッタ内にあり、起動直後の singleShot 復元時点では
        まだ実サイズが付いていないことがあるため、複数の自然なタイミング
        （rebuild 後・resizeEvent・showEvent）から繰り返し呼んで取りこぼしを防ぐ。
        centerOn はスクロールバーを動かし valueChanged 経由で保存を誘発するため、
        その間は _restoring_view で保存を抑止する。
        """
        if self._animating_center:
            return  # 現在地への追従アニメ中はアニメに任せる（割り込まない）
        center = self._desired_center
        if center is None:
            return
        vp = self._view.viewport()
        if vp.width() <= 1 or vp.height() <= 1:
            return
        if self._scene.sceneRect().isEmpty():
            return
        prev = self._restoring_view
        self._restoring_view = True
        try:
            self._view.centerOn(center[0], center[1])
        finally:
            self._restoring_view = prev

    def restore_saved_view_state(self) -> None:
        if not self.isVisible():
            self._restore_when_shown = True
            return
        self._restore_view_state()

    def save_view_state_now(self) -> None:
        """アプリ終了時に、ズームのみを一度だけ DB へ永続化する。

        表示位置は次回起動時に現在地ノードへ追従させるため保存しない（中心を
        valueChanged のたびに書くと生成ごとの rebuild で壊れる、という以前の問題も回避）。
        """
        zoom = self._desired_zoom if self._desired_zoom is not None else float(self._view.zoom())
        _write_app_setting("parent_child_map_zoom", str(zoom))

    def _save_view_state(self) -> None:
        # スクロール/ズーム変更(valueChanged / zoom_changed)で呼ばれる。
        # プログラム的変更中(_restoring_view)は無視。ここでは DB に書かず、
        # 「ユーザーが見たい領域」を in-memory に覚えるだけにする。永続化は終了時の
        # save_view_state_now() でのみ行う＝生成ごとの rebuild で保存値が壊れない。
        if self._restoring_view or self._animating_center:
            return
        center = self._view.mapToScene(self._view.viewport().rect().center())
        self._desired_center = (center.x(), center.y())
        self._desired_zoom = float(self._view.zoom())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # レイアウト確定でビューに実サイズが付くのはこのタイミング。起動直後の
        # 復元では centerOn が効かないため、ここで保存中心を再適用する。
        self._apply_desired_center()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._restore_when_shown:
            self._restore_when_shown = False
            self._restore_view_state()
        else:
            self._apply_desired_center()
