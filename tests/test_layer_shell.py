import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect

from codex_widget.layer_shell import top_left_margins, top_right_margins


class LayerShellMarginTests(unittest.TestCase):
    def test_top_right_anchor_keeps_the_window_on_the_requested_corner(self) -> None:
        screen = QRect(0, 0, 1920, 1080)
        self.assertEqual(top_right_margins(screen, QPoint(1504, 24), 400), (0, 24, 16, 0))

    def test_top_right_anchor_uses_the_output_origin(self) -> None:
        screen = QRect(1920, 40, 2560, 1440)
        self.assertEqual(top_right_margins(screen, QPoint(4000, 80), 400), (0, 40, 80, 0))

    def test_top_left_anchor_places_a_popup(self) -> None:
        screen = QRect(0, 0, 1920, 1080)
        self.assertEqual(top_left_margins(screen, QPoint(120, 48)), (120, 48, 0, 0))


if __name__ == "__main__":
    unittest.main()
