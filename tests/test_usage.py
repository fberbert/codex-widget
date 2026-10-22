import unittest

from codex_widget.usage import parse_usage_payload


def _window(*, used_percent: float, seconds: int | None, reset_after: int = 600) -> dict[str, object]:
    return {
        "used_percent": used_percent,
        "limit_window_seconds": seconds,
        "reset_after_seconds": reset_after,
    }


class ParseUsagePayloadTests(unittest.TestCase):
    def test_classifies_six_hour_primary_window_as_short(self) -> None:
        payload = {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": _window(used_percent=20, seconds=21_600),
                "secondary_window": _window(used_percent=40, seconds=604_800),
            },
        }

        usage = parse_usage_payload(payload, now=1_000)

        self.assertIsNotNone(usage.short_window)
        self.assertEqual(usage.short_window.window_seconds, 21_600)
        self.assertEqual(usage.short_window.used_percent, 20)
        self.assertEqual(usage.short_window.reset_at, 1_600)
        self.assertIsNotNone(usage.weekly)
        self.assertEqual(usage.weekly.window_seconds, 604_800)
        self.assertEqual(usage.plan_type, "plus")

    def test_classifies_five_hour_primary_window_as_short(self) -> None:
        payload = {
            "rate_limit": {
                "primary_window": _window(used_percent=14, seconds=18_000),
                "secondary_window": _window(used_percent=16, seconds=604_800),
            },
        }

        usage = parse_usage_payload(payload, now=1_000)

        self.assertEqual(usage.short_window.window_seconds, 18_000)
        self.assertEqual(usage.weekly.window_seconds, 604_800)

    def test_reads_short_window_from_secondary_when_primary_is_weekly(self) -> None:
        payload = {
            "rate_limit": {
                "primary_window": _window(used_percent=40, seconds=604_800),
                "secondary_window": _window(used_percent=20, seconds=21_600),
            },
        }

        usage = parse_usage_payload(payload, now=1_000)

        self.assertEqual(usage.weekly.used_percent, 40)
        self.assertEqual(usage.short_window.used_percent, 20)

    def test_weekly_only_payload_has_no_short_window(self) -> None:
        payload = {
            "rate_limit": {
                "primary_window": _window(used_percent=30, seconds=604_800),
            },
        }

        usage = parse_usage_payload(payload, now=1_000)

        self.assertIsNone(usage.short_window)
        self.assertEqual(usage.weekly.used_percent, 30)

    def test_missing_rate_limit_yields_empty_usage(self) -> None:
        usage = parse_usage_payload({"plan_type": "pro"}, now=1_000)

        self.assertIsNone(usage.short_window)
        self.assertIsNone(usage.weekly)
        self.assertEqual(usage.plan_type, "pro")


if __name__ == "__main__":
    unittest.main()
