import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from codex_widget.deepseek import (
    DeepSeekBalance,
    DeepSeekUsageError,
    SpendTracker,
    format_money,
    parse_balance_payload,
)
from codex_widget.view_model import build_deepseek_card


def _balance(total: str, currency: str = "USD", available: bool = True) -> DeepSeekBalance:
    value = Decimal(total)
    return DeepSeekBalance(
        currency=currency,
        total=value,
        granted=Decimal("0"),
        topped_up=value,
        is_available=available,
    )


class ParseBalancePayloadTests(unittest.TestCase):
    def test_parses_balance_infos(self) -> None:
        payload = {
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "USD",
                    "total_balance": "4.19",
                    "granted_balance": "0.00",
                    "topped_up_balance": "4.19",
                }
            ],
        }

        balance = parse_balance_payload(payload)

        self.assertEqual(balance.currency, "USD")
        self.assertEqual(balance.total, Decimal("4.19"))
        self.assertEqual(balance.granted, Decimal("0.00"))
        self.assertEqual(balance.topped_up, Decimal("4.19"))
        self.assertTrue(balance.is_available)

    def test_prefers_usd_when_multiple_currencies(self) -> None:
        payload = {
            "is_available": False,
            "balance_infos": [
                {"currency": "CNY", "total_balance": "30.00"},
                {"currency": "USD", "total_balance": "4.19"},
            ],
        }

        balance = parse_balance_payload(payload)

        self.assertEqual(balance.currency, "USD")
        self.assertEqual(balance.total, Decimal("4.19"))
        self.assertFalse(balance.is_available)

    def test_missing_infos_raises(self) -> None:
        with self.assertRaises(DeepSeekUsageError):
            parse_balance_payload({"is_available": True, "balance_infos": []})


class FormatMoneyTests(unittest.TestCase):
    def test_formats_usd_with_brazilian_separators(self) -> None:
        self.assertEqual(format_money(Decimal("1234.56"), "USD"), "US$ 1.234,56")

    def test_formats_other_currencies(self) -> None:
        self.assertEqual(format_money(Decimal("0"), "CNY"), "CN¥ 0,00")
        self.assertEqual(format_money(Decimal("9.9"), "BRL"), "R$ 9,90")


class SpendTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "deepseek-spend.json"

    def test_first_observation_starts_tracking_without_spend(self) -> None:
        spend = SpendTracker(self._path).record(_balance("10.00"), now=1_000)

        self.assertEqual(spend.spent, Decimal("0"))
        self.assertEqual(spend.topups, Decimal("0"))
        self.assertEqual(spend.since, 1_000)
        self.assertFalse(spend.tracking)

    def test_balance_drop_counts_as_spend(self) -> None:
        tracker = SpendTracker(self._path)
        tracker.record(_balance("10.00"), now=1_000)

        spend = tracker.record(_balance("7.50"), now=2_000)

        self.assertEqual(spend.spent, Decimal("2.50"))
        self.assertTrue(spend.tracking)

    def test_balance_increase_counts_as_topup_not_negative_spend(self) -> None:
        tracker = SpendTracker(self._path)
        tracker.record(_balance("10.00"), now=1_000)

        spend = tracker.record(_balance("12.00"), now=2_000)

        self.assertEqual(spend.spent, Decimal("0"))
        self.assertEqual(spend.topups, Decimal("2.00"))

    def test_spend_survives_new_tracker_instance(self) -> None:
        SpendTracker(self._path).record(_balance("10.00"), now=1_000)
        SpendTracker(self._path).record(_balance("6.00"), now=2_000)

        spend = SpendTracker(self._path).record(_balance("5.00"), now=3_000)

        self.assertEqual(spend.spent, Decimal("5.00"))
        self.assertEqual(spend.since, 1_000)

    def test_currency_change_resets_ledger(self) -> None:
        tracker = SpendTracker(self._path)
        tracker.record(_balance("10.00"), now=1_000)

        spend = tracker.record(_balance("10.00", currency="CNY"), now=2_000)

        self.assertEqual(spend.spent, Decimal("0"))
        self.assertEqual(spend.since, 2_000)
        self.assertFalse(spend.tracking)

    def test_corrupt_state_file_is_ignored(self) -> None:
        self._path.write_text("{not json", encoding="utf-8")

        spend = SpendTracker(self._path).record(_balance("10.00"), now=1_000)

        self.assertEqual(spend.spent, Decimal("0"))
        self.assertFalse(spend.tracking)
        state = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_total"], "10.00")


class BuildDeepSeekCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._state_file = Path(self._directory.name) / "state.json"

    def test_shows_balance_and_tracked_spend(self) -> None:
        tracker = SpendTracker(self._state_file)
        tracker.record(_balance("10.00"), now=1_000)
        spend = tracker.record(_balance("7.50"), now=2_000)

        card = build_deepseek_card(balance=_balance("7.50"), spend=spend)

        self.assertEqual(card.primary_value, "US$ 7,50")
        self.assertEqual(card.secondary_value, "US$ 2,50")
        self.assertEqual(card.secondary_label, "Gasto monitorado")
        self.assertEqual(card.detail_text, "Concedido US$ 0,00 · Recarregado US$ 7,50")
        self.assertIn("Monitorado desde", card.footnote)
        self.assertTrue(card.available)

    def test_baseline_promotes_spend_label_to_total(self) -> None:
        spend = SpendTracker(self._state_file).record(_balance("10.00"), now=1_000)

        card = build_deepseek_card(
            balance=_balance("10.00"),
            spend=spend,
            baseline=Decimal("5.50"),
        )

        self.assertEqual(card.secondary_value, "US$ 5,50")
        self.assertEqual(card.secondary_label, "Gasto total")

    def test_unavailable_account_changes_footnote(self) -> None:
        spend = SpendTracker(self._state_file).record(_balance("0.00"), now=1_000)

        card = build_deepseek_card(balance=_balance("0.00", available=False), spend=spend)

        self.assertEqual(card.footnote, "Conta sem saldo disponível para uso")
        self.assertFalse(card.available)


if __name__ == "__main__":
    unittest.main()
