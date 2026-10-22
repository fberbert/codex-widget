import unittest
from datetime import datetime

from codex_widget.claude import ClaudeUsageError, parse_usage
from codex_widget.view_model import build_claude_card

RESET_5H = "2026-10-22T03:49:59.549914+00:00"
RESET_7D = "2026-10-23T03:59:59.549930+00:00"


class ParseUsageTests(unittest.TestCase):
    def test_parses_windows_and_reset_times(self) -> None:
        usage = parse_usage(
            {
                "five_hour": {"utilization": 2.0, "resets_at": RESET_5H},
                "seven_day": {"utilization": 3.0, "resets_at": RESET_7D},
            },
            plan="Max 5x",
        )

        self.assertEqual(usage.five_hour.utilization, 2.0)
        self.assertEqual(usage.seven_day.utilization, 3.0)
        self.assertEqual(
            usage.five_hour.resets_at,
            int(datetime.fromisoformat(RESET_5H).timestamp()),
        )
        self.assertEqual(usage.plan, "Max 5x")

    def test_null_windows_are_ignored(self) -> None:
        usage = parse_usage(
            {"five_hour": None, "seven_day": {"utilization": 10, "resets_at": RESET_7D}}
        )

        self.assertIsNone(usage.five_hour)
        self.assertIsNotNone(usage.seven_day)

    def test_payload_without_windows_raises(self) -> None:
        with self.assertRaises(ClaudeUsageError):
            parse_usage({"five_hour": None, "seven_day": None})


class BuildClaudeCardTests(unittest.TestCase):
    def test_builds_remaining_percentages_and_plan(self) -> None:
        usage = parse_usage(
            {
                "five_hour": {"utilization": 2.0, "resets_at": RESET_5H},
                "seven_day": {"utilization": 3.0, "resets_at": RESET_7D},
            },
            plan="Max 5x",
        )

        card = build_claude_card(usage)

        self.assertEqual(card.title, "Claude Code")
        self.assertEqual(card.primary_label, "Janela de 5 horas")
        self.assertEqual(card.primary_value, "98% restantes")
        self.assertEqual(card.secondary_label, "Janela semanal")
        self.assertEqual(card.secondary_value, "97% restantes")
        self.assertEqual(card.detail_text, "Assinatura Max 5x")
        self.assertTrue(card.available)

        reset_5h = datetime.fromtimestamp(usage.five_hour.resets_at).astimezone()
        self.assertIn(f"5h {reset_5h:%d/%m %H:%M}", card.footnote)

    def test_missing_window_shows_indisponivel(self) -> None:
        usage = parse_usage({"seven_day": {"utilization": 100, "resets_at": RESET_7D}})

        card = build_claude_card(usage)

        self.assertEqual(card.primary_value, "Indisponível")
        self.assertEqual(card.secondary_value, "0% restantes")




if __name__ == "__main__":
    unittest.main()
