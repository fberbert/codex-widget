from __future__ import annotations

import math
import time
from decimal import Decimal
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip

from .chrome_cookies import ChromeCookieError
from .claude import ClaudeUsageError, load_usage
from .desktop import load_window_icon
from .cursor_usage import (
    DEFAULT_STATE_DB as DEFAULT_CURSOR_STATE_DB,
    CursorUsageError,
    load_usage as load_cursor_usage,
)
from .deepseek import (
    DEFAULT_BASE_URL as DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_STATE_FILE as DEFAULT_DEEPSEEK_STATE_FILE,
    DeepSeekUsageError,
    SpendTracker,
    fetch_balance,
    resolve_api_key,
)
from .kiro import (
    DEFAULT_BASE_URL as DEFAULT_KIRO_BASE_URL,
    DEFAULT_TOKEN_DB as DEFAULT_KIRO_TOKEN_DB,
    KiroUsageError,
    load_usage as load_kiro_usage,
)
from .kwin_place import KWinPlacer
from .layer_shell import (
    ANCHOR_LEFT,
    ANCHOR_RIGHT,
    ANCHOR_TOP,
    LayerShell,
    load_layer_shell,
    top_left_margins,
    top_right_margins,
)
from .mimo import (
    DEFAULT_BASE_URL as DEFAULT_MIMO_BASE_URL,
    MiMoUsageError,
    load_snapshot,
)
from .openai_costs import (
    DEFAULT_BASE_URL as DEFAULT_OPENAI_BASE_URL,
    OpenAICostsError,
    load_costs,
    resolve_admin_key,
)
from .usage import (
    CodexUsageError,
    fetch_usage_with_auth_refresh,
    parse_usage_payload,
)
from .ui_state import (
    DEFAULT_STATE_FILE as DEFAULT_UI_STATE_FILE,
    WidgetUiState,
    load_ui_state,
    save_ui_state,
)

from .view_model import (
    MetricCardModel,
    build_codex_card,
    build_claude_card,
    build_cursor_card,
    build_kiro_card,
    build_deepseek_card,
    build_mimo_card,
    build_openai_card,
)

MIN_SCALE = 0.45
MAX_SCALE = 1.8
SCALE_STEP = 0.1
THREAD_JOIN_TIMEOUT_MS = 25_000
CARD_SPACING = 12
DEFAULT_EDGE_MARGIN = 16
DEFAULT_TOP_MARGIN = 24
MAXIMUM_WIDGET_SIZE = 16_777_215
_BADGE_NAMES = {
    "Codex": "Codex",
    "Claude Code": "Claude",
    "Cursor": "Cursor",
    "Kiro": "Kiro",
    "DeepSeek": "DeepSeek",
    "MiMo": "MiMo",
}


def _clamp_scale(value: float) -> float:
    return max(MIN_SCALE, min(MAX_SCALE, round(float(value), 2)))


def _window_top_left(widget: QtWidgets.QWidget) -> QtCore.QPoint:
    window = widget.window()
    if isinstance(window, CodexUsageWidget):
        return window._origin()
    return window.frameGeometry().topLeft()


def _drag_window_to(widget: QtWidgets.QWidget, top_left: QtCore.QPoint) -> None:
    window = widget.window()
    if isinstance(window, CodexUsageWidget):
        window._user_moved = True
        window._place_at(top_left.x(), top_left.y())
        return
    window.move(top_left)


class CodexUsageWidget(QtWidgets.QWidget):
    """Frameless desktop widget that shows Codex usage windows."""

    def __init__(
        self,
        *,
        auth_file,
        base_url: str,
        refresh_seconds: int = 60,
        deepseek_api_key: str | None = None,
        deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        deepseek_state_file=DEFAULT_DEEPSEEK_STATE_FILE,
        deepseek_spent_baseline: Decimal | None = None,
        mimo_chrome_profile: str | None = None,
        mimo_base_url: str = DEFAULT_MIMO_BASE_URL,
        claude_chrome_profile: str | None = None,
        kiro_token_db=DEFAULT_KIRO_TOKEN_DB,
        kiro_base_url: str = DEFAULT_KIRO_BASE_URL,
        cursor_state_db=DEFAULT_CURSOR_STATE_DB,
        openai_admin_key: str | None = None,
        openai_credits_purchased: Decimal | None = None,
        openai_credits_since=None,
        openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
        ui_state_file=DEFAULT_UI_STATE_FILE,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._auth_file = auth_file
        self._base_url = base_url
        self._deepseek_api_key = deepseek_api_key
        self._deepseek_base_url = deepseek_base_url
        self._deepseek_state_file = deepseek_state_file
        self._deepseek_spent_baseline = deepseek_spent_baseline
        self._mimo_chrome_profile = mimo_chrome_profile
        self._mimo_base_url = mimo_base_url
        self._claude_chrome_profile = claude_chrome_profile
        self._kiro_token_db = kiro_token_db
        self._kiro_base_url = kiro_base_url
        self._cursor_state_db = cursor_state_db
        self._openai_admin_key = openai_admin_key
        self._openai_credits_purchased = openai_credits_purchased
        self._openai_credits_since = openai_credits_since
        self._openai_base_url = openai_base_url
        self._ui_state_file = ui_state_file
        self._drag_position: QtCore.QPoint | None = None
        self._thread: QtCore.QThread | None = None
        self._worker: UsageFetchWorker | None = None
        self._closing = False
        self._retired_threads: list[QtCore.QThread] = []

        ui_state = load_ui_state(ui_state_file)
        self._scale_saved = ui_state.scale is not None
        self._scale = _clamp_scale(ui_state.scale) if ui_state.scale is not None else 1.0
        self._horizontal = ui_state.horizontal
        self._orientation_locked = ui_state.orientation_locked
        self._collapsed = ui_state.collapsed
        self._expanded_position: QtCore.QPoint | None = None
        self._collapsed_position: QtCore.QPoint | None = None
        self._saved_screen_name = ui_state.screen_name
        self._saved_offset = (
            QtCore.QPoint(ui_state.offset_x, ui_state.offset_y)
            if ui_state.offset_x is not None and ui_state.offset_y is not None
            else None
        )
        self._codex_model: MetricCardModel | None = None
        self._deepseek_model: MetricCardModel | None = None
        self._mimo_model: MetricCardModel | None = None
        self._claude_model: MetricCardModel | None = None
        self._kiro_model: MetricCardModel | None = None
        self._cursor_model: MetricCardModel | None = None
        self._openai_model: MetricCardModel | None = None
        self._user_width = ui_state.width
        self._active_only = ui_state.active_only
        self._width_gesture = False
        self._width_drag: tuple[QtCore.Qt.Edge, int, int, int] | None = None
        self._applying_geometry = False
        self._placing = False
        self._placed_origin: QtCore.QPoint | None = None
        self._kwin_active = False
        self._kwin_confirmed = False
        self._kwin_hooked = False
        self._user_moved = False
        self._reported: QtCore.QPoint | None = None
        self._expecting: QtCore.QPoint | None = None
        self._expecting_at = 0.0
        self._chrome_visible = False
        self._awaiting_reveal = False
        self._kwin = KWinPlacer(self)
        self._layer_shell: LayerShell | None = load_layer_shell()

        self._layout = QtWidgets.QHBoxLayout(self)
        self._codex_card = MetricCard(
            title="Codex",
            primary_label="Janela de 5 horas",
            secondary_label="Janela semanal",
            bars=True,
            parent=self,
        )
        self._claude_card = MetricCard(
            title="Claude Code",
            primary_label="Janela de 5 horas",
            secondary_label="Janela semanal",
            bars=True,
            parent=self,
        )
        self._cursor_card = MetricCard(
            title="Cursor",
            primary_label="Uso do plano",
            secondary_label="Uso da API",
            bars=True,
            parent=self,
        )
        self._kiro_card = MetricCard(
            title="Kiro",
            primary_label="Créditos restantes",
            secondary_label="Créditos usados",
            bars=True,
            parent=self,
        )
        self._deepseek_card = MetricCard(
            title="DeepSeek",
            primary_label="Saldo",
            secondary_label="Gasto monitorado",
            parent=self,
        )
        self._mimo_card = MetricCard(
            title="MiMo",
            primary_label="Saldo",
            secondary_label="Gasto total",
            parent=self,
        )
        self._openai_card = MetricCard(
            title="OpenAI",
            primary_label="Gasto no mês",
            secondary_label="Gasto hoje",
            parent=self,
        )
        self._detail_popup = CardDetailPopup(parent=self)
        self._detail_popup.clicked.connect(self._hide_card_detail)
        for card in self._metric_cards():
            self._layout.addWidget(card)
            card.set_loading()
            card.clicked.connect(lambda card=card: self._toggle_card_detail(card))
            card.moved.connect(self._save_ui_state)

        self._badge = CollapsedBadge(parent=self)
        self._layout.addWidget(self._badge)
        self._badge.clicked.connect(self._toggle_collapsed)
        self._badge.moved.connect(self._save_ui_state)
        self._badge.hide()

        self._shortcuts = QtWidgets.QLabel(parent=self)
        self._shortcuts.setObjectName("shortcuts")
        self._shortcuts.setWordWrap(True)
        self._layout.addWidget(self._shortcuts)

        self._toggle_button = QtWidgets.QToolButton(self)
        self._toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._toggle_button.setAutoRaise(True)
        self._toggle_button.setObjectName("toggle")
        self._toggle_button.clicked.connect(self._toggle_collapsed)
        self._update_toggle_button()
        self._close_button = QtWidgets.QToolButton(self)
        self._close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._close_button.setAutoRaise(True)
        self._close_button.setObjectName("close")
        self._close_button.setText("×")
        self._close_button.setToolTip("Fechar")
        self._close_button.clicked.connect(self.close)
        self._close_button.hide()
        self._toggle_button.hide()

        self.setMouseTracking(True)
        icon = load_window_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setWindowTitle("Codex Usage")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Window
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet("background: transparent;")
        if self._kwin.available():
            # Wayland maps new toplevels at the center. Opacity is ignored here, so
            # keep children hidden (fully transparent shell) until KWin confirms.
            self._awaiting_reveal = True
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._quit_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Q"), self)
        self._quit_shortcut.activated.connect(self.close)
        self._increase_shortcuts = (
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl++"), self),
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+="), self),
        )
        for shortcut in self._increase_shortcuts:
            shortcut.activated.connect(self._increase_scale)
        self._decrease_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+-"), self)
        self._decrease_shortcut.activated.connect(self._decrease_scale)
        self._toggle_orientation_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+H"), self)
        self._toggle_orientation_shortcut.activated.connect(self._toggle_orientation)
        self._active_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+A"), self)
        self._active_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        self._active_shortcut.activated.connect(self._toggle_active_only)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(max(15, refresh_seconds) * 1000)
        self._timer.timeout.connect(self.refresh)
        self._kwin_watch = QtCore.QTimer(self)
        self._kwin_watch.setInterval(500)
        self._kwin_watch.timeout.connect(self._kwin.report)
        self._chrome_timer = QtCore.QTimer(self)
        self._chrome_timer.setSingleShot(True)
        self._chrome_timer.setInterval(200)
        self._chrome_timer.timeout.connect(self._hide_window_buttons)
        self._chrome_watch = QtCore.QTimer(self)
        self._chrome_watch.setInterval(300)
        self._chrome_watch.timeout.connect(self._sync_chrome_hover)
        self._place_retry = QtCore.QTimer(self)
        self._place_retry.setInterval(200)
        self._place_retry.timeout.connect(self._place_when_mapped)

        self._arm_chrome_filters()
        self._apply_scale()
        self._place_initially()
        if self._awaiting_reveal:
            self._place_retry.start()
            QtCore.QTimer.singleShot(2000, self._force_reveal)
        else:
            QtCore.QTimer.singleShot(0, self._place_when_mapped)
        QtCore.QTimer.singleShot(0, self.refresh)
        self._timer.start()

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            edge = self._width_edge(event.position())
            if edge is not None:
                self._width_gesture = True
                handle = self.windowHandle()
                if (
                    self._layer_shell is None
                    and handle is not None
                    and handle.startSystemResize(edge)
                ):
                    event.accept()
                    return
                origin = self._origin()
                anchor = origin.x() + self.width() if edge == QtCore.Qt.Edge.LeftEdge else origin.x()
                self._width_drag = (edge, event.globalPosition().toPoint().x(), self.width(), anchor)
                event.accept()
                return
            self._width_gesture = False
            self._drag_position = event.globalPosition().toPoint() - self._origin()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and self._width_drag is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            edge, origin_x, start_width, anchor = self._width_drag
            delta = event.globalPosition().toPoint().x() - origin_x
            if edge == QtCore.Qt.Edge.LeftEdge:
                width = self._clamp_panel_width(start_width - delta)
                self._user_moved = True
                self._set_window_geometry(anchor - width, self._origin().y(), width, self.height())
            else:
                width = self._clamp_panel_width(start_width + delta)
                self._user_moved = True
                self._set_window_geometry(anchor, self._origin().y(), width, self.height())
            event.accept()
            return
        if event and not event.buttons():
            self._sync_width_cursor(event.position())
        if event and event.buttons() & QtCore.Qt.MouseButton.LeftButton and self._drag_position is not None:
            self._user_moved = True
            self._place_at((event.globalPosition().toPoint() - self._drag_position).x(),
                           (event.globalPosition().toPoint() - self._drag_position).y())
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton and self._width_drag is not None:
            self._width_drag = None
            self._width_gesture = False
            self._user_width = self.width()
            self._save_ui_state()
            event.accept()
            return
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._width_gesture = False
            was_dragging = self._drag_position is not None
            self._drag_position = None
            event.accept()
            if was_dragging:
                self._save_ui_state()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QtCore.QEvent | None) -> None:  # noqa: N802
        if self._width_drag is None:
            self.unsetCursor()
        # Parent leave also fires when the pointer enters a child; hide later
        # only if nothing in the window tree is still under the mouse.
        self._chrome_timer.start()
        super().leaveEvent(event)

    def enterEvent(self, event: QtGui.QEnterEvent | None) -> None:  # noqa: N802
        self._show_chrome()
        super().enterEvent(event)

    def eventFilter(self, watched: QtCore.QObject | None, event: QtCore.QEvent | None) -> bool:  # noqa: N802
        if event is not None and not self._awaiting_reveal and not self._closing:
            kind = event.type()
            if kind in (
                QtCore.QEvent.Type.Enter,
                QtCore.QEvent.Type.HoverEnter,
                QtCore.QEvent.Type.HoverMove,
                QtCore.QEvent.Type.MouseMove,
            ):
                self._show_chrome()
            elif kind == QtCore.QEvent.Type.Leave:
                self._chrome_timer.start()
            elif kind == QtCore.QEvent.Type.ChildAdded and isinstance(event, QtCore.QChildEvent):
                child = event.child()
                if isinstance(child, QtWidgets.QWidget):
                    child.installEventFilter(self)
        return super().eventFilter(watched, event)

    def _width_edge(self, pos: QtCore.QPointF) -> QtCore.Qt.Edge | None:
        if self._collapsed:
            return None
        margin = max(6, int(round(8 * self._scale)))
        if pos.x() <= margin:
            return QtCore.Qt.Edge.LeftEdge
        if pos.x() >= self.width() - margin:
            return QtCore.Qt.Edge.RightEdge
        return None

    def _sync_width_cursor(self, pos: QtCore.QPointF) -> None:
        if self._width_edge(pos) is not None:
            self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()

    def _clamp_panel_width(self, width: int) -> int:
        floor = max(1, self.minimumWidth())
        screen = self._source_screen()
        if screen is None:
            return max(floor, width)
        cap = screen.availableGeometry().width() - 20
        if cap < floor:
            return floor
        return min(max(width, floor), cap)

    def moveEvent(self, event: QtGui.QMoveEvent | None) -> None:  # noqa: N802
        super().moveEvent(event)
        self._hide_card_detail()
        if not self._placing and self._layer_shell is None and not self._kwin_active:
            self._placed_origin = QtCore.QPoint(self.pos())

    def _panel_geometry(self) -> QtCore.QRect:
        return QtCore.QRect(self._origin(), self.size())

    def _toggle_card_detail(self, card: MetricCard) -> None:
        popup = self._detail_popup
        if popup.isVisible() and popup.card_title() == card.labels()[0]:
            popup.hide()
            return
        popup.show_card(card, panel=self._panel_geometry(), scale=self._scale)

    def _hide_card_detail(self) -> None:
        self._detail_popup.hide()

    def closeEvent(self, event: QtGui.QCloseEvent | None) -> None:  # noqa: N802
        if self._closing:
            if event is not None:
                event.accept()
            return
        self._hide_card_detail()
        if self.isVisible():
            self._save_ui_state()
        self._closing = True
        self._timer.stop()
        self._kwin_watch.stop()
        self._chrome_timer.stop()
        self._chrome_watch.stop()
        self._place_retry.stop()
        self._stop_worker()
        try:
            self._join_retired_threads()
        except KeyboardInterrupt:
            # The fetch thread is still running. Destroying its QThread aborts
            # the process, so leave it and let the interpreter exit.
            pass
        super().closeEvent(event)

    def showEvent(self, event: QtGui.QShowEvent | None) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_layer_shell()
        if self._kwin.available() and not self._kwin_hooked:
            self._kwin.moved.connect(self._on_kwin_reported)
            self._kwin_hooked = True
        if self._awaiting_reveal:
            if not self._place_retry.isActive():
                self._place_retry.start()
            self._place_when_mapped()
        elif self._kwin.available() and not self._kwin_watch.isActive():
            self._kwin.report()
            self._kwin_watch.start()

    @QtCore.pyqtSlot()
    def refresh(self) -> None:
        if self._thread is not None or self._closing:
            return
        self._thread = QtCore.QThread(self)
        self._worker = UsageFetchWorker(
            auth_file=self._auth_file,
            base_url=self._base_url,
            deepseek_api_key=self._deepseek_api_key,
            deepseek_base_url=self._deepseek_base_url,
            deepseek_state_file=self._deepseek_state_file,
            deepseek_spent_baseline=self._deepseek_spent_baseline,
            mimo_chrome_profile=self._mimo_chrome_profile,
            mimo_base_url=self._mimo_base_url,
            claude_chrome_profile=self._claude_chrome_profile,
            kiro_token_db=self._kiro_token_db,
            kiro_base_url=self._kiro_base_url,
            cursor_state_db=self._cursor_state_db,
            openai_admin_key=self._openai_admin_key,
            openai_credits_purchased=self._openai_credits_purchased,
            openai_credits_since=self._openai_credits_since,
            openai_base_url=self._openai_base_url,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.loaded.connect(self._handle_usage_loaded)
        self._worker.failed.connect(self._handle_usage_failed)
        self._worker.deepseek_loaded.connect(self._handle_deepseek_loaded)
        self._worker.deepseek_failed.connect(self._handle_deepseek_failed)
        self._worker.mimo_loaded.connect(self._handle_mimo_loaded)
        self._worker.mimo_failed.connect(self._handle_mimo_failed)
        self._worker.claude_loaded.connect(self._handle_claude_loaded)
        self._worker.claude_failed.connect(self._handle_claude_failed)
        self._worker.kiro_loaded.connect(self._handle_kiro_loaded)
        self._worker.kiro_failed.connect(self._handle_kiro_failed)
        self._worker.cursor_loaded.connect(self._handle_cursor_loaded)
        self._worker.cursor_failed.connect(self._handle_cursor_failed)
        self._worker.openai_loaded.connect(self._handle_openai_loaded)
        self._worker.openai_failed.connect(self._handle_openai_failed)
        self._worker.finished.connect(self._stop_worker)
        self._thread.start()

    @QtCore.pyqtSlot(object)
    def _handle_usage_loaded(self, model: MetricCardModel) -> None:
        self._codex_model = model
        self._codex_card.set_model(model)
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_usage_failed(self, message: str) -> None:
        self._codex_card.set_error(message)

    @QtCore.pyqtSlot(object)
    def _handle_deepseek_loaded(self, model: MetricCardModel) -> None:
        self._deepseek_model = model
        self._deepseek_card.set_model(model)
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_deepseek_failed(self, message: str) -> None:
        self._deepseek_card.set_error(message)

    @QtCore.pyqtSlot(object)
    def _handle_mimo_loaded(self, model: MetricCardModel) -> None:
        self._mimo_model = model
        self._mimo_card.set_model(model)
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_mimo_failed(self, message: str) -> None:
        self._mimo_card.set_error(message)

    @QtCore.pyqtSlot(object)
    def _handle_claude_loaded(self, model: MetricCardModel) -> None:
        self._claude_model = model
        self._claude_card.set_model(model)
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_claude_failed(self, message: str) -> None:
        self._claude_card.set_error(message)

    @QtCore.pyqtSlot(object)
    def _handle_kiro_loaded(self, model: MetricCardModel) -> None:
        self._kiro_model = model
        self._kiro_card.set_model(model)
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_kiro_failed(self, message: str) -> None:
        self._kiro_card.set_error(message)

    @QtCore.pyqtSlot(object)
    def _handle_cursor_loaded(self, model: MetricCardModel) -> None:
        self._cursor_model = model
        self._cursor_card.set_model(model)
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_cursor_failed(self, message: str) -> None:
        self._cursor_card.set_error(message)

    @QtCore.pyqtSlot(object)
    def _handle_openai_loaded(self, model: MetricCardModel) -> None:
        self._openai_model = model
        self._openai_card.set_model(model)

    @QtCore.pyqtSlot(str)
    def _handle_openai_failed(self, message: str) -> None:
        self._openai_card.set_error(message)

    def _increase_scale(self) -> None:
        self._change_scale(SCALE_STEP)

    def _decrease_scale(self) -> None:
        self._change_scale(-SCALE_STEP)

    def _change_scale(self, delta: float) -> None:
        next_scale = max(MIN_SCALE, min(MAX_SCALE, round(self._scale + delta, 2)))
        if next_scale == self._scale:
            return
        self._scale = next_scale
        self._scale_saved = True
        self._apply_scale()
        self._save_ui_state()

    def _toggle_orientation(self) -> None:
        self._horizontal = not self._effective_horizontal()
        self._orientation_locked = True
        self._apply_scale()
        self._save_ui_state()

    def _toggle_collapsed(self) -> None:
        collapsing = not self._collapsed
        source_screen = self._source_screen()
        self._hide_card_detail()
        bottom_right = self._bottom_right()
        if collapsing:
            self._expanded_position = self._origin()
            badge_unmoved = True
        else:
            badge_unmoved = (
                self._collapsed_position is not None
                and self._origin() == self._collapsed_position
            )

        self._collapsed = not self._collapsed
        self._update_toggle_button()
        self._apply_scale()
        if not self._collapsed:
            self._refit_if_needed(source_screen)

        if self._collapsed:
            self._anchor_bottom_right(bottom_right, source_screen)
            self._collapsed_position = self._origin()
        elif badge_unmoved and self._expanded_position is not None:
            self._place_at(self._expanded_position.x(), self._expanded_position.y())
            self._clamp_to_screen(source_screen)
        else:
            self._anchor_bottom_right(bottom_right, source_screen)
        self._save_ui_state()

    def _origin(self) -> QtCore.QPoint:
        if self._user_moved and self._expecting is not None:
            return QtCore.QPoint(self._expecting)
        if self._reported is not None and (self._kwin_confirmed or not self._kwin_active):
            return QtCore.QPoint(self._reported)
        if self._placed_origin is not None and (self._layer_shell is not None or self._kwin_active):
            return QtCore.QPoint(self._placed_origin)
        if self._reported is not None:
            return QtCore.QPoint(self._reported)
        return self.pos()

    def _on_kwin_reported(self, x: int, y: int) -> None:
        reported = QtCore.QPoint(int(x), int(y))
        if self._expecting is not None:
            near = (
                abs(reported.x() - self._expecting.x()) <= 80
                and abs(reported.y() - self._expecting.y()) <= 80
            )
            if near:
                self._reported = reported
                self._placed_origin = reported
                self._kwin_confirmed = True
                self._reveal()
                return
            if time.monotonic() - self._expecting_at < 2.0:
                return
            self._reported = reported
            self._placed_origin = reported
            self._expecting = reported
            self._user_moved = True
            self._reveal()
            return
        self._reported = reported
        self._sync_chrome_hover()

    def _bottom_right(self) -> QtCore.QPoint:
        position = self._origin()
        return QtCore.QPoint(position.x() + self.width(), position.y() + self.height())

    def _source_screen(self) -> QtGui.QScreen | None:
        control = self._badge if self._collapsed else self._toggle_button
        return (
            QtWidgets.QApplication.screenAt(control.mapToGlobal(control.rect().center()))
            or QtWidgets.QApplication.screenAt(self.geometry().center())
            or self.screen()
            or QtWidgets.QApplication.primaryScreen()
        )

    def _anchor_bottom_right(self, bottom_right: QtCore.QPoint, screen: QtGui.QScreen | None) -> None:
        self._place_at(bottom_right.x() - self.width(), bottom_right.y() - self.height())
        self._clamp_to_screen(screen)

    def _clamp_to_screen(self, screen: QtGui.QScreen | None) -> None:
        if screen is None:
            return
        area = screen.availableGeometry()
        max_x = max(area.x(), area.x() + area.width() - self.width())
        max_y = max(area.y(), area.y() + area.height() - self.height())
        origin = self._placed_origin if self._placed_origin is not None else self.pos()
        target_x = min(max(origin.x(), area.x()), max_x)
        target_y = min(max(origin.y(), area.y()), max_y)
        if origin.x() == target_x and origin.y() == target_y:
            return
        self._place_at(target_x, target_y)

    def _place_at(self, x: int, y: int) -> None:
        self._set_window_geometry(x, y, self.width(), self.height())

    def _set_window_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self._placing = True
        self._placed_origin = QtCore.QPoint(x, y)
        try:
            if self._layer_shell is not None and self._anchor_layer_shell(self, x, y, width, height, top_right=True):
                if self.width() != width or self.height() != height:
                    self.resize(width, height)
                return
            resizing = self.width() != width or self.height() != height
            if self.isVisible() and self._kwin.place(x, y, width, height, resize=resizing):
                self._kwin_active = True
                self._expecting = QtCore.QPoint(x, y)
                self._expecting_at = time.monotonic()
                if self.width() != width or self.height() != height:
                    self.resize(width, height)
                return
            self.setGeometry(x, y, width, height)
        finally:
            self._placing = False

    def _ensure_layer_shell(self) -> None:
        if self._layer_shell is None:
            self._layer_shell = load_layer_shell()
        if self._layer_shell is None or self._placed_origin is None:
            return
        self._anchor_layer_shell(
            self,
            self._placed_origin.x(),
            self._placed_origin.y(),
            self.width(),
            self.height(),
            top_right=True,
        )

    def _anchor_layer_shell(
        self,
        widget: QtWidgets.QWidget,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        top_right: bool,
    ) -> bool:
        if self._layer_shell is None:
            return False
        handle = widget.windowHandle()
        if handle is None:
            widget.winId()
            handle = widget.windowHandle()
        if handle is None:
            return False
        screen = QtWidgets.QApplication.screenAt(QtCore.QPoint(x + width // 2, y + max(1, height) // 2))
        if screen is None:
            screen = handle.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return False
        bounds = screen.geometry()
        origin = QtCore.QPoint(x, y)
        if top_right:
            anchors = ANCHOR_TOP | ANCHOR_RIGHT
            margins = top_right_margins(bounds, origin, width)
        else:
            anchors = ANCHOR_TOP | ANCHOR_LEFT
            margins = top_left_margins(bounds, origin)
        try:
            screen_ptr = sip.unwrapinstance(screen)
            return self._layer_shell.place(
                sip.unwrapinstance(handle),
                anchors=anchors,
                margins=margins,
                size=(width, height),
                screen=screen_ptr,
            )
        except (OSError, AttributeError, TypeError, ValueError):
            self._layer_shell = None
            return False

    def place_overlay(self, overlay: QtWidgets.QWidget, origin: QtCore.QPoint) -> None:
        if self._anchor_layer_shell(
            overlay,
            origin.x(),
            origin.y(),
            overlay.width(),
            overlay.height(),
            top_right=False,
        ):
            return
        if overlay.isVisible() and self._kwin.place(
            origin.x(),
            origin.y(),
            overlay.width(),
            overlay.height(),
            caption=overlay.windowTitle(),
            resize=True,
        ):
            return
        overlay.move(origin)

    def _update_toggle_button(self) -> None:
        self._toggle_button.setText("+" if self._collapsed else "–")
        self._toggle_button.setToolTip("Expandir" if self._collapsed else "Recolher")

    def _save_ui_state(self) -> None:
        screen = self._source_screen()
        area = screen.availableGeometry() if screen is not None else None
        position = self._origin()
        save_ui_state(
            WidgetUiState(
                horizontal=self._horizontal,
                orientation_locked=self._orientation_locked,
                scale=self._scale if self._scale_saved else None,
                collapsed=self._collapsed,
                screen_name=screen.name() if screen is not None else None,
                offset_x=position.x() - area.x() if area is not None else None,
                offset_y=position.y() - area.y() if area is not None else None,
                width=self._user_width,
                active_only=self._active_only,
            ),
            self._ui_state_file,
        )

    def _effective_horizontal(self) -> bool:
        if not self._horizontal:
            return False
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return True
        card_count = len(self._metric_cards())
        needed = (
            MetricCard.MIN_WIDTH * card_count + CARD_SPACING * (card_count - 1) + 32
        ) * self._scale
        if self._orientation_locked:
            return True
        return needed <= screen.availableGeometry().width() - 20

    def _apply_scale(self) -> None:
        margin_h = int(round(16 * self._scale))
        margin_v = int(round(10 * self._scale))
        spacing = int(round(CARD_SPACING * self._scale))
        self._layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)

        if self._collapsed:
            self._layout.setDirection(QtWidgets.QBoxLayout.Direction.TopToBottom)
            for card in self._metric_cards():
                card.apply_scale(self._scale)
                card.hide()
            self._badge.apply_scale(self._scale)
            self._badge.show()
            self._apply_badge_filter()
            shortcut_h = self._refresh_shortcut_label()
            width = int(round((CollapsedBadge.MIN_WIDTH + 32) * self._scale))
            height = int(round((CollapsedBadge.MIN_HEIGHT + 20) * self._scale)) + shortcut_h
            self._set_panel_size(width, height, minimum_width=width, fixed=True)
            self._position_window_buttons()
            if self._awaiting_reveal:
                self._hide_shell_content()
            return

        self._badge.hide()
        horizontal = self._effective_horizontal()
        direction = (
            QtWidgets.QBoxLayout.Direction.LeftToRight
            if horizontal
            else QtWidgets.QBoxLayout.Direction.TopToBottom
        )
        self._layout.setDirection(direction)
        self._layout.setSpacing(spacing)
        cards = self._metric_cards()
        visible = [card for card in cards if not self._active_only or card.is_active()]
        for card in cards:
            card.apply_scale(self._scale)
            card.setVisible(card in visible)
        self._apply_badge_filter()
        shortcut_h = self._refresh_shortcut_label()
        shown = visible
        gaps = spacing * max(0, len(shown))
        preferred_card = int(round(MetricCard.MIN_WIDTH * self._scale))
        floor_card = int(round(MetricCard.FLOOR_WIDTH * self._scale))
        shortcut_w = min(preferred_card, max(1, self._shortcuts.sizeHint().width()))
        if not shown:
            preferred = preferred_card + 2 * margin_h
            floor = floor_card + 2 * margin_h
            height = shortcut_h + 2 * margin_v
        elif horizontal:
            preferred = preferred_card * len(shown) + gaps + 2 * margin_h + shortcut_w
            floor = floor_card * len(shown) + gaps + 2 * margin_h
            height = max(card.minimumHeight() for card in shown) + 2 * margin_v
        else:
            preferred = preferred_card + 2 * margin_h
            floor = floor_card + 2 * margin_h
            height = sum(card.minimumHeight() for card in shown) + gaps + shortcut_h + 2 * margin_v
        chosen = preferred if self._user_width is None else max(floor, self._user_width)
        screen = self._source_screen()
        if screen is not None:
            cap = screen.availableGeometry().width() - 20
            if cap >= floor:
                chosen = min(chosen, cap)
        self._set_panel_size(chosen, height, minimum_width=floor, fixed=False)
        self._position_window_buttons()
        if self._awaiting_reveal:
            self._hide_shell_content()

    def _set_panel_size(self, width: int, height: int, *, minimum_width: int, fixed: bool) -> None:
        self._applying_geometry = True
        try:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(MAXIMUM_WIDGET_SIZE, MAXIMUM_WIDGET_SIZE)
            if fixed:
                self.setFixedSize(width, height)
                return
            self.setFixedHeight(height)
            self.setMinimumWidth(minimum_width)
            self.resize(width, height)
        finally:
            self._applying_geometry = False

    def _position_window_buttons(self) -> None:
        size = max(26, int(round(30 * self._scale)))
        grip = max(6, int(round(8 * self._scale)))
        margin = max(grip + 4, int(round(8 * self._scale)))
        gap = max(4, int(round(6 * self._scale)))
        self._style_window_button(self._close_button, size, "#6b2d2d", "#8a3a3a", "#f0c2c2")
        self._close_button.move(max(margin, self.width() - size - margin), margin)
        if not self._collapsed:
            self._style_window_button(self._toggle_button, size, "#3a3a3a", "#4a4a4a", "#d5d5d5")
            self._toggle_button.move(max(margin, self.width() - (2 * size) - gap - margin), margin)
        self._apply_chrome_visibility()

    def _apply_badge_filter(self) -> None:
        if not self._active_only:
            self._badge.set_active_names(None)
            return
        names = {
            _BADGE_NAMES[title]
            for title, card in ((card.labels()[0], card) for card in self._metric_cards())
            if card.is_active() and title in _BADGE_NAMES
        }
        self._badge.set_active_names(names)

    def _style_window_button(
        self,
        button: QtWidgets.QToolButton,
        size: int,
        fill: str,
        hover: str,
        border: str,
    ) -> None:
        signature = (size, fill, hover, border)
        if button.property("chrome") == signature:
            return
        button.setProperty("chrome", signature)
        button.setFixedSize(size, size)
        font_size = max(14, int(round(18 * self._scale)))
        radius = size // 2
        button.setStyleSheet(
            f"""
            QToolButton#{button.objectName()} {{
                background-color: {fill};
                border: 1px solid {border};
                border-radius: {radius}px;
                color: #ffffff;
                font-size: {font_size}px;
                font-weight: 700;
                padding: 0px;
            }}
            QToolButton#{button.objectName()}:hover {{
                background-color: {hover};
                border-color: #ffffff;
            }}
            """
        )

    def _update_badge(self) -> None:
        codex = (
            self._codex_model.primary_value.replace(" restantes", "")
            if self._codex_model is not None
            else "—"
        )
        claude = (
            self._claude_model.primary_value.replace(" restantes", "")
            if self._claude_model is not None
            else "—"
        )
        cursor = (
            self._cursor_model.primary_value.replace(" restantes", "")
            if self._cursor_model is not None
            else "—"
        )
        kiro = (
            self._kiro_model.primary_value.replace(" restantes", "")
            if self._kiro_model is not None
            else "—"
        )
        deepseek = self._deepseek_model.primary_value if self._deepseek_model is not None else "—"
        mimo = self._mimo_model.primary_value if self._mimo_model is not None else "—"
        self._badge.set_metrics(
            codex=codex,
            claude=claude,
            cursor=cursor,
            kiro=kiro,
            deepseek=deepseek,
            mimo=mimo,
        )

    def resizeEvent(self, event: QtGui.QResizeEvent | None) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_close_button"):
            self._position_window_buttons()
        if self._applying_geometry or not self._width_gesture or self._collapsed:
            return
        width = self.width()
        if width != self._user_width:
            self._user_width = width
            self._save_ui_state()

    def _stop_worker(self) -> None:
        worker = self._worker
        thread = self._thread
        self._worker = None
        self._thread = None
        if worker is not None:
            worker.deleteLater()
        if thread is None:
            return
        thread.quit()
        if thread.wait(500):
            thread.deleteLater()
            return
        # Destroying a running QThread aborts the process, so keep it owned
        # until the in-flight request finishes.
        self._retired_threads.append(thread)
        thread.finished.connect(self._discard_retired_thread)

    @QtCore.pyqtSlot()
    def _discard_retired_thread(self) -> None:
        thread = self.sender()
        if isinstance(thread, QtCore.QThread) and thread in self._retired_threads:
            self._retired_threads.remove(thread)
            thread.deleteLater()

    def _join_retired_threads(self) -> None:
        for thread in tuple(self._retired_threads):
            thread.wait(THREAD_JOIN_TIMEOUT_MS)
            self._retired_threads.remove(thread)
            thread.deleteLater()

    def _metric_cards(self) -> tuple[MetricCard, ...]:
        return (
            self._codex_card,
            self._claude_card,
            self._cursor_card,
            self._kiro_card,
            self._deepseek_card,
            self._mimo_card,
            self._openai_card,
        )

    def _refit_if_needed(self, screen: QtGui.QScreen | None = None) -> None:
        if self._scale_saved or self._collapsed:
            return
        screen = screen or self._source_screen()
        if screen is None:
            return
        self._fit_scale_to_screen(screen.availableGeometry())

    def _place_when_mapped(self) -> None:
        if self._closing:
            return
        if self._user_moved or self._kwin_confirmed:
            self._reveal()
            return
        self._place_initially()

    def _force_reveal(self) -> None:
        self._reveal()

    def _reveal(self) -> None:
        was_waiting = self._awaiting_reveal
        self._awaiting_reveal = False
        self._place_retry.stop()
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        if was_waiting:
            self._apply_scale()
            if self._kwin.available() and not self._kwin_watch.isActive():
                self._kwin.report()
                self._kwin_watch.start()
        self._arm_chrome_filters()
        if not self._chrome_watch.isActive():
            self._chrome_watch.start()
        self._sync_chrome_hover()

    def _arm_chrome_filters(self) -> None:
        self.installEventFilter(self)
        for child in self.findChildren(QtWidgets.QWidget):
            child.installEventFilter(self)

    def _show_chrome(self) -> None:
        if self._awaiting_reveal or self._closing:
            return
        self._chrome_timer.stop()
        if self._chrome_visible:
            return
        self._chrome_visible = True
        self._apply_chrome_visibility()

    def _sync_chrome_hover(self) -> None:
        if self._awaiting_reveal or not self.isVisible():
            self._chrome_visible = False
            self._apply_chrome_visibility()
            return
        over = self._pointer_in_window()
        if over == self._chrome_visible:
            return
        self._chrome_visible = over
        self._apply_chrome_visibility()

    def _pointer_in_window(self) -> bool:
        # underMouse() tracks the real pointer on Wayland; QCursor.pos() often
        # stays at 0,0 for always-on-top windows that never activate.
        if self.underMouse():
            return True
        for child in self.findChildren(QtWidgets.QWidget):
            if child.isVisible() and child.underMouse():
                return True
        cursor = QtGui.QCursor.pos()
        if cursor.isNull() or (cursor.x() == 0 and cursor.y() == 0):
            return False
        return QtCore.QRect(self._origin(), self.size()).contains(cursor)

    def _cursor_over_self(self) -> bool:
        return self._pointer_in_window()

    def _hide_shell_content(self) -> None:
        for card in self._metric_cards():
            card.hide()
        self._badge.hide()
        self._shortcuts.hide()
        self._chrome_visible = False
        self._apply_chrome_visibility()

    def _toggle_active_only(self) -> None:
        self._active_only = not self._active_only
        self._apply_scale()
        self._save_ui_state()

    def _refresh_shortcut_label(self) -> int:
        font = max(10, int(round(11 * self._scale)))
        self._shortcuts.setText(self._shortcut_text())
        self._shortcuts.setStyleSheet(
            "QLabel#shortcuts {"
            f"color: #9aa6b8; font-size: {font}px; background: transparent;"
            "border: none; padding-top: 4px;"
            "}"
        )
        self._shortcuts.show()
        return max(self._shortcuts.sizeHint().height(), font + 8)

    def _shortcut_text(self) -> str:
        active = "Ctrl+Shift+A  todas" if self._active_only else "Ctrl+Shift+A  só as ativas"
        return f"{active}    ·    Q  fechar    ·    Ctrl+H  girar    ·    Ctrl++  tamanho"

    def _hide_window_buttons(self) -> None:
        self._sync_chrome_hover()

    def _apply_chrome_visibility(self) -> None:
        show = self._chrome_visible and not self._awaiting_reveal
        self._close_button.setVisible(show)
        self._toggle_button.setVisible(show and not self._collapsed)
        if show:
            self._close_button.raise_()
            self._toggle_button.raise_()

    def _place_initially(self) -> None:
        restored_screen = (
            next(
                (screen for screen in QtWidgets.QApplication.screens()
                 if screen.name() == self._saved_screen_name),
                None,
            )
            if self._saved_offset is not None and self._saved_screen_name
            else None
        )
        screen = restored_screen or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        scale_changed = False
        if self._scale_saved:
            self._apply_scale()
            if not self._fits_screen(geometry):
                self._fit_scale_to_screen(geometry)
                scale_changed = True
        else:
            self._fit_scale_to_screen(geometry)
        if restored_screen is not None and self._saved_offset is not None:
            self._place_at(
                geometry.x() + self._saved_offset.x(),
                geometry.y() + self._saved_offset.y(),
            )
            self._clamp_to_screen(screen)
        else:
            self._place_at(
                geometry.x() + max(0, geometry.width() - self.width() - DEFAULT_EDGE_MARGIN),
                geometry.y() + DEFAULT_TOP_MARGIN,
            )
        if scale_changed and (restored_screen is not None or self._saved_offset is None):
            self._save_ui_state()

    def _fit_scale_to_screen(self, geometry: QtCore.QRect) -> None:
        self._scale = 1.0
        for _ in range(12):
            self._apply_scale()
            if self._fits_screen(geometry):
                return
            if self._scale <= MIN_SCALE:
                return
            self._scale = max(MIN_SCALE, round(self._scale - SCALE_STEP, 2))

    def _fits_screen(self, geometry: QtCore.QRect) -> bool:
        return (
            self.width() <= geometry.width() - 20
            and self.height() <= geometry.height() - DEFAULT_TOP_MARGIN
        )


class CollapsedBadge(QtWidgets.QFrame):
    MIN_WIDTH = 300
    MIN_HEIGHT = 190

    clicked = QtCore.pyqtSignal()
    moved = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._values: dict[str, QtWidgets.QLabel] = {}
        self._cells: dict[str, QtWidgets.QWidget] = {}
        self._drag_offset: QtCore.QPoint | None = None
        self._press_position: QtCore.QPoint | None = None
        self._moved = False
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        grid = QtWidgets.QGridLayout(self)
        for index, key in enumerate(("Codex", "Claude", "Cursor", "Kiro", "DeepSeek", "MiMo")):
            row, column = divmod(index, 2)
            cell = QtWidgets.QWidget()
            box = QtWidgets.QVBoxLayout(cell)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(1)
            caption = QtWidgets.QLabel(key)
            caption.setObjectName("caption")
            value = QtWidgets.QLabel("—")
            value.setObjectName("value")
            box.addWidget(caption)
            box.addWidget(value)
            grid.addWidget(cell, row, column)
            self._cells[key] = cell
            self._values[key] = value

        self.apply_scale(1.0)

    def set_active_names(self, names: set[str] | None) -> None:
        for key, cell in self._cells.items():
            cell.setVisible(names is None or key in names)

    def set_metrics(
        self,
        *,
        codex: str,
        claude: str,
        cursor: str,
        kiro: str,
        deepseek: str,
        mimo: str,
    ) -> None:
        self._values["Codex"].setText(codex)
        self._values["Claude"].setText(claude)
        self._values["Cursor"].setText(cursor)
        self._values["Kiro"].setText(kiro)
        self._values["DeepSeek"].setText(deepseek)
        self._values["MiMo"].setText(mimo)

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        layout = self.layout()
        if isinstance(layout, QtWidgets.QGridLayout):
            margin = int(round(18 * scale))
            layout.setContentsMargins(margin, margin, margin, margin)
            layout.setHorizontalSpacing(int(round(24 * scale)))
            layout.setVerticalSpacing(int(round(8 * scale)))
        self.setMinimumSize(
            int(round(self.MIN_WIDTH * scale)),
            int(round(self.MIN_HEIGHT * scale)),
        )
        radius = int(round(22 * scale))
        caption_size = max(9, int(round(11 * scale)))
        value_size = max(13, int(round(19 * scale)))
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: #222222;
                border: 1px solid #333333;
                border-radius: {radius}px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: #d6dbe4;
                font-family: Inter, "Segoe UI", Arial, sans-serif;
            }}
            QLabel#caption {{
                color: #bcc6d5;
                font-size: {caption_size}px;
                font-weight: 500;
            }}
            QLabel#value {{
                color: #ffffff;
                font-size: {value_size}px;
                font-weight: 750;
            }}
            """
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._press_position = event.globalPosition().toPoint()
            self._drag_offset = self._press_position - _window_top_left(self)
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if (
            event
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
            and self._drag_offset is not None
            and self._press_position is not None
        ):
            current = event.globalPosition().toPoint()
            if (current - self._press_position).manhattanLength() > 4:
                self._moved = True
            if self._moved:
                _drag_window_to(self, current - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_offset = None
            if not self._moved:
                self.clicked.emit()
            else:
                self.moved.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ElidedLabel(QtWidgets.QLabel):
    """Single-line label that elides its text instead of widening the card."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._full_text = ""

    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.updateGeometry()
        self._refresh()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        # Glyph advances are fractional; round up so the full text never elides at its own hint.
        margins = self.contentsMargins()
        advance = QtGui.QFontMetricsF(self.font()).horizontalAdvance(self._full_text)
        width = math.ceil(advance) + 1 + margins.left() + margins.right()
        return QtCore.QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802
        return QtCore.QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event: QtGui.QResizeEvent | None) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event: QtCore.QEvent | None) -> None:  # noqa: N802
        super().changeEvent(event)
        if event is not None and event.type() == QtCore.QEvent.Type.FontChange:
            self.updateGeometry()
            self._refresh()

    def _refresh(self) -> None:
        elided = self.fontMetrics().elidedText(
            self._full_text,
            QtCore.Qt.TextElideMode.ElideRight,
            max(0, self.contentsRect().width()),
        )
        self.setToolTip(self._full_text if elided != self._full_text else "")
        self.setText(elided)


class MetricCard(QtWidgets.QFrame):
    """Provider card; usage cards carry bars, balance cards use a shorter layout."""

    MIN_WIDTH = 498
    FLOOR_WIDTH = 280
    USAGE_HEIGHT = 128
    BALANCE_HEIGHT = 100

    clicked = QtCore.pyqtSignal()
    moved = QtCore.pyqtSignal()
    updated = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        title: str,
        primary_label: str,
        secondary_label: str,
        bars: bool = False,
        interactive: bool = True,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._state: tuple[str, object] = ("loading", None)
        self._bars = bars
        self._interactive = interactive
        self._drag_offset: QtCore.QPoint | None = None
        self._press_position: QtCore.QPoint | None = None
        self._moved = False
        if interactive:
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._default_title = title
        self._default_primary_label = primary_label
        self._default_secondary_label = secondary_label
        self._title = QtWidgets.QLabel(title)
        self._status = QtWidgets.QLabel()
        self._primary_caption = ElidedLabel()
        self._primary_value = ElidedLabel()
        self._secondary_caption = ElidedLabel()
        self._secondary_value = ElidedLabel()
        self._primary_value.set_full_text("—")
        self._secondary_value.set_full_text("—")
        self._primary_bar = QtWidgets.QProgressBar(self)
        self._secondary_bar = QtWidgets.QProgressBar(self)
        self._detail = ElidedLabel()
        self._footnote = ElidedLabel()

        self._title.setObjectName("title")
        self._status.setObjectName("status")
        for caption in (self._primary_caption, self._secondary_caption):
            caption.setObjectName("caption")
        for value in (self._primary_value, self._secondary_value):
            value.setObjectName("metric")
        for bar in (self._primary_bar, self._secondary_bar):
            bar.setObjectName("bar")
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.hide()
        self._detail.setObjectName("detail")
        self._footnote.setObjectName("footnote")
        self._primary_caption.set_full_text(primary_label)
        self._secondary_caption.set_full_text(secondary_label)

        # Header: title, plan/detail line and the availability dot share one row.
        self._header = QtWidgets.QHBoxLayout()
        self._header.addWidget(self._title, 0, QtCore.Qt.AlignmentFlag.AlignBaseline)
        self._header.addWidget(self._detail, 1, QtCore.Qt.AlignmentFlag.AlignBaseline)
        self._header.addWidget(self._status, 0, QtCore.Qt.AlignmentFlag.AlignBaseline)

        # Usage cards stack caption/value/bar; balance cards keep caption and value on one line.
        self._metrics = QtWidgets.QHBoxLayout()
        self._columns: list[QtWidgets.QBoxLayout] = []
        for caption, value, bar in (
            (self._primary_caption, self._primary_value, self._primary_bar),
            (self._secondary_caption, self._secondary_value, self._secondary_bar),
        ):
            if bars:
                column: QtWidgets.QBoxLayout = QtWidgets.QVBoxLayout()
                column.addWidget(caption)
                column.addWidget(value)
                column.addWidget(bar)
            else:
                column = QtWidgets.QHBoxLayout()
                column.addWidget(caption, 0, QtCore.Qt.AlignmentFlag.AlignBaseline)
                column.addWidget(value, 0, QtCore.Qt.AlignmentFlag.AlignBaseline)
                column.addStretch(1)
            self._columns.append(column)
            self._metrics.addLayout(column, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self._header)
        layout.addLayout(self._metrics)
        layout.addStretch(1)
        layout.addWidget(self._footnote)

        self.apply_scale(1.0)

    def has_bars(self) -> bool:
        return self._bars

    def base_height(self) -> int:
        return self.USAGE_HEIGHT if self._bars else self.BALANCE_HEIGHT

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        layout = self.layout()
        if isinstance(layout, QtWidgets.QVBoxLayout):
            horizontal = int(round(24 * scale))
            vertical = int(round(14 * scale))
            layout.setContentsMargins(horizontal, vertical, horizontal, vertical)
            layout.setSpacing(int(round(6 * scale)))
        self._header.setSpacing(int(round(10 * scale)))
        self._metrics.setSpacing(int(round(16 * scale)))
        for column in self._columns:
            column.setSpacing(int(round((2 if self._bars else 6) * scale)))
        for bar in (self._primary_bar, self._secondary_bar):
            bar.setFixedHeight(max(4, int(round(10 * scale))))
        self.setMinimumSize(
            int(round(MetricCard.FLOOR_WIDTH * scale)),
            int(round(self.base_height() * scale)),
        )
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self._apply_style()
        if isinstance(layout, QtWidgets.QVBoxLayout):
            layout.activate()
            needed = layout.minimumSize().height()
            if needed > self.minimumHeight():
                self.setMinimumHeight(needed)

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        return QtCore.QSize(
            int(round(self.MIN_WIDTH * self._scale)),
            max(self.minimumHeight(), int(round(self.base_height() * self._scale))),
        )

    def set_model(self, model: MetricCardModel) -> None:
        self._state: tuple[str, object] = ("model", model)
        self._title.setText(model.title)
        self._primary_caption.set_full_text(model.primary_label)
        self._primary_value.set_full_text(model.primary_value)
        self._secondary_caption.set_full_text(model.secondary_label)
        self._secondary_value.set_full_text(model.secondary_value)
        self._set_percent(self._primary_bar, model.primary_percent)
        self._set_percent(self._secondary_bar, model.secondary_percent)
        self._detail.set_full_text(model.detail_text)
        self._footnote.set_full_text(model.footnote)
        self._set_status(model.available)
        self._apply_style()
        self.updated.emit()

    def set_loading(self) -> None:
        self._state = ("loading", None)
        self._reset_defaults()
        self._footnote.set_full_text("Atualizando…")
        self._status.setText("")
        self._status.setToolTip("")
        self._apply_style()
        self.updated.emit()

    def set_error(self, message: str) -> None:
        self._state = ("error", message)
        self._reset_defaults()
        self._footnote.set_full_text(message)
        self._set_status(False)
        self._apply_style()
        self.updated.emit()

    def labels(self) -> tuple[str, str, str]:
        return (
            self._default_title,
            self._default_primary_label,
            self._default_secondary_label,
        )

    def set_labels(self, *, title: str, primary_label: str, secondary_label: str) -> None:
        self._default_title = title
        self._default_primary_label = primary_label
        self._default_secondary_label = secondary_label

    def state(self) -> tuple[str, object]:
        return self._state

    def is_active(self) -> bool:
        kind, payload = self._state
        return kind == "model" and isinstance(payload, MetricCardModel) and payload.available

    def apply_state(self, state: tuple[str, object]) -> None:
        kind, payload = state
        if kind == "model" and isinstance(payload, MetricCardModel):
            self.set_model(payload)
        elif kind == "error" and isinstance(payload, str):
            self.set_error(payload)
        else:
            self.set_loading()

    def _reset_defaults(self) -> None:
        self._title.setText(self._default_title)
        self._primary_caption.set_full_text(self._default_primary_label)
        self._secondary_caption.set_full_text(self._default_secondary_label)
        self._primary_value.set_full_text("—")
        self._secondary_value.set_full_text("—")
        self._set_percent(self._primary_bar, None)
        self._set_percent(self._secondary_bar, None)
        self._detail.set_full_text("")

    def _set_percent(self, bar: QtWidgets.QProgressBar, percent: int | None) -> None:
        if percent is None or not self._bars:
            bar.hide()
            return
        bar.setValue(percent)
        bar.show()

    def _set_status(self, available: bool) -> None:
        color, text = ("#22c55e", "disponível") if available else ("#f59e0b", "indisponível")
        self._status.setText(f'<span style="color:{color};">●</span>')
        self._status.setToolTip(text)

    def _apply_style(self) -> None:
        radius = int(round(22 * self._scale))
        title_size = int(round(17 * self._scale))
        caption_size = int(round(12 * self._scale))
        metric_size = int(round(22 * self._scale))
        detail_size = int(round(12 * self._scale))
        footnote_size = int(round(12 * self._scale))
        status_size = int(round(13 * self._scale))
        bar_radius = max(1, int(round(5 * self._scale)))
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: #222222;
                border: 1px solid #333333;
                border-radius: {radius}px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: #d6dbe4;
                font-family: Inter, "Segoe UI", Arial, sans-serif;
            }}
            QLabel#title {{
                font-size: {title_size}px;
                font-weight: 650;
            }}
            QLabel#caption {{
                color: #bcc6d5;
                font-size: {caption_size}px;
                font-weight: 500;
            }}
            QLabel#metric {{
                color: #ffffff;
                font-size: {metric_size}px;
                font-weight: 750;
            }}
            QLabel#detail {{
                color: #bcc6d5;
                font-size: {detail_size}px;
                font-weight: 500;
            }}
            QLabel#footnote {{
                color: #9aa6b8;
                font-size: {footnote_size}px;
                font-weight: 500;
            }}
            QLabel#status {{
                font-size: {status_size}px;
                font-weight: 600;
            }}
            QProgressBar#bar {{
                background-color: #e7ebf2;
                border: none;
                border-radius: {bar_radius}px;
            }}
            QProgressBar#bar::chunk {{
                background-color: #22c55e;
                border-radius: {bar_radius}px;
            }}
            """
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if (
            self._interactive
            and event
            and event.button() == QtCore.Qt.MouseButton.LeftButton
        ):
            self._press_position = event.globalPosition().toPoint()
            self._drag_offset = self._press_position - _window_top_left(self)
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if (
            event
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
            and self._drag_offset is not None
            and self._press_position is not None
        ):
            current = event.globalPosition().toPoint()
            if (current - self._press_position).manhattanLength() > 4:
                self._moved = True
            if self._moved:
                _drag_window_to(self, current - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if (
            self._interactive
            and event
            and event.button() == QtCore.Qt.MouseButton.LeftButton
            and self._press_position is not None
        ):
            self._drag_offset = None
            self._press_position = None
            if self._moved:
                self.moved.emit()
            else:
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CardDetailPopup(QtWidgets.QWidget):
    """Enlarged copy of a metric card, shown next to the panel."""

    GAP = 14

    clicked = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent, QtCore.Qt.WindowType.Tool)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Codex Usage — detalhe")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._source_title = ""
        self._source: MetricCard | None = None
        self._card = self._new_card(bars=False)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._card)

    def _new_card(self, *, bars: bool) -> MetricCard:
        return MetricCard(
            title="",
            primary_label="",
            secondary_label="",
            bars=bars,
            interactive=False,
            parent=self,
        )

    def has_bars(self) -> bool:
        return self._card.has_bars()

    @staticmethod
    def placement(
        panel: QtCore.QRect,
        size: QtCore.QSize,
        area: QtCore.QRect,
    ) -> QtCore.QPoint:
        gap = CardDetailPopup.GAP
        right = panel.right() + 1 + gap
        left = panel.x() - gap - size.width()
        below = panel.bottom() + 1 + gap
        above = panel.y() - gap - size.height()
        if right + size.width() <= area.right() + 1:
            x, y = right, panel.y()
        elif left >= area.x():
            x, y = left, panel.y()
        elif below + size.height() <= area.bottom() + 1:
            x, y = panel.x(), below
        elif above >= area.y():
            x, y = panel.x(), above
        else:
            x, y = right, panel.y()
        max_x = area.right() + 1 - size.width()
        max_y = area.bottom() + 1 - size.height()
        x = min(max(x, area.x()), max(area.x(), max_x))
        y = min(max(y, area.y()), max(area.y(), max_y))
        return QtCore.QPoint(x, y)

    def card_title(self) -> str:
        return self._source_title

    def show_card(self, card: MetricCard, *, panel: QtCore.QRect, scale: float) -> None:
        self._detach_source()
        self._source = card
        card.updated.connect(self._sync_from_source)
        self._source_title = card.labels()[0]
        if self._card.has_bars() != card.has_bars():
            replacement = self._new_card(bars=card.has_bars())
            layout = self.layout()
            if layout is not None:
                layout.replaceWidget(self._card, replacement)
            self._card.deleteLater()
            self._card = replacement
        self._sync_from_source()
        area = self._available_area(panel)
        self._card.apply_scale(self._fitting_scale(scale, area))
        size = self._card.minimumSize()
        origin = self.placement(panel, size, area)
        self.resize(size)
        self.show()
        parent = self.parent()
        if isinstance(parent, CodexUsageWidget):
            parent.place_overlay(self, origin)
        else:
            self.move(origin)
        self.raise_()

    def hideEvent(self, event: QtGui.QHideEvent | None) -> None:  # noqa: N802
        self._detach_source()
        super().hideEvent(event)

    def _sync_from_source(self) -> None:
        if self._source is None:
            return
        title, primary_label, secondary_label = self._source.labels()
        self._card.set_labels(
            title=title,
            primary_label=primary_label,
            secondary_label=secondary_label,
        )
        self._card.apply_state(self._source.state())

    def _detach_source(self) -> None:
        if self._source is not None:
            try:
                self._source.updated.disconnect(self._sync_from_source)
            except TypeError:
                pass
            self._source = None

    def _fitting_scale(self, scale: float, area: QtCore.QRect) -> float:
        target = _clamp_scale(max(scale, 0.6) * 1.8)
        while target > MIN_SCALE:
            width = MetricCard.MIN_WIDTH * target
            height = self._card.base_height() * target
            if width <= area.width() - 2 * self.GAP and height <= area.height() - 2 * self.GAP:
                break
            target = round(target - SCALE_STEP, 2)
        return _clamp_scale(target)

    def _available_area(self, panel: QtCore.QRect) -> QtCore.QRect:
        screen = QtWidgets.QApplication.screenAt(panel.center())
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return panel
        return screen.availableGeometry()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class UsageFetchWorker(QtCore.QObject):
    loaded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    deepseek_loaded = QtCore.pyqtSignal(object)
    deepseek_failed = QtCore.pyqtSignal(str)
    mimo_loaded = QtCore.pyqtSignal(object)
    mimo_failed = QtCore.pyqtSignal(str)
    claude_loaded = QtCore.pyqtSignal(object)
    claude_failed = QtCore.pyqtSignal(str)
    kiro_loaded = QtCore.pyqtSignal(object)
    kiro_failed = QtCore.pyqtSignal(str)
    cursor_loaded = QtCore.pyqtSignal(object)
    cursor_failed = QtCore.pyqtSignal(str)
    openai_loaded = QtCore.pyqtSignal(object)
    openai_failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        auth_file,
        base_url: str,
        deepseek_api_key: str | None,
        deepseek_base_url: str,
        deepseek_state_file,
        deepseek_spent_baseline: Decimal | None,
        mimo_chrome_profile: str | None,
        mimo_base_url: str,
        claude_chrome_profile: str | None,
        kiro_token_db,
        kiro_base_url: str,
        cursor_state_db,
        openai_admin_key: str | None,
        openai_credits_purchased: Decimal | None,
        openai_credits_since,
        openai_base_url: str,
    ) -> None:
        super().__init__()
        self._auth_file = auth_file
        self._base_url = base_url
        self._deepseek_api_key = deepseek_api_key
        self._deepseek_base_url = deepseek_base_url
        self._deepseek_state_file = deepseek_state_file
        self._deepseek_spent_baseline = deepseek_spent_baseline
        self._mimo_chrome_profile = mimo_chrome_profile
        self._mimo_base_url = mimo_base_url
        self._claude_chrome_profile = claude_chrome_profile
        self._kiro_token_db = kiro_token_db
        self._kiro_base_url = kiro_base_url
        self._cursor_state_db = cursor_state_db
        self._openai_admin_key = openai_admin_key
        self._openai_credits_purchased = openai_credits_purchased
        self._openai_credits_since = openai_credits_since
        self._openai_base_url = openai_base_url

    @QtCore.pyqtSlot()
    def start(self) -> None:
        self.run()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        steps = (
            self._run_codex,
            self._run_deepseek,
            self._run_mimo,
            self._run_claude,
            self._run_kiro,
            self._run_cursor,
            self._run_openai,
        )
        try:
            for step in steps:
                step()
        finally:
            self.finished.emit()

    def _run_codex(self) -> None:
        try:
            payload = fetch_usage_with_auth_refresh(self._auth_file, self._base_url, timeout=20.0)
            usage = parse_usage_payload(payload)
            self.loaded.emit(build_codex_card(usage))
        except CodexUsageError as exc:
            self.failed.emit(str(exc))

    def _run_deepseek(self) -> None:
        try:
            balance = fetch_balance(
                resolve_api_key(self._deepseek_api_key),
                self._deepseek_base_url,
            )
            spend = SpendTracker(self._deepseek_state_file).record(balance)
            self.deepseek_loaded.emit(
                build_deepseek_card(
                    balance=balance,
                    spend=spend,
                    baseline=self._deepseek_spent_baseline,
                )
            )
        except DeepSeekUsageError as exc:
            self.deepseek_failed.emit(str(exc))

    def _run_mimo(self) -> None:
        try:
            snapshot = load_snapshot(
                profile=self._mimo_chrome_profile,
                base_url=self._mimo_base_url,
            )
            self.mimo_loaded.emit(build_mimo_card(snapshot))
        except (MiMoUsageError, ChromeCookieError) as exc:
            self.mimo_failed.emit(str(exc))

    def _run_claude(self) -> None:
        try:
            usage = load_usage(profile=self._claude_chrome_profile)
            self.claude_loaded.emit(build_claude_card(usage))
        except ClaudeUsageError as exc:
            self.claude_failed.emit(str(exc))

    def _run_kiro(self) -> None:
        try:
            usage = load_kiro_usage(
                token_db=self._kiro_token_db,
                base_url=self._kiro_base_url,
            )
            self.kiro_loaded.emit(build_kiro_card(usage))
        except KiroUsageError as exc:
            self.kiro_failed.emit(str(exc))

    def _run_cursor(self) -> None:
        try:
            usage = load_cursor_usage(state_db=self._cursor_state_db)
            self.cursor_loaded.emit(build_cursor_card(usage))
        except CursorUsageError as exc:
            self.cursor_failed.emit(str(exc))

    def _run_openai(self) -> None:
        try:
            costs = load_costs(
                resolve_admin_key(self._openai_admin_key),
                credits_since=self._openai_credits_since,
                base_url=self._openai_base_url,
            )
            self.openai_loaded.emit(
                build_openai_card(costs, credits_purchased=self._openai_credits_purchased)
            )
        except OpenAICostsError as exc:
            self.openai_failed.emit(str(exc))
