import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from codex_widget.kwin_place import KWinPlacer, placement_script


class PlacementScriptTests(unittest.TestCase):
    def test_script_moves_this_process_and_reports_the_rectangle(self) -> None:
        script = placement_script(
            1500,
            24,
            420,
            800,
            pid=4321,
            service="org.codex.Widget.p4321",
            interface="local.codex_widget.kwin_place._Bridge",
        )
        self.assertIn("window.pid !== 4321", script)
        self.assertIn("x: 1500, y: 24, width: geo.width, height: geo.height", script)
        self.assertIn("window.frameGeometry", script)
        self.assertIn('"at"', script)

    def test_caption_match_is_exact(self) -> None:
        script = placement_script(
            10,
            20,
            30,
            40,
            pid=1,
            service="org.codex.Widget.p1",
            interface="local.Example",
            caption='Codex "Usage"',
        )
        self.assertIn('"Codex \\"Usage\\""', script)

    def test_offscreen_session_does_not_talk_to_kwin(self) -> None:
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertFalse(KWinPlacer().available())


if __name__ == "__main__":
    unittest.main()
