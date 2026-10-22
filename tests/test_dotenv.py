import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_widget.dotenv import load_env_file, resolve_secret


class LoadEnvFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / ".env"

    def test_parses_shell_style_assignments(self) -> None:
        self._path.write_text(
            "\n".join(
                [
                    "# openai",
                    "OPENAI_ADMIN_API_KEY=sk-admin-plain",
                    'QUOTED="sk-quoted"',
                    "SINGLE='sk-single'",
                    "export EXPORTED=sk-exported",
                    "export A B C",
                    "SPACED = sk-spaced ",
                    "",
                    "OVERRIDDEN=first",
                    "OVERRIDDEN=last",
                    "not an assignment",
                ]
            ),
            encoding="utf-8",
        )

        values = load_env_file(self._path)

        self.assertEqual(values["OPENAI_ADMIN_API_KEY"], "sk-admin-plain")
        self.assertEqual(values["QUOTED"], "sk-quoted")
        self.assertEqual(values["SINGLE"], "sk-single")
        self.assertEqual(values["EXPORTED"], "sk-exported")
        self.assertEqual(values["SPACED"], "sk-spaced")
        self.assertEqual(values["OVERRIDDEN"], "last")
        self.assertNotIn("not an assignment", values)

    def test_missing_file_yields_no_values(self) -> None:
        self.assertEqual(load_env_file(self._path), {})


class ResolveSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / ".env"
        self._path.write_text("OPENAI_ADMIN_API_KEY=from-file\n", encoding="utf-8")

    def test_process_environment_wins_over_the_file(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_ADMIN_API_KEY": "from-env"}):
            self.assertEqual(
                resolve_secret("OPENAI_ADMIN_API_KEY", env_file=self._path),
                "from-env",
            )

    def test_falls_back_to_the_file_and_honours_name_order(self) -> None:
        environment = {k: v for k, v in os.environ.items() if k != "OPENAI_ADMIN_API_KEY"}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                resolve_secret("OPENAI_ADMIN_KEY", "OPENAI_ADMIN_API_KEY", env_file=self._path),
                "from-file",
            )
            self.assertIsNone(resolve_secret("ABSENT_NAME", env_file=self._path))


if __name__ == "__main__":
    unittest.main()
