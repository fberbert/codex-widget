import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from codex_widget import widget as widget_module
from codex_widget.widget import CodexUsageWidget


class ShutdownDuringFetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_closing_waits_for_the_in_flight_fetch_thread(self) -> None:
        def slow_run(worker: widget_module.UsageFetchWorker) -> None:
            time.sleep(1.5)
            worker.finished.emit()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            widget = CodexUsageWidget(
                auth_file=root / "missing-auth.json",
                base_url="http://127.0.0.1:1",
                ui_state_file=root / "ui.json",
                deepseek_state_file=root / "deepseek.json",
            )
            with patch.object(widget_module.UsageFetchWorker, "run", slow_run):
                widget.refresh()
                thread = widget._thread
                self.assertIsNotNone(thread)

                widget.close()

                # Destroying a running QThread aborts the process.
                self.assertFalse(thread.isRunning())
                self.assertEqual(widget._retired_threads, [])
                self.app.processEvents()

    def test_close_absorbs_keyboard_interrupt_while_a_fetch_is_stopping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            widget = CodexUsageWidget(
                auth_file=root / "missing-auth.json",
                base_url="http://127.0.0.1:1",
                ui_state_file=root / "ui.json",
                deepseek_state_file=root / "deepseek.json",
            )
            with patch.object(CodexUsageWidget, "_join_retired_threads", side_effect=KeyboardInterrupt):
                widget.close()
            self.assertTrue(widget._closing)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
