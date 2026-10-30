import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from codex_widget.view_model import MetricCardModel
from codex_widget.widget import CodexUsageWidget, ElidedLabel

USAGE = MetricCardModel(
    title="Claude Code",
    primary_label="Janela de 5 horas",
    primary_value="100% restantes",
    secondary_label="Janela semanal",
    secondary_value="Indisponível",
    detail_text="Assinatura Max 5x",
    footnote="5h 23/10 01:59 · 7d 23/10 00:59",
    available=True,
    primary_percent=100,
    secondary_percent=None,
)
BALANCE = MetricCardModel(
    title="OpenAI",
    primary_label="Saldo",
    primary_value="US$ 1.234,56",
    secondary_label="Gasto monitorado",
    secondary_value="US$ 20,94",
    detail_text="Hoje US$ 0,01 · Desde 01/10/2026 US$ 20,97",
    footnote="Créditos informados US$ 30,77 · custos da organização",
    available=True,
)
LONG_TEXT = "Erro ao consultar o endpoint de custos da organização " * 4


def _screen() -> SimpleNamespace:
    rect = QtCore.QRect(0, 0, 3440, 1440)
    return SimpleNamespace(
        name=lambda: "fake",
        geometry=lambda: rect,
        availableGeometry=lambda: rect,
    )


class CardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        screen = _screen()
        for name in ("primaryScreen", "screenAt"):
            patcher = patch.object(QtWidgets.QApplication, name, return_value=screen)
            patcher.start()
            self.addCleanup(patcher.stop)
        root = Path(directory.name)
        self.widget = CodexUsageWidget(
            auth_file=root / "missing-auth.json",
            base_url="http://127.0.0.1:1",
            ui_state_file=root / "ui.json",
            deepseek_state_file=root / "deepseek.json",
        )
        self.addCleanup(self.widget.close)
        self.widget._timer.stop()

    def _load_all(self, *, usage: MetricCardModel = USAGE, balance: MetricCardModel = BALANCE) -> None:
        self.widget._handle_usage_loaded(usage)
        self.widget._handle_claude_loaded(usage)
        self.widget._handle_cursor_loaded(usage)
        self.widget._handle_kiro_loaded(usage)
        self.widget._handle_deepseek_loaded(balance)
        self.widget._handle_mimo_loaded(balance)
        self.widget._handle_openai_loaded(balance)

    def _set_scale(self, scale: float) -> None:
        self.widget._scale = scale
        self.widget._apply_scale()

    def test_balance_cards_are_shorter_than_usage_cards(self) -> None:
        self._load_all()

        for usage_card in (
            self.widget._codex_card,
            self.widget._claude_card,
            self.widget._cursor_card,
            self.widget._kiro_card,
        ):
            for balance_card in (
                self.widget._deepseek_card,
                self.widget._mimo_card,
                self.widget._openai_card,
            ):
                self.assertLess(balance_card.minimumHeight(), usage_card.minimumHeight())

    def test_card_content_fits_the_card_at_every_scale(self) -> None:
        self._load_all()
        for scale in (0.45, 0.7, 1.0, 1.8):
            self._set_scale(scale)
            for card in self.widget._metric_cards():
                with self.subTest(scale=scale, card=card.labels()[0]):
                    needed = card.layout().minimumSize()
                    self.assertLessEqual(needed.height(), card.minimumHeight())
                    self.assertLessEqual(needed.width(), card.minimumWidth())

    def test_long_texts_are_elided_instead_of_growing_the_card(self) -> None:
        long_model = MetricCardModel(
            title="OpenAI",
            primary_label="Saldo",
            primary_value="US$ 9,83",
            secondary_label="Gasto no mês",
            secondary_value="US$ 20,94",
            detail_text=LONG_TEXT,
            footnote=LONG_TEXT,
            available=False,
        )
        self._load_all(balance=long_model)
        self._set_scale(0.7)
        self.widget.show()
        self.app.processEvents()

        card = self.widget._openai_card
        self.assertLessEqual(card.layout().minimumSize().height(), card.minimumHeight())
        self.assertLessEqual(card.layout().minimumSize().width(), card.minimumWidth())
        footnote = card._footnote
        self.assertNotEqual(footnote.text(), LONG_TEXT)
        self.assertTrue(footnote.text().endswith("…"))
        self.assertEqual(footnote.toolTip(), LONG_TEXT)

    def test_panel_is_tall_enough_for_all_cards(self) -> None:
        self._load_all()
        self._set_scale(1.0)

        layout = self.widget._layout
        margins = layout.contentsMargins()
        cards = self.widget._metric_cards()
        needed = (
            sum(card.minimumHeight() for card in cards)
            + layout.spacing() * (len(cards) - 1)
            + margins.top()
            + margins.bottom()
        )
        self.assertGreaterEqual(self.widget.height(), needed)

    def test_label_given_its_size_hint_shows_the_full_text(self) -> None:
        for size in (8, 9, 12, 17, 22):
            for text in ("Saldo", "Gasto monitorado", "Créditos restantes", "Assinatura Max 5x"):
                with self.subTest(size=size, text=text):
                    label = ElidedLabel()
                    self.addCleanup(label.deleteLater)
                    label.setStyleSheet(f"font-family: Inter; font-size: {size}px; font-weight: 500;")
                    label.set_full_text(text)
                    label.show()
                    label.resize(label.sizeHint())
                    self.app.processEvents()

                    self.assertEqual(label.width(), label.sizeHint().width())
                    self.assertEqual(label.text(), text)

    def test_enlarged_copy_keeps_the_compact_balance_layout(self) -> None:
        self._load_all()
        self.widget.show()

        self.widget._deepseek_card.clicked.emit()
        balance_height = self.widget._detail_popup.height()
        self.assertFalse(self.widget._detail_popup.has_bars())

        self.widget._codex_card.clicked.emit()
        self.assertTrue(self.widget._detail_popup.has_bars())
        self.assertLess(balance_height, self.widget._detail_popup.height())


if __name__ == "__main__":
    unittest.main()
