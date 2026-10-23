import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from codex_widget.kiro import KiroUsageError, parse_usage, read_token

PAYLOAD = {
    "daysUntilReset": 9,
    "limits": [],
    "nextDateReset": 1793491200.0,
    "overageConfiguration": {"overageStatus": "DISABLED"},
    "subscriptionInfo": {
        "subscriptionTitle": "KIRO POWER",
        "type": "Q_DEVELOPER_STANDALONE_POWER",
    },
    "usageBreakdownList": [
        {
            "currency": "USD",
            "currentOverages": 0,
            "currentOveragesWithPrecision": 0.0,
            "currentUsage": 176,
            "currentUsageWithPrecision": 176.64,
            "displayName": "Credit",
            "displayNamePlural": "Credits",
            "nextDateReset": 1793491200.0,
            "overageCap": 2500,
            "overageCapWithPrecision": 2500.0,
            "overageCharges": 0.0,
            "overageRate": 0.04,
            "resourceType": "CREDIT",
            "unit": "INVOCATIONS",
            "usageLimit": 10000,
            "usageLimitWithPrecision": 10000.0,
        }
    ],
}


class ParseUsageTests(unittest.TestCase):
    def test_reads_the_credit_breakdown(self) -> None:
        usage = parse_usage(PAYLOAD)

        self.assertEqual(usage.used, Decimal("176.64"))
        self.assertEqual(usage.limit, Decimal("10000.0"))
        self.assertEqual(usage.plan, "KIRO POWER")
        self.assertEqual(usage.next_reset, 1793491200)
        self.assertFalse(usage.overage_enabled)
        self.assertEqual(usage.percent_remaining, 98)

    def test_rounds_the_remaining_percentage_down_to_whole_points(self) -> None:
        payload = json.loads(json.dumps(PAYLOAD))
        payload["usageBreakdownList"][0]["currentUsageWithPrecision"] = 9999.5

        self.assertEqual(parse_usage(payload).percent_remaining, 0)

    def test_keeps_overage_details_when_enabled(self) -> None:
        payload = json.loads(json.dumps(PAYLOAD))
        payload["overageConfiguration"]["overageStatus"] = "ENABLED"
        payload["usageBreakdownList"][0]["currentOveragesWithPrecision"] = 12.5
        payload["usageBreakdownList"][0]["overageCharges"] = 0.5

        usage = parse_usage(payload)

        self.assertTrue(usage.overage_enabled)
        self.assertEqual(usage.overage_used, Decimal("12.5"))
        self.assertEqual(usage.overage_charges, Decimal("0.5"))

    def test_rejects_a_payload_without_a_credit_breakdown(self) -> None:
        with self.assertRaises(KiroUsageError):
            parse_usage({"usageBreakdownList": []})

    def test_rejects_a_zero_limit(self) -> None:
        payload = json.loads(json.dumps(PAYLOAD))
        payload["usageBreakdownList"][0]["usageLimitWithPrecision"] = 0.0
        payload["usageBreakdownList"][0]["usageLimit"] = 0

        with self.assertRaises(KiroUsageError):
            parse_usage(payload)


class ReadTokenTests(unittest.TestCase):
    def _database(self, token: dict[str, object]) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "data.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO auth_kv VALUES (?, ?)",
            ("kirocli:odic:token", json.dumps(token)),
        )
        connection.commit()
        connection.close()
        return path

    def test_returns_the_access_token(self) -> None:
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        path = self._database(
            {"access_token": "abc", "expires_at": later.strftime("%Y-%m-%dT%H:%M:%S.%f")}
        )

        self.assertEqual(read_token(path), "abc")

    def test_reports_an_expired_session(self) -> None:
        earlier = datetime.now(timezone.utc) - timedelta(minutes=5)
        path = self._database(
            {"access_token": "abc", "expires_at": earlier.strftime("%Y-%m-%dT%H:%M:%S.%f")}
        )

        with self.assertRaises(KiroUsageError) as error:
            read_token(path)
        self.assertIn("kiro-cli login", str(error.exception))

    def test_reports_a_missing_database(self) -> None:
        with self.assertRaises(KiroUsageError):
            read_token(Path(tempfile.mkdtemp()) / "absent.sqlite3")

    def test_reports_a_database_without_a_token(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "data.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
        connection.commit()
        connection.close()

        with self.assertRaises(KiroUsageError):
            read_token(path)


if __name__ == "__main__":
    unittest.main()
