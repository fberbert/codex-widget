import unittest
from datetime import datetime
from decimal import Decimal

from codex_widget.claude import ClaudeUsage, ClaudeWindow
from codex_widget.kiro import KiroUsage
from codex_widget.usage import CodexUsage, UsageWindow
from codex_widget.view_model import build_claude_card, build_codex_card, build_kiro_card


def _usage(
    short_used: float | None = 25,
    weekly_used: float | None = 7,
    plan: str | None = "plus",
) -> CodexUsage:
    return CodexUsage(
        short_window=(
            UsageWindow(used_percent=short_used, reset_at=1_800_000_000, window_seconds=18_000)
            if short_used is not None
            else None
        ),
        weekly=(
            UsageWindow(used_percent=weekly_used, reset_at=1_800_050_000, window_seconds=604_800)
            if weekly_used is not None
            else None
        ),
        plan_type=plan,
    )


class BuildCodexCardTests(unittest.TestCase):
    def test_merges_both_windows_into_one_card_with_bars(self) -> None:
        card = build_codex_card(_usage())

        self.assertEqual(card.title, "Codex")
        self.assertEqual(card.primary_label, "Janela de 5 horas")
        self.assertEqual(card.primary_value, "75% restantes")
        self.assertEqual(card.primary_percent, 75)
        self.assertEqual(card.secondary_label, "Janela semanal")
        self.assertEqual(card.secondary_value, "93% restantes")
        self.assertEqual(card.secondary_percent, 93)
        self.assertEqual(card.detail_text, "Assinatura Plus")
        self.assertTrue(card.available)

        short_reset = datetime.fromtimestamp(1_800_000_000).astimezone()
        weekly_reset = datetime.fromtimestamp(1_800_050_000).astimezone()
        self.assertEqual(
            card.footnote,
            f"5h {short_reset:%H:%M} · 7d {weekly_reset:%d/%m %H:%M}",
        )

    def test_window_label_follows_the_window_length(self) -> None:
        usage = CodexUsage(
            short_window=UsageWindow(used_percent=10, reset_at=None, window_seconds=21_600),
            weekly=None,
            plan_type=None,
        )

        card = build_codex_card(usage)

        self.assertEqual(card.primary_label, "Janela de 6 horas")
        self.assertEqual(card.detail_text, "Limites de uso do Codex")

    def test_missing_windows_are_reported_without_bars(self) -> None:
        card = build_codex_card(_usage(short_used=None, weekly_used=None, plan=None))

        self.assertEqual(card.primary_value, "Indisponível")
        self.assertIsNone(card.primary_percent)
        self.assertEqual(card.secondary_value, "Indisponível")
        self.assertIsNone(card.secondary_percent)
        self.assertEqual(card.footnote, "Redefinição indisponível")
        self.assertFalse(card.available)


class ClaudeCardBarTests(unittest.TestCase):
    def test_claude_card_exposes_percentages_for_the_bars(self) -> None:
        usage = ClaudeUsage(
            five_hour=ClaudeWindow(utilization=19.0, resets_at=1_800_000_000),
            seven_day=ClaudeWindow(utilization=5.0, resets_at=1_800_050_000),
            plan="Max 5x",
        )

        card = build_claude_card(usage)

        self.assertEqual(card.primary_percent, 81)
        self.assertEqual(card.secondary_percent, 95)

    def test_missing_claude_window_has_no_bar(self) -> None:
        usage = ClaudeUsage(
            five_hour=None,
            seven_day=ClaudeWindow(utilization=100.0, resets_at=None),
            plan=None,
        )

        card = build_claude_card(usage)

        self.assertIsNone(card.primary_percent)
        self.assertEqual(card.secondary_percent, 0)


class KiroCardTests(unittest.TestCase):
    def _usage(self, **overrides: object) -> KiroUsage:
        values: dict[str, object] = {
            "plan": "KIRO POWER",
            "resource": "Credits",
            "used": Decimal("176.64"),
            "limit": Decimal("10000"),
            "overage_enabled": False,
            "overage_used": Decimal("0"),
            "overage_cap": Decimal("2500"),
            "overage_charges": Decimal("0"),
            "currency": "USD",
            "next_reset": 1_800_000_000,
        }
        values.update(overrides)
        return KiroUsage(**values)  # type: ignore[arg-type]

    def test_shows_remaining_credits_with_a_bar(self) -> None:
        card = build_kiro_card(self._usage())

        self.assertEqual(card.title, "Kiro")
        self.assertEqual(card.primary_label, "Créditos restantes")
        self.assertEqual(card.primary_value, "98% restantes")
        self.assertEqual(card.primary_percent, 98)
        self.assertEqual(card.secondary_label, "Créditos usados")
        self.assertEqual(card.secondary_value, "176,6 de 10.000")
        self.assertIsNone(card.secondary_percent)
        self.assertEqual(card.detail_text, "Plano KIRO POWER")
        self.assertTrue(card.available)

        reset = datetime.fromtimestamp(1_800_000_000).astimezone()
        self.assertEqual(card.footnote, f"Renova em {reset:%d/%m/%Y} · excedente desativado")

    def test_reports_exhausted_credits_as_unavailable(self) -> None:
        card = build_kiro_card(self._usage(used=Decimal("10000")))

        self.assertEqual(card.primary_value, "0% restantes")
        self.assertEqual(card.primary_percent, 0)
        self.assertFalse(card.available)

    def test_describes_the_overage_when_enabled(self) -> None:
        card = build_kiro_card(
            self._usage(
                overage_enabled=True,
                overage_used=Decimal("12.5"),
                overage_charges=Decimal("0.5"),
            )
        )

        self.assertIn("excedente 12,5 de 2.500", card.footnote)
        self.assertIn("US$ 0,50", card.footnote)


if __name__ == "__main__":
    unittest.main()
