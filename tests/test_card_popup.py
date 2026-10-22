import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from codex_widget.view_model import MetricCardModel
from codex_widget.widget import CardDetailPopup, CodexUsageWidget

MODEL = MetricCardModel(
    title="Codex",
    primary_label="Janela de 5 horas",
    primary_value="85% restantes",
    secondary_label="Janela semanal",
    secondary_value="69% restantes",
    detail_text="Assinatura Plus",
    footnote="5h 03:47 · 7d 28/10 04:34",
    available=True,
    primary_percent=85,
    secondary_percent=69,
)


def _screen(x: int, y: int, width: int, height: int) -> SimpleNamespace:
    rect = QtCore.QRect(x, y, width, height)
    return SimpleNamespace(
        name=lambda: "fake",
        geometry=lambda: rect,
        availableGeometry=lambda: rect,
    )


class PopupPlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.area = QtCore.QRect(0, 0, 1920, 1080)
        self.size = QtCore.QSize(700, 320)

    def test_prefers_the_right_side_of_the_panel(self) -> None:
        panel = QtCore.QRect(200, 300, 400, 500)

        position = CardDetailPopup.placement(panel, self.size, self.area)

        self.assertGreaterEqual(position.x(), panel.right())
        self.assertEqual(position.y(), panel.y())

    def test_falls_back_to_the_left_when_the_right_is_blocked(self) -> None:
        panel = QtCore.QRect(1400, 300, 400, 500)

        position = CardDetailPopup.placement(panel, self.size, self.area)

        self.assertLessEqual(position.x() + self.size.width(), panel.x())

    def test_uses_below_then_above_when_no_horizontal_room(self) -> None:
        narrow = QtCore.QRect(0, 0, 760, 1080)
        panel = QtCore.QRect(30, 100, 700, 300)

        below = CardDetailPopup.placement(panel, self.size, narrow)
        self.assertGreaterEqual(below.y(), panel.bottom())

        panel_low = QtCore.QRect(30, 700, 700, 300)
        above = CardDetailPopup.placement(panel_low, self.size, narrow)
        self.assertLessEqual(above.y() + self.size.height(), panel_low.y())

    def test_keeps_the_popup_inside_the_screen(self) -> None:
        panel = QtCore.QRect(1700, 1000, 200, 80)

        position = CardDetailPopup.placement(panel, self.size, self.area)

        self.assertGreaterEqual(position.x(), self.area.x())
        self.assertGreaterEqual(position.y(), self.area.y())
        self.assertLessEqual(position.x() + self.size.width(), self.area.right() + 1)
        self.assertLessEqual(position.y() + self.size.height(), self.area.bottom() + 1)


class PopupToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _widget(self, root: Path) -> CodexUsageWidget:
        widget = CodexUsageWidget(
            auth_file=root / "missing-auth.json",
            base_url="http://127.0.0.1:1",
            ui_state_file=root / "ui.json",
            deepseek_state_file=root / "deepseek.json",
        )
        self.addCleanup(widget.close)
        return widget

    def test_card_click_opens_an_enlarged_copy_and_click_closes_it(self) -> None:
        screen = _screen(0, 0, 1920, 1080)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
             patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
            widget = self._widget(Path(directory))
            widget._handle_usage_loaded(MODEL)
            widget.move(300, 200)

            widget._codex_card.clicked.emit()

            popup = widget._detail_popup
            self.assertIsNotNone(popup)
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.card_title(), "Codex")
            self.assertGreater(popup.width(), widget._codex_card.width())
            self.assertTrue(screen.availableGeometry().contains(popup.geometry()))

            popup.clicked.emit()
            self.assertFalse(popup.isVisible())

    def test_clicking_another_card_swaps_the_content(self) -> None:
        screen = _screen(0, 0, 1920, 1080)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
             patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
            widget = self._widget(Path(directory))
            widget._handle_usage_loaded(MODEL)
            widget._handle_mimo_failed("MiMo indisponível")

            widget._codex_card.clicked.emit()
            self.assertEqual(widget._detail_popup.card_title(), "Codex")

            widget._mimo_card.clicked.emit()
            self.assertTrue(widget._detail_popup.isVisible())
            self.assertEqual(widget._detail_popup.card_title(), "MiMo")

            widget._mimo_card.clicked.emit()
            self.assertFalse(widget._detail_popup.isVisible())

    def test_collapsing_the_panel_hides_the_popup(self) -> None:
        screen = _screen(0, 0, 1920, 1080)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
             patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
            widget = self._widget(Path(directory))
            widget._handle_usage_loaded(MODEL)
            widget._codex_card.clicked.emit()

            widget._toggle_collapsed()

            self.assertFalse(widget._detail_popup.isVisible())


if __name__ == "__main__":
    unittest.main()
