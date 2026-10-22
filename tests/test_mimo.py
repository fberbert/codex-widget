import unittest
from decimal import Decimal

from codex_widget.mimo import MiMoUsageError, parse_snapshot
from codex_widget.view_model import build_mimo_card


class ParseSnapshotTests(unittest.TestCase):
    def test_parses_balance_usage_and_recharge(self) -> None:
        snapshot = parse_snapshot(
            {
                "balance": "9.82",
                "cashBalance": "9.82",
                "giftBalance": "0.00",
                "currency": "USD",
            },
            {
                "tokenUsage": {"totalToken": 17_570_210},
                "costUsage": {"totalCost": "0.90", "currentMonthCost": "0.10"},
            },
            {"accumulatedRechargeAmount": "10.00", "currency": "USD"},
        )

        self.assertEqual(snapshot.currency, "USD")
        self.assertEqual(snapshot.balance, Decimal("9.82"))
        self.assertEqual(snapshot.cash, Decimal("9.82"))
        self.assertEqual(snapshot.gift, Decimal("0.00"))
        self.assertEqual(snapshot.recharged, Decimal("10.00"))
        self.assertEqual(snapshot.total_cost, Decimal("0.90"))
        self.assertEqual(snapshot.month_cost, Decimal("0.10"))
        self.assertEqual(snapshot.total_tokens, 17_570_210)

    def test_recharge_is_optional(self) -> None:
        snapshot = parse_snapshot(
            {"balance": "1.00", "currency": "CNY"},
            {
                "tokenUsage": {"totalToken": 10},
                "costUsage": {"totalCost": "0.5", "currentMonthCost": "0.5"},
            },
        )

        self.assertIsNone(snapshot.recharged)
        self.assertEqual(snapshot.currency, "CNY")

    def test_missing_usage_blocks_raises(self) -> None:
        with self.assertRaises(MiMoUsageError):
            parse_snapshot({"balance": "1.00"}, {"costUsage": {}})


class BuildMiMoCardTests(unittest.TestCase):
    def test_shows_balance_spend_and_tokens(self) -> None:
        snapshot = parse_snapshot(
            {"balance": "9.82", "currency": "USD"},
            {
                "tokenUsage": {"totalToken": 17_570_210},
                "costUsage": {"totalCost": "0.90", "currentMonthCost": "0.10"},
            },
            {"accumulatedRechargeAmount": "10.00"},
        )

        card = build_mimo_card(snapshot)

        self.assertEqual(card.title, "MiMo")
        self.assertEqual(card.primary_value, "US$ 9,82")
        self.assertEqual(card.secondary_label, "Gasto total")
        self.assertEqual(card.secondary_value, "US$ 0,90")
        self.assertEqual(card.detail_text, "Recarregado US$ 10,00 · Mês US$ 0,10")
        self.assertEqual(card.footnote, "Sessão do Chrome · 17,6M tokens")
        self.assertTrue(card.available)

    def test_zero_balance_is_unavailable(self) -> None:
        snapshot = parse_snapshot(
            {"balance": "0.00", "currency": "USD"},
            {
                "tokenUsage": {"totalToken": 0},
                "costUsage": {"totalCost": "0", "currentMonthCost": "0"},
            },
        )

        card = build_mimo_card(snapshot)

        self.assertFalse(card.available)
        self.assertEqual(card.footnote, "Sessão do Chrome · 0 tokens")


if __name__ == "__main__":
    unittest.main()
