from __future__ import annotations

from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from .usage import (
    CodexUsageError,
    extract_access_token,
    extract_account_id,
    fetch_usage,
    load_auth,
    parse_usage_payload,
)

from .view_model import UsageCardModel, build_card_models

MIN_SCALE = 0.45
MAX_SCALE = 1.8
SCALE_STEP = 0.1


class CodexUsageWidget(QtWidgets.QWidget):
    """Frameless desktop widget that shows Codex usage windows."""

    def __init__(
        self,
        *,
        auth_file,
        base_url: str,
        refresh_seconds: int = 60,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._auth_file = auth_file
        self._base_url = base_url
        self._drag_position: QtCore.QPoint | None = None
        self._thread: QtCore.QThread | None = None
        self._worker: UsageFetchWorker | None = None
        self._scale = 1.0
        self._horizontal = True

        self._cards = (
            UsageCard(parent=self),
            UsageCard(parent=self),
        )

        self._layout = QtWidgets.QHBoxLayout(self)
        for card in self._cards:
            self._layout.addWidget(card)

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
        self._quit_shortcut.activated.connect(QtWidgets.QApplication.quit)
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
            self._drag_position = None
            event.accept()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent | None) -> None:  # noqa: N802
        self._stop_worker()
        super().closeEvent(event)

    @QtCore.pyqtSlot()
    def refresh(self) -> None:
        if self._thread is not None:
            return
        self._thread = QtCore.QThread(self)
        self._worker = UsageFetchWorker(auth_file=self._auth_file, base_url=self._base_url)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.loaded.connect(self._handle_usage_loaded)
        self._worker.failed.connect(self._handle_usage_failed)
        self._worker.finished.connect(self._stop_worker)
        self._thread.start()

    @QtCore.pyqtSlot(object)
    def _handle_usage_loaded(self, cards: tuple[UsageCardModel, UsageCardModel]) -> None:
        for card, model in zip(self._cards, cards):
            card.set_model(model)

    @QtCore.pyqtSlot(str)
    def _handle_usage_failed(self, message: str) -> None:
        self._cards[0].set_error("Limite de uso de 5 horas", message)
        self._cards[1].set_error("Limite de uso semanal", message)

    def _increase_scale(self) -> None:
        self._change_scale(SCALE_STEP)

    def _decrease_scale(self) -> None:
        self._change_scale(-SCALE_STEP)

    def _change_scale(self, delta: float) -> None:
        next_scale = max(MIN_SCALE, min(MAX_SCALE, round(self._scale + delta, 2)))
        if next_scale == self._scale:
            return
        self._scale = next_scale
        self._apply_scale()

    def _toggle_orientation(self) -> None:
        self._horizontal = not self._horizontal
        self._apply_scale()

    def _apply_scale(self) -> None:
        margin_h = int(round(16 * self._scale))
        margin_v = int(round(10 * self._scale))
        spacing = int(round(20 * self._scale))
        direction = (
            QtWidgets.QBoxLayout.Direction.LeftToRight
            if self._horizontal
            else QtWidgets.QBoxLayout.Direction.TopToBottom
        )
        self._layout.setDirection(direction)
        self._layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)
        self._layout.setSpacing(spacing)
        for card in self._cards:
            card.apply_scale(self._scale)
        if self._horizontal:
            width = int(round((498 * 2 + 20 + 32) * self._scale))
            height = int(round(226 * self._scale))
        else:
            width = int(round((498 + 32) * self._scale))
            height = int(round((206 * 2 + 20 + 20) * self._scale))
        self.setMinimumSize(width, height)
        self.resize(width, height)
        self.adjustSize()

    def _stop_worker(self) -> None:
        worker = self._worker
        thread = self._thread
        self._worker = None
        self._thread = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.quit()
            thread.wait(1500)
            thread.deleteLater()

    def _place_initially(self) -> None:
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(
            geometry.x() + int((geometry.width() - self.width()) / 2),
            geometry.y() + 24,
        )


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


class UsageFetchWorker(QtCore.QObject):
    loaded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, *, auth_file, base_url: str) -> None:
        super().__init__()
        self._auth_file = auth_file
        self._base_url = base_url

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            auth = load_auth(self._auth_file)
            access_token = extract_access_token(auth)
            account_id = extract_account_id(auth)
            payload = fetch_usage(access_token, account_id, self._base_url, timeout=20.0)
            usage = parse_usage_payload(payload)
            self.loaded.emit(build_card_models(five_hour=usage.five_hour, weekly=usage.weekly))
        except CodexUsageError as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
