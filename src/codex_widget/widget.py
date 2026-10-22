from __future__ import annotations

from decimal import Decimal
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from .chrome_cookies import ChromeCookieError
from .claude import ClaudeUsageError, load_usage
from .deepseek import (
    DEFAULT_BASE_URL as DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_STATE_FILE as DEFAULT_DEEPSEEK_STATE_FILE,
    DeepSeekUsageError,
    SpendTracker,
    fetch_balance,
    resolve_api_key,
)
from .mimo import (
    DEFAULT_BASE_URL as DEFAULT_MIMO_BASE_URL,
    MiMoUsageError,
    load_snapshot,
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
    UsageCardModel,
    build_card_models,
    build_claude_card,
    build_deepseek_card,
    build_mimo_card,
)

MIN_SCALE = 0.45
MAX_SCALE = 1.8
SCALE_STEP = 0.1
THREAD_JOIN_TIMEOUT_MS = 25_000


def _clamp_scale(value: float) -> float:
    return max(MIN_SCALE, min(MAX_SCALE, round(float(value), 2)))


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
        self._codex_models: tuple[UsageCardModel, ...] = ()
        self._deepseek_model: MetricCardModel | None = None
        self._mimo_model: MetricCardModel | None = None
        self._claude_model: MetricCardModel | None = None

        self._cards: tuple[UsageCard, ...] = ()
        self._layout = QtWidgets.QHBoxLayout(self)
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
        self._claude_card = MetricCard(
            title="Claude Code",
            primary_label="Janela de 5 horas",
            secondary_label="Janela semanal",
            parent=self,
        )
        self._layout.addWidget(self._deepseek_card)
        self._layout.addWidget(self._mimo_card)
        self._layout.addWidget(self._claude_card)
        self._deepseek_card.set_loading()
        self._mimo_card.set_loading()
        self._claude_card.set_loading()

        self._badge = CollapsedBadge(parent=self)
        self._layout.addWidget(self._badge)
        self._badge.clicked.connect(self._toggle_collapsed)
        self._badge.moved.connect(self._save_ui_state)
        self._badge.hide()

        self._toggle_button = QtWidgets.QToolButton(self)
        self._toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._toggle_button.setAutoRaise(True)
        self._toggle_button.setObjectName("toggle")
        self._toggle_button.clicked.connect(self._toggle_collapsed)
        self._update_toggle_button()

        self._rebuild_cards(
            (
                UsageCardModel(
                    title="Limite de uso semanal",
                    percent_remaining=0,
                    reset_text="",
                ),
            )
        )
        self._cards[0].set_loading("Limite de uso semanal")

        self.setWindowTitle("Codex Usage")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Window
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")

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

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(max(15, refresh_seconds) * 1000)
        self._timer.timeout.connect(self.refresh)

        self._apply_scale()
        QtCore.QTimer.singleShot(0, self._place_initially)
        QtCore.QTimer.singleShot(0, self.refresh)
        self._timer.start()

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.buttons() & QtCore.Qt.MouseButton.LeftButton and self._drag_position is not None:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event and event.button() == QtCore.Qt.MouseButton.LeftButton:
            was_dragging = self._drag_position is not None
            self._drag_position = None
            event.accept()
            if was_dragging:
                self._save_ui_state()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent | None) -> None:  # noqa: N802
        if self.isVisible():
            self._save_ui_state()
        self._closing = True
        self._timer.stop()
        self._stop_worker()
        self._join_retired_threads()
        super().closeEvent(event)

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
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.loaded.connect(self._handle_usage_loaded)
        self._worker.failed.connect(self._handle_usage_failed)
        self._worker.deepseek_loaded.connect(self._handle_deepseek_loaded)
        self._worker.deepseek_failed.connect(self._handle_deepseek_failed)
        self._worker.mimo_loaded.connect(self._handle_mimo_loaded)
        self._worker.mimo_failed.connect(self._handle_mimo_failed)
        self._worker.claude_loaded.connect(self._handle_claude_loaded)
        self._worker.claude_failed.connect(self._handle_claude_failed)
        self._worker.finished.connect(self._stop_worker)
        self._thread.start()

    @QtCore.pyqtSlot(object)
    def _handle_usage_loaded(self, models: tuple[UsageCardModel, ...]) -> None:
        self._codex_models = models
        if len(models) != len(self._cards):
            self._rebuild_cards(models)
            self._refit_if_needed()
            self._update_badge()
            return
        for card, model in zip(self._cards, models):
            card.set_model(model)
        self._apply_scale()
        self._refit_if_needed()
        self._update_badge()

    @QtCore.pyqtSlot(str)
    def _handle_usage_failed(self, message: str) -> None:
        self._show_error(message)

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

    def _show_error(self, message: str) -> None:
        self._drop_cards()
        card = UsageCard(parent=self)
        self._cards = (card,)
        self._layout.addWidget(card)
        card.set_error("Limites do Codex", message)
        self._apply_scale()

    def _rebuild_cards(self, models: tuple[UsageCardModel, ...]) -> None:
        self._drop_cards()
        cards = tuple(UsageCard(parent=self) for _ in models)
        self._cards = cards
        for index, (card, model) in enumerate(zip(cards, models)):
            self._layout.insertWidget(index, card)
            card.set_model(model)
        self._apply_scale()

    def _drop_cards(self) -> None:
        for card in self._cards:
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards = ()

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
        bottom_right = self._bottom_right()
        if collapsing:
            self._expanded_position = self.pos()
            badge_unmoved = True
        else:
            badge_unmoved = (
                self._collapsed_position is not None
                and self.pos() == self._collapsed_position
            )

        self._collapsed = not self._collapsed
        self._update_toggle_button()
        self._apply_scale()
        if not self._collapsed:
            self._refit_if_needed(source_screen)

        if self._collapsed:
            self._anchor_bottom_right(bottom_right, source_screen)
            self._collapsed_position = self.pos()
        elif badge_unmoved and self._expanded_position is not None:
            self.move(self._expanded_position)
            self._clamp_to_screen(source_screen)
        else:
            self._anchor_bottom_right(bottom_right, source_screen)
        self._save_ui_state()

    def _bottom_right(self) -> QtCore.QPoint:
        position = self.pos()
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
        self.move(bottom_right.x() - self.width(), bottom_right.y() - self.height())
        self._clamp_to_screen(screen)

    def _clamp_to_screen(self, screen: QtGui.QScreen | None) -> None:
        if screen is None:
            return
        area = screen.availableGeometry()
        max_x = max(area.x(), area.x() + area.width() - self.width())
        max_y = max(area.y(), area.y() + area.height() - self.height())
        self.move(
            min(max(self.pos().x(), area.x()), max_x),
            min(max(self.pos().y(), area.y()), max_y),
        )

    def _update_toggle_button(self) -> None:
        self._toggle_button.setText("+" if self._collapsed else "–")
        self._toggle_button.setToolTip("Expandir" if self._collapsed else "Recolher")

    def _save_ui_state(self) -> None:
        screen = self._source_screen()
        area = screen.availableGeometry() if screen is not None else None
        position = self.pos()
        save_ui_state(
            WidgetUiState(
                horizontal=self._horizontal,
                orientation_locked=self._orientation_locked,
                scale=self._scale if self._scale_saved else None,
                collapsed=self._collapsed,
                screen_name=screen.name() if screen is not None else None,
                offset_x=position.x() - area.x() if area is not None else None,
                offset_y=position.y() - area.y() if area is not None else None,
            ),
            self._ui_state_file,
        )

    def _effective_horizontal(self) -> bool:
        if not self._horizontal:
            return False
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return True
        card_count = len(self._cards) + 3
        needed = (498 * card_count + 20 * (card_count - 1) + 32) * self._scale
        if self._orientation_locked:
            return True
        return needed <= screen.availableGeometry().width() - 20

    def _apply_scale(self) -> None:
        margin_h = int(round(16 * self._scale))
        margin_v = int(round(10 * self._scale))
        spacing = int(round(20 * self._scale))
        self._layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)

        if self._collapsed:
            self._layout.setDirection(QtWidgets.QBoxLayout.Direction.TopToBottom)
            for card in (*self._cards, self._deepseek_card, self._mimo_card, self._claude_card):
                card.apply_scale(self._scale)
                card.hide()
            self._badge.apply_scale(self._scale)
            self._badge.show()
            self._toggle_button.hide()
            width = int(round((CollapsedBadge.MIN_WIDTH + 32) * self._scale))
            height = int(round((CollapsedBadge.MIN_HEIGHT + 20) * self._scale))
            self.setMinimumSize(width, height)
            self.resize(width, height)
            self.adjustSize()
            return

        self._badge.hide()
        self._toggle_button.show()
        horizontal = self._effective_horizontal()
        direction = (
            QtWidgets.QBoxLayout.Direction.LeftToRight
            if horizontal
            else QtWidgets.QBoxLayout.Direction.TopToBottom
        )
        self._layout.setDirection(direction)
        self._layout.setSpacing(spacing)
        for card in (*self._cards, self._deepseek_card, self._mimo_card, self._claude_card):
            card.apply_scale(self._scale)
            card.show()
        card_count = len(self._cards) + 3
        if horizontal:
            width = int(round((498 * card_count + 20 * (card_count - 1) + 32) * self._scale))
            height = int(round(226 * self._scale))
        else:
            width = int(round((498 + 32) * self._scale))
            height = int(round((206 * card_count + 20 * (card_count - 1) + 20) * self._scale))
        self.setMinimumSize(width, height)
        self.resize(width, height)
        self.adjustSize()
        self._position_toggle_button()

    def _position_toggle_button(self) -> None:
        size = max(22, int(round(28 * self._scale)))
        margin = max(6, int(round(9 * self._scale)))
        if self._toggle_button.width() != size:
            self._toggle_button.setFixedSize(size, size)
            font_size = max(12, int(round(16 * self._scale)))
            radius = size // 2
            self._toggle_button.setStyleSheet(
                f"""
                QToolButton#toggle {{
                    background-color: #3a3a3a;
                    border: 1px solid #5a5a5a;
                    border-radius: {radius}px;
                    color: #ffffff;
                    font-size: {font_size}px;
                    font-weight: 700;
                }}
                QToolButton#toggle:hover {{
                    background-color: #4a4a4a;
                    border-color: #7a7a7a;
                }}
                """
            )
        self._toggle_button.move(
            max(margin, self.width() - size - margin),
            max(margin, self.height() - size - margin),
        )
        self._toggle_button.raise_()

    def _update_badge(self) -> None:
        codex = f"{self._codex_models[0].percent_remaining}%" if self._codex_models else "—"
        claude = (
            self._claude_model.primary_value.replace(" restantes", "")
            if self._claude_model is not None
            else "—"
        )
        deepseek = self._deepseek_model.primary_value if self._deepseek_model is not None else "—"
        mimo = self._mimo_model.primary_value if self._mimo_model is not None else "—"
        self._badge.set_metrics(codex=codex, claude=claude, deepseek=deepseek, mimo=mimo)

    def resizeEvent(self, event: QtGui.QResizeEvent | None) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_toggle_button"):
            self._position_toggle_button()

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

    def _refit_if_needed(self, screen: QtGui.QScreen | None = None) -> None:
        if self._scale_saved or self._collapsed:
            return
        screen = screen or self._source_screen()
        if screen is None:
            return
        self._fit_scale_to_screen(screen.availableGeometry())

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
            self.move(geometry.topLeft() + self._saved_offset)
            self._clamp_to_screen(screen)
        else:
            self.move(
                geometry.x() + int((geometry.width() - self.width()) / 2),
                geometry.y() + 24,
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
            and self.height() <= geometry.height() - 24
        )


class CollapsedBadge(QtWidgets.QFrame):
    MIN_WIDTH = 300
    MIN_HEIGHT = 132

    clicked = QtCore.pyqtSignal()
    moved = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._values: dict[str, QtWidgets.QLabel] = {}
        self._drag_offset: QtCore.QPoint | None = None
        self._press_position: QtCore.QPoint | None = None
        self._moved = False
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        grid = QtWidgets.QGridLayout(self)
        for index, key in enumerate(("Codex", "Claude", "DeepSeek", "MiMo")):
            row, column = divmod(index, 2)
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(1)
            caption = QtWidgets.QLabel(key)
            caption.setObjectName("caption")
            value = QtWidgets.QLabel("—")
            value.setObjectName("value")
            box.addWidget(caption)
            box.addWidget(value)
            grid.addLayout(box, row, column)
            self._values[key] = value

        self.apply_scale(1.0)

    def set_metrics(
        self,
        *,
        codex: str,
        claude: str,
        deepseek: str,
        mimo: str,
    ) -> None:
        self._values["Codex"].setText(codex)
        self._values["Claude"].setText(claude)
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
            self._drag_offset = self._press_position - self.window().frameGeometry().topLeft()
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
                self.window().move(current - self._drag_offset)
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


class UsageCard(QtWidgets.QFrame):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._model: UsageCardModel | None = None
        self._title = QtWidgets.QLabel()
        self._percent = QtWidgets.QLabel()
        self._reset = QtWidgets.QLabel()
        self._bar = QtWidgets.QProgressBar()

        self._title.setObjectName("title")
        self._percent.setObjectName("percent")
        self._reset.setObjectName("reset")
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._percent)
        layout.addSpacing(2)
        layout.addWidget(self._bar)
        layout.addStretch(1)
        layout.addWidget(self._reset)

        self.apply_scale(1.0)

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        layout = self.layout()
        if isinstance(layout, QtWidgets.QVBoxLayout):
            layout.setContentsMargins(
                int(round(32 * scale)),
                int(round(28 * scale)),
                int(round(32 * scale)),
                int(round(28 * scale)),
            )
            layout.setSpacing(int(round(18 * scale)))
        self._bar.setFixedHeight(int(round(15 * scale)))
        self.setMinimumSize(int(round(498 * scale)), int(round(206 * scale)))
        if self._model is not None:
            self._set_percent_text(self._model.percent_remaining)
        self._apply_style()

    def set_model(self, model: UsageCardModel) -> None:
        self._model = model
        self._title.setText(model.title)
        self._set_percent_text(model.percent_remaining)
        self._bar.setValue(model.percent_remaining)
        self._reset.setText(model.reset_text)
        self._apply_style()

    def set_loading(self, title: str) -> None:
        self._model = None
        self._title.setText(title)
        self._percent.setText("—")
        self._bar.setValue(0)
        self._reset.setText("Atualizando…")
        self._apply_style()

    def set_error(self, title: str, message: str) -> None:
        self._title.setText(title)
        self._percent.setText("Uso indisponível")
        self._bar.setValue(0)
        self._reset.setText(message[:96])
        self._apply_style()

    def _set_percent_text(self, percent_remaining: int) -> None:
        percent_size = int(round(31 * self._scale))
        self._percent.setText(
            f'<span style="font-size: {percent_size}px; font-weight: 800;">'
            f"{percent_remaining}%</span> restantes"
        )

    def _apply_style(self) -> None:
        radius = int(round(28 * self._scale))
        title_size = int(round(18 * self._scale))
        percent_size = int(round(22 * self._scale))
        reset_size = int(round(15 * self._scale))
        bar_radius = max(1, int(round(7 * self._scale)))
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
            QLabel#percent {{
                color: #ffffff;
                font-size: {percent_size}px;
                font-weight: 500;
            }}
            QLabel#reset {{
                color: #bcc6d5;
                font-size: {reset_size}px;
                font-weight: 500;
            }}
            QProgressBar {{
                background-color: #e7ebf2;
                border: none;
                border-radius: {bar_radius}px;
            }}
            QProgressBar::chunk {{
                background-color: #22c55e;
                border-radius: {bar_radius}px;
            }}
            """
        )


class MetricCard(QtWidgets.QFrame):
    def __init__(
        self,
        *,
        title: str,
        primary_label: str,
        secondary_label: str,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._default_title = title
        self._default_primary_label = primary_label
        self._default_secondary_label = secondary_label
        self._title = QtWidgets.QLabel(title)
        self._status = QtWidgets.QLabel()
        self._primary_caption = QtWidgets.QLabel(primary_label)
        self._primary_value = QtWidgets.QLabel("—")
        self._secondary_caption = QtWidgets.QLabel(secondary_label)
        self._secondary_value = QtWidgets.QLabel("—")
        self._detail = QtWidgets.QLabel()
        self._footnote = QtWidgets.QLabel()

        self._title.setObjectName("title")
        self._status.setObjectName("status")
        for caption in (self._primary_caption, self._secondary_caption):
            caption.setObjectName("caption")
        for value in (self._primary_value, self._secondary_value):
            value.setObjectName("metric")
        self._detail.setObjectName("detail")
        self._footnote.setObjectName("footnote")
        self._detail.setWordWrap(True)
        self._footnote.setWordWrap(True)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._status)

        metrics = QtWidgets.QHBoxLayout()
        for caption, value in (
            (self._primary_caption, self._primary_value),
            (self._secondary_caption, self._secondary_value),
        ):
            column = QtWidgets.QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(caption)
            column.addWidget(value)
            metrics.addLayout(column, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(header)
        layout.addSpacing(6)
        layout.addLayout(metrics)
        layout.addStretch(1)
        layout.addWidget(self._detail)
        layout.addWidget(self._footnote)

        self.apply_scale(1.0)

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        layout = self.layout()
        if isinstance(layout, QtWidgets.QVBoxLayout):
            layout.setContentsMargins(
                int(round(32 * scale)),
                int(round(24 * scale)),
                int(round(32 * scale)),
                int(round(24 * scale)),
            )
            layout.setSpacing(int(round(10 * scale)))
        self.setMinimumSize(int(round(498 * scale)), int(round(206 * scale)))
        self._apply_style()

    def set_model(self, model: MetricCardModel) -> None:
        self._title.setText(model.title)
        self._primary_caption.setText(model.primary_label)
        self._primary_value.setText(model.primary_value)
        self._secondary_caption.setText(model.secondary_label)
        self._secondary_value.setText(model.secondary_value)
        self._detail.setText(model.detail_text)
        self._footnote.setText(model.footnote)
        self._set_status(model.available)
        self._apply_style()

    def set_loading(self) -> None:
        self._title.setText(self._default_title)
        self._primary_caption.setText(self._default_primary_label)
        self._secondary_caption.setText(self._default_secondary_label)
        self._primary_value.setText("—")
        self._secondary_value.setText("—")
        self._detail.setText("")
        self._footnote.setText("Atualizando…")
        self._status.setText("")
        self._apply_style()

    def set_error(self, message: str) -> None:
        self._title.setText(self._default_title)
        self._primary_caption.setText(self._default_primary_label)
        self._secondary_caption.setText(self._default_secondary_label)
        self._primary_value.setText("—")
        self._secondary_value.setText("—")
        self._detail.setText("")
        self._footnote.setText(message[:120])
        self._set_status(False)
        self._apply_style()

    def _set_status(self, available: bool) -> None:
        if available:
            self._status.setText('<span style="color:#22c55e;">● disponível</span>')
        else:
            self._status.setText('<span style="color:#f59e0b;">● indisponível</span>')

    def _apply_style(self) -> None:
        radius = int(round(28 * self._scale))
        title_size = int(round(18 * self._scale))
        caption_size = int(round(13 * self._scale))
        metric_size = int(round(26 * self._scale))
        detail_size = int(round(13 * self._scale))
        footnote_size = int(round(12 * self._scale))
        status_size = int(round(12 * self._scale))
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
            """
        )


class UsageFetchWorker(QtCore.QObject):
    loaded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    deepseek_loaded = QtCore.pyqtSignal(object)
    deepseek_failed = QtCore.pyqtSignal(str)
    mimo_loaded = QtCore.pyqtSignal(object)
    mimo_failed = QtCore.pyqtSignal(str)
    claude_loaded = QtCore.pyqtSignal(object)
    claude_failed = QtCore.pyqtSignal(str)
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

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._run_codex()
        finally:
            try:
                self._run_deepseek()
            finally:
                try:
                    self._run_mimo()
                finally:
                    try:
                        self._run_claude()
                    finally:
                        self.finished.emit()

    def _run_codex(self) -> None:
        try:
            payload = fetch_usage_with_auth_refresh(self._auth_file, self._base_url, timeout=20.0)
            usage = parse_usage_payload(payload)
            self.loaded.emit(build_card_models(short_window=usage.short_window, weekly=usage.weekly))
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
