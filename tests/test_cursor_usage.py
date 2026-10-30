import base64
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from codex_widget.cursor_usage import (
    CursorUsageError,
    jwt_expired,
    load_usage,
    parse_period,
    read_session,
)
from codex_widget.view_model import build_cursor_card

PERIOD = {
    "billingCycleStart": "1768399334000",
    "billingCycleEnd": "1800000000000",
    "planUsage": {
        "totalSpend": 23222,
        "includedSpend": 23222,
        "limit": 40000,
        "autoPercentUsed": 0,
        "apiPercentUsed": 46.444,
        "totalPercentUsed": 15.48,
    },
}


def _jwt(exp: int) -> str:
    def segment(payload: dict[str, object]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        return raw.decode("ascii")

    return f"{segment({'alg': 'none'})}.{segment({'exp': exp})}.sig"


class _Body:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Body":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class ParsePeriodTests(unittest.TestCase):
    def test_reads_plan_percentages_and_cycle_end(self) -> None:
        usage = parse_period(PERIOD, plan="Pro")

        self.assertEqual(usage.plan, "Pro")
        self.assertEqual(usage.total_percent_used, 15.48)
        self.assertEqual(usage.api_percent_used, 46.444)
        self.assertEqual(usage.spent_cents, 23222)
        self.assertEqual(usage.limit_cents, 40000)
        self.assertEqual(usage.cycle_end, 1_800_000_000)

    def test_derives_the_percent_from_spend_when_the_api_omits_it(self) -> None:
        payload = json.loads(json.dumps(PERIOD))
        del payload["planUsage"]["totalPercentUsed"]
        del payload["planUsage"]["apiPercentUsed"]

        usage = parse_period(payload, plan=None)

        self.assertAlmostEqual(usage.total_percent_used, 58.055, places=3)
        self.assertIsNone(usage.api_percent_used)

    def test_rejects_a_payload_without_plan_usage(self) -> None:
        with self.assertRaises(CursorUsageError):
            parse_period({})


class BuildCursorCardTests(unittest.TestCase):
    def test_uses_the_same_two_metric_layout_as_the_other_usage_cards(self) -> None:
        card = build_cursor_card(parse_period(PERIOD, plan="Pro"))
        renewal = datetime.fromtimestamp(1_800_000_000).astimezone()

        self.assertEqual(card.title, "Cursor")
        self.assertEqual(card.primary_label, "Uso do plano")
        self.assertEqual(card.primary_value, "85% restantes")
        self.assertEqual(card.primary_percent, 85)
        self.assertEqual(card.secondary_label, "Uso da API")
        self.assertEqual(card.secondary_value, "54% restantes")
        self.assertEqual(card.secondary_percent, 54)
        self.assertEqual(card.detail_text, "Plano Pro")
        self.assertEqual(card.footnote, f"US$ 232,22 de US$ 400,00 · renova {renewal:%d/%m}")
        self.assertTrue(card.available)


class ReadSessionTests(unittest.TestCase):
    def test_reads_the_cursor_auth_keys(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "state.vscdb"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO ItemTable VALUES (?, ?)",
            (
                ("cursorAuth/accessToken", _jwt(2_000_000_000)),
                ("cursorAuth/refreshToken", "refresh-token"),
                ("cursorAuth/stripeMembershipType", "pro_plus"),
            ),
        )
        connection.commit()
        connection.close()

        session = read_session(path)

        self.assertTrue(session.access_token.startswith("ey"))
        self.assertEqual(session.refresh_token, "refresh-token")
        self.assertEqual(session.plan, "Pro Plus")

    def test_missing_database_explains_where_it_looked(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "state.vscdb"
        with self.assertRaises(CursorUsageError) as caught:
            read_session(missing)
        self.assertIn(str(missing), str(caught.exception))


class LoadUsageTests(unittest.TestCase):
    def _database(self, token: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "state.vscdb"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            ("cursorAuth/accessToken", token),
        )
        connection.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            ("cursorAuth/refreshToken", "refresh-token"),
        )
        connection.commit()
        connection.close()
        return path

    def test_refreshes_once_after_an_unauthorized_period_request(self) -> None:
        calls: list[str] = []

        def fake_urlopen(request, timeout=0):  # noqa: ARG001
            calls.append(request.full_url)
            if request.full_url.endswith("/oauth/token"):
                return _Body({"access_token": "refreshed-token", "shouldLogout": False})
            if request.full_url.endswith("GetPlanInfo"):
                return _Body({"planInfo": {"planName": "Pro"}})
            if calls.count(request.full_url) == 1:
                error = HTTPError(request.full_url, 401, "unauthorized", hdrs=None, fp=BytesIO(b""))
                error.close()
                raise error
            self.assertEqual(request.get_header("Authorization"), "Bearer refreshed-token")
            return _Body(PERIOD)

        database = self._database(_jwt(2_000_000_000))
        with patch("codex_widget.cursor_usage.urlopen", fake_urlopen):
            usage = load_usage(state_db=database, now=1_700_000_000)

        self.assertEqual(usage.plan, "Pro")
        self.assertEqual(usage.spent_cents, 23222)
        self.assertFalse(jwt_expired(_jwt(2_000_000_000), now=1_700_000_000))
        self.assertTrue(jwt_expired(_jwt(1_600_000_000), now=1_700_000_000))
