import json
import tempfile
import unittest
from pathlib import Path

from codex_widget.ui_state import WidgetUiState, load_ui_state, save_ui_state


class UiStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "ui.json"

    def test_defaults_to_vertical_expanded(self) -> None:
        state = load_ui_state(self._path)

        self.assertEqual(state, WidgetUiState())
        self.assertFalse(state.horizontal)
        self.assertFalse(state.collapsed)
        self.assertFalse(state.orientation_locked)
        self.assertIsNone(state.scale)

    def test_round_trip(self) -> None:
        save_ui_state(
            WidgetUiState(
                horizontal=True,
                orientation_locked=True,
                scale=0.9,
                collapsed=True,
                screen_name="DVI-I-3-2",
                offset_x=350,
                offset_y=120,
                width=640,
                active_only=True,
            ),
            self._path,
        )

        state = load_ui_state(self._path)

        self.assertTrue(state.horizontal)
        self.assertTrue(state.orientation_locked)
        self.assertEqual(state.scale, 0.9)
        self.assertTrue(state.collapsed)
        self.assertEqual(state.screen_name, "DVI-I-3-2")
        self.assertEqual((state.offset_x, state.offset_y), (350, 120))
        self.assertEqual(state.width, 640)
        self.assertTrue(state.active_only)

    def test_missing_and_corrupt_files_fall_back_to_defaults(self) -> None:
        self.assertEqual(load_ui_state(self._path), WidgetUiState())

        self._path.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_ui_state(self._path), WidgetUiState())

        self._path.write_text("[]", encoding="utf-8")
        self.assertEqual(load_ui_state(self._path), WidgetUiState())

    def test_invalid_width_is_ignored(self) -> None:
        for value in ("640", True, 0, -20, 1.5):
            self._path.write_text(json.dumps({"width": value}), encoding="utf-8")
            self.assertIsNone(load_ui_state(self._path).width)

    def test_invalid_scale_is_ignored(self) -> None:
        for value in ("big", True, None):
            self._path.write_text(json.dumps({"scale": value}), encoding="utf-8")
            self.assertIsNone(load_ui_state(self._path).scale)


if __name__ == "__main__":
    unittest.main()
