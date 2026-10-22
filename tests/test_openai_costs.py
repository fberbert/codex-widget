import json
import threading
import unittest
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from codex_widget.openai_costs import OpenAICostsError, load_costs, summarize
from codex_widget.view_model import build_openai_card

DAY = 86_400


def _bucket(start: int, value: str) -> dict[str, object]:
    return {
        "object": "bucket",
        "start_time": start,
        "end_time": start + DAY,
        "results": [
            {
                "object": "organization.costs.result",
                "amount": {"value": float(value), "currency": "usd"},
            }
        ],
    }


class SummarizeTests(unittest.TestCase):
    def test_splits_today_month_and_tracked_windows(self) -> None:
        tracked_since, month_start, today_start = 1_000, 5_000, 9_000
        costs = summarize(
            [
                _bucket(500, "7.00"),  # before the tracked window
                _bucket(tracked_since, "1.50"),
                _bucket(month_start, "2.25"),
                _bucket(today_start, "0.75"),
            ],
            today_start=today_start,
            month_start=month_start,
            tracked_since=tracked_since,
        )

        self.assertEqual(costs.today, Decimal("0.75"))
        self.assertEqual(costs.month, Decimal("3.00"))
        self.assertEqual(costs.tracked, Decimal("4.50"))
        self.assertEqual(costs.currency, "USD")


class LoadCostsTests(unittest.TestCase):
    def test_sums_every_page_of_daily_buckets(self) -> None:
        now = int(datetime(2026, 10, 21, 20, 0).astimezone().timestamp())
        month_start = int(datetime(2026, 10, 1).astimezone().timestamp())
        today_start = int(datetime(2026, 10, 21).astimezone().timestamp())
        requests: list[dict[str, list[str]]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                requests.append(query)
                if parsed.path != "/v1/organization/costs":
                    status, data = 404, {"error": "unknown path"}
                elif self.headers.get("Authorization") != "Bearer sk-admin-fixture":
                    status, data = 403, {"error": "missing scope"}
                elif "page" not in query:
                    status, data = 200, {
                        "object": "page",
                        "data": [_bucket(month_start, "4.00")],
                        "has_more": True,
                        "next_page": "cursor-2",
                    }
                else:
                    status, data = 200, {
                        "object": "page",
                        "data": [_bucket(today_start, "1.25")],
                        "has_more": False,
                        "next_page": None,
                    }
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            costs = load_costs(
                "sk-admin-fixture",
                credits_since=date(2026, 9, 1),
                base_url=base,
                timeout=3,
                now=now,
            )

            self.assertEqual(costs.today, Decimal("1.25"))
            self.assertEqual(costs.month, Decimal("5.25"))
            self.assertEqual(costs.tracked, Decimal("5.25"))
            self.assertEqual(
                costs.tracked_since,
                int(datetime(2026, 9, 1).astimezone().timestamp()),
            )
            self.assertEqual(requests[0]["bucket_width"], ["1d"])
            self.assertEqual(requests[0]["start_time"], [str(costs.tracked_since)])
            self.assertEqual(requests[1]["page"], ["cursor-2"])

            with self.assertRaises(OpenAICostsError):
                load_costs("sk-proj-wrong", base_url=base, timeout=3, now=now)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


class BuildOpenAICardTests(unittest.TestCase):
    def _costs(self) -> object:
        return summarize(
            [_bucket(1_000, "1.50"), _bucket(9_000, "0.75")],
            today_start=9_000,
            month_start=1_000,
            tracked_since=1_000,
        )

    def test_shows_balance_when_credits_are_configured(self) -> None:
        card = build_openai_card(self._costs(), credits_purchased=Decimal("20"))

        self.assertEqual(card.title, "OpenAI")
        self.assertEqual(card.primary_label, "Saldo")
        self.assertEqual(card.primary_value, "US$ 17,75")
        self.assertEqual(card.secondary_label, "Gasto no mês")
        self.assertEqual(card.secondary_value, "US$ 2,25")
        self.assertIn("Hoje US$ 0,75", card.detail_text)
        self.assertTrue(card.available)

    def test_shows_spend_only_without_credits(self) -> None:
        card = build_openai_card(self._costs())

        self.assertEqual(card.primary_label, "Gasto no mês")
        self.assertEqual(card.primary_value, "US$ 2,25")
        self.assertEqual(card.secondary_value, "US$ 0,75")

    def test_exhausted_credits_are_unavailable(self) -> None:
        card = build_openai_card(self._costs(), credits_purchased=Decimal("1"))

        self.assertEqual(card.primary_value, "US$ -1,25")
        self.assertFalse(card.available)


if __name__ == "__main__":
    unittest.main()
