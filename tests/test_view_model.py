import unittest
from datetime import datetime

from codex_widget.usage import UsageWindow
from codex_widget.view_model import build_card_models


class BuildCardModelsTests(unittest.TestCase):
    def test_builds_short_window_card_before_weekly(self) -> None:
        short = UsageWindow(used_percent=25, reset_at=1_800_000_000, window_seconds=18_000)
        weekly = UsageWindow(used_percent=7, reset_at=1_800_000_000, window_seconds=604_800)

        cards = build_card_models(short_window=short, weekly=weekly)

        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].title, "Limite de uso de 5 horas")
        self.assertEqual(cards[0].percent_remaining, 75)
        self.assertEqual(cards[1].title, "Limite de uso semanal")
        self.assertEqual(cards[1].percent_remaining, 93)
        reset_at = datetime.fromtimestamp(weekly.reset_at).astimezone()
        self.assertIn(str(reset_at.year), cards[1].reset_text)

    def test_short_window_title_follows_window_length(self) -> None:
        short = UsageWindow(used_percent=10, reset_at=1_800_000_000, window_seconds=21_600)

        cards = build_card_models(short_window=short, weekly=None)

        self.assertEqual(cards[0].title, "Limite de uso de 6 horas")

    def test_short_window_without_length_uses_generic_title(self) -> None:
        short = UsageWindow(used_percent=10, reset_at=1_800_000_000, window_seconds=None)

        cards = build_card_models(short_window=short, weekly=None)

        self.assertEqual(cards[0].title, "Limite de uso por janela")

    def test_short_window_reset_text_omits_date(self) -> None:
        short = UsageWindow(used_percent=10, reset_at=1_800_000_000, window_seconds=18_000)

        cards = build_card_models(short_window=short, weekly=None)

        reset_at = datetime.fromtimestamp(short.reset_at).astimezone()
        self.assertEqual(cards[0].reset_text, f"Redefinição {reset_at:%H:%M}")

    def test_builds_only_the_weekly_card_without_short_window(self) -> None:
        weekly = UsageWindow(used_percent=7, reset_at=1_800_000_000, window_seconds=604_800)

        cards = build_card_models(short_window=None, weekly=weekly)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].title, "Limite de uso semanal")
        self.assertEqual(cards[0].percent_remaining, 93)

    def test_missing_windows_still_render_weekly_card(self) -> None:
        cards = build_card_models(short_window=None, weekly=None)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].percent_remaining, 0)
        self.assertEqual(cards[0].reset_text, "Redefinição indisponível")


if __name__ == "__main__":
    unittest.main()
