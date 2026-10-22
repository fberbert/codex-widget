import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from codex_widget.config import WidgetConfig, default_config_path, load_config


class LoadConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "config.json"

    def test_loads_all_known_fields(self) -> None:
        self._path.write_text(
            json.dumps(
                {
                    "deepseek_api_key": "  sk-test  ",
                    "deepseek_base_url": "https://example.test",
                    "deepseek_state_file": "~/state.json",
                    "deepseek_spent_baseline": "12.34",
                    "mimo_chrome_profile": "Default",
                    "claude_chrome_profile": "Profile 1",
                }
            ),
            encoding="utf-8",
        )

        config = load_config(self._path)

        self.assertEqual(config.deepseek_api_key, "sk-test")
        self.assertEqual(config.deepseek_base_url, "https://example.test")
        self.assertEqual(config.deepseek_state_file, Path("~/state.json").expanduser())
        self.assertEqual(config.deepseek_spent_baseline, Decimal("12.34"))
        self.assertEqual(config.mimo_chrome_profile, "Default")
        self.assertEqual(config.claude_chrome_profile, "Profile 1")

    def test_missing_file_yields_empty_config(self) -> None:
        self.assertEqual(load_config(self._path), WidgetConfig())

    def test_corrupt_file_yields_empty_config(self) -> None:
        self._path.write_text("{not json", encoding="utf-8")

        self.assertEqual(load_config(self._path), WidgetConfig())

    def test_non_object_file_yields_empty_config(self) -> None:
        self._path.write_text("[]", encoding="utf-8")

        self.assertEqual(load_config(self._path), WidgetConfig())

    def test_invalid_baseline_is_ignored(self) -> None:
        self._path.write_text(json.dumps({"deepseek_spent_baseline": "abc"}), encoding="utf-8")

        self.assertIsNone(load_config(self._path).deepseek_spent_baseline)

    def test_default_path_honors_override_and_xdg(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_WIDGET_CONFIG": "~/custom.json"}):
            self.assertEqual(default_config_path(), Path("~/custom.json").expanduser())

        env = {"XDG_CONFIG_HOME": "/tmp/xdg-test"}
        env.pop("CODEX_WIDGET_CONFIG", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_config_path(), Path("/tmp/xdg-test/codex-widget/config.json"))


if __name__ == "__main__":
    unittest.main()
