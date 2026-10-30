import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from codex_widget.widget import DEFAULT_EDGE_MARGIN, DEFAULT_TOP_MARGIN, CodexUsageWidget
from codex_widget.ui_state import load_ui_state


class MultiMonitorCollapseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_badge_and_expanded_panel_stay_on_the_button_monitor(self) -> None:
        primary = SimpleNamespace(name=lambda: "primary",
                                  geometry=lambda: QtCore.QRect(1920, 0, 3440, 1440),
                                  availableGeometry=lambda: QtCore.QRect(1920, 0, 3440, 1440))
        left = SimpleNamespace(name=lambda: "left",
                               geometry=lambda: QtCore.QRect(0, 1140, 1920, 1080),
                               availableGeometry=lambda: QtCore.QRect(0, 1140, 1920, 1080))
        right = SimpleNamespace(name=lambda: "right",
                                geometry=lambda: QtCore.QRect(5360, 360, 2560, 1080),
                                availableGeometry=lambda: QtCore.QRect(5360, 360, 2560, 1080))
        screens = (left, primary, right)

        def screen_at(point):
            return next((screen for screen in screens if screen.geometry().contains(point)), None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, screen in (("left edge", left), ("right monitor", right)):
                with self.subTest(name=name):
                    widget = CodexUsageWidget(
                        auth_file=root / "missing-auth.json",
                        base_url="http://127.0.0.1:1",
                        ui_state_file=root / f"{name}.json",
                        deepseek_state_file=root / "deepseek.json",
                    )
                    self.addCleanup(widget.close)
                    widget._scale = 0.45
                    widget._scale_saved = True
                    widget._apply_scale()
                    if screen is left:
                        # The exclusive bottom-right point is across the monitor seam.
                        widget.move(screen.geometry().right() + 1 - widget.width(),
                                    screen.geometry().bottom() + 1 - widget.height())
                    else:
                        widget.move(screen.geometry().x() + 300, screen.geometry().y() + 100)
                    original_position = QtCore.QPoint(widget.pos())
                    original_corner = widget._bottom_right()
                    button_point = widget._toggle_button.mapToGlobal(widget._toggle_button.rect().center())
                    self.assertTrue(screen.geometry().contains(button_point))

                    with patch.object(QtWidgets.QApplication, "primaryScreen", return_value=primary), \
                         patch.object(QtWidgets.QApplication, "screenAt", side_effect=screen_at):
                        widget._toggle_collapsed()
                        self.assertTrue(screen.availableGeometry().contains(widget.geometry()))
                        self.assertEqual(widget._bottom_right(), original_corner)

                        widget._toggle_collapsed()
                        self.assertTrue(screen.availableGeometry().contains(widget.geometry()))
                        self.assertEqual(widget.pos(), original_position)
                    widget.close()

    def test_restores_position_on_same_monitor_after_layout_changes(self) -> None:
        primary = SimpleNamespace(
            name=lambda: "primary",
            geometry=lambda: QtCore.QRect(1920, 0, 3440, 1440),
            availableGeometry=lambda: QtCore.QRect(1920, 0, 3440, 1440),
        )
        right = SimpleNamespace(
            name=lambda: "right",
            geometry=lambda: QtCore.QRect(5360, 360, 2560, 1080),
            availableGeometry=lambda: QtCore.QRect(5360, 360, 2560, 1080),
        )
        moved_right = SimpleNamespace(
            name=lambda: "right",
            geometry=lambda: QtCore.QRect(6000, 100, 2560, 1080),
            availableGeometry=lambda: QtCore.QRect(6000, 100, 2560, 1080),
        )
        screens = [primary, right]

        def screen_at(point):
            return next((screen for screen in screens if screen.geometry().contains(point)), None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "ui.json"
            with patch.object(QtWidgets.QApplication, "primaryScreen", return_value=primary), \
                 patch.object(QtWidgets.QApplication, "screens", side_effect=lambda: screens), \
                 patch.object(QtWidgets.QApplication, "screenAt", side_effect=screen_at):
                widget = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=state,
                    deepseek_state_file=root / "deepseek.json",
                )
                widget._scale = 0.45
                widget._scale_saved = True
                widget._apply_scale()
                widget.move(5360 + 350, 360 + 120)
                widget._save_ui_state()
                widget.close()

                screens[1] = moved_right
                restored = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=state,
                    deepseek_state_file=root / "deepseek.json",
                )
                restored._place_initially()
                self.assertEqual(restored.pos(), QtCore.QPoint(6000 + 350, 100 + 120))
                self.assertTrue(moved_right.availableGeometry().contains(restored.geometry()))
                restored._toggle_collapsed()
                badge_position = QtCore.QPoint(restored.pos())
                restored.close()

                collapsed = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=state,
                    deepseek_state_file=root / "deepseek.json",
                )
                collapsed._place_initially()
                self.assertTrue(collapsed._collapsed)
                self.assertEqual(collapsed.pos(), badge_position)
                collapsed.close()


    def test_releasing_a_drag_saves_new_window_coordinates(self) -> None:
        screen = SimpleNamespace(
            name=lambda: "primary",
            geometry=lambda: QtCore.QRect(0, 0, 1920, 1080),
            availableGeometry=lambda: QtCore.QRect(0, 0, 1920, 1080),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "ui.json"
            with patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
                 patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
                widget = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=state_file,
                    deepseek_state_file=root / "deepseek.json",
                )
                widget.move(100, 100)
                left = QtCore.Qt.MouseButton.LeftButton
                none = QtCore.Qt.MouseButton.NoButton
                modifier = QtCore.Qt.KeyboardModifier.NoModifier
                local = QtCore.QPointF(10, 10)
                widget.mousePressEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.Type.MouseButtonPress, local, QtCore.QPointF(110, 110),
                    left, left, modifier,
                ))
                widget.mouseMoveEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.Type.MouseMove, local, QtCore.QPointF(410, 240),
                    none, left, modifier,
                ))
                widget.mouseReleaseEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.Type.MouseButtonRelease, local, QtCore.QPointF(410, 240),
                    left, none, modifier,
                ))
                saved = load_ui_state(state_file)
                self.assertEqual(widget.pos(), QtCore.QPoint(400, 230))
                self.assertEqual((saved.screen_name, saved.offset_x, saved.offset_y),
                                 ("primary", 400, 230))
                widget.close()

    def test_closing_after_window_manager_move_persists_position(self) -> None:
        screen = SimpleNamespace(
            name=lambda: "primary",
            geometry=lambda: QtCore.QRect(0, 0, 1920, 1080),
            availableGeometry=lambda: QtCore.QRect(0, 0, 1920, 1080),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "ui.json"
            with patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
                 patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
                widget = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=state_file,
                    deepseek_state_file=root / "deepseek.json",
                )
                widget.show()
                widget.move(300, 200)
                widget.close()
                saved = load_ui_state(state_file)
                self.assertEqual((saved.screen_name, saved.offset_x, saved.offset_y),
                                 ("primary", 300, 200))


class PanelWidthAndPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_opens_on_the_right_edge_until_the_user_moves_it(self) -> None:
        screen = SimpleNamespace(
            name=lambda: "primary",
            geometry=lambda: QtCore.QRect(100, 40, 1920, 1080),
            availableGeometry=lambda: QtCore.QRect(100, 40, 1920, 1080),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
                 patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
                widget = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=root / "ui.json",
                    deepseek_state_file=root / "deepseek.json",
                )
                widget._place_initially()
                area = screen.availableGeometry()
                self.assertEqual(
                    widget.pos(),
                    QtCore.QPoint(
                        area.x() + area.width() - widget.width() - DEFAULT_EDGE_MARGIN,
                        area.y() + DEFAULT_TOP_MARGIN,
                    ),
                )
                widget.close()

    def test_dragging_the_left_edge_changes_width_and_shows_the_resize_cursor(self) -> None:
        screen = SimpleNamespace(
            name=lambda: "primary",
            geometry=lambda: QtCore.QRect(0, 0, 1920, 1080),
            availableGeometry=lambda: QtCore.QRect(0, 0, 1920, 1080),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "ui.json"
            with patch.object(QtWidgets.QApplication, "primaryScreen", return_value=screen), \
                 patch.object(QtWidgets.QApplication, "screenAt", return_value=screen):
                widget = CodexUsageWidget(
                    auth_file=root / "missing-auth.json",
                    base_url="http://127.0.0.1:1",
                    ui_state_file=state_file,
                    deepseek_state_file=root / "deepseek.json",
                )
                widget._user_width = None
                widget._apply_scale()
                widget.move(400, 80)
                start = widget.geometry()
                right = start.x() + start.width()
                self.assertIn(widget._cursor_card, widget._metric_cards())
                self.assertTrue(widget._cursor_card.has_bars())
                self.assertIn("Cursor", widget._badge._values)

                left = QtCore.Qt.MouseButton.LeftButton
                none = QtCore.Qt.MouseButton.NoButton
                modifier = QtCore.Qt.KeyboardModifier.NoModifier
                local = QtCore.QPointF(2, 40)
                origin = QtCore.QPointF(start.x() + 2, start.y() + 40)
                widget.mouseMoveEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.Type.MouseMove, local, origin, none, none, modifier,
                ))
                self.assertEqual(widget.cursor().shape(), QtCore.Qt.CursorShape.SizeHorCursor)

                handle = widget.windowHandle()
                resize = patch.object(handle, "startSystemResize", return_value=False) if handle is not None else nullcontext()
                with resize:
                    widget.mousePressEvent(QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseButtonPress, local, origin, left, left, modifier,
                    ))
                    moved = QtCore.QPointF(origin.x() - 70, origin.y())
                    widget.mouseMoveEvent(QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseMove, QtCore.QPointF(2, 40), moved, none, left, modifier,
                    ))
                    widget.mouseReleaseEvent(QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseButtonRelease, QtCore.QPointF(2, 40), moved,
                        left, none, modifier,
                    ))

                self.assertEqual(widget.width(), start.width() + 70)
                self.assertEqual(widget.height(), start.height())
                self.assertEqual(widget.x() + widget.width(), right)
                self.assertEqual(load_ui_state(state_file).width, widget.width())
                widget.close()

    def test_close_button_sits_at_the_top_right_and_quits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            widget = CodexUsageWidget(
                auth_file=root / "missing-auth.json",
                base_url="http://127.0.0.1:1",
                ui_state_file=root / "ui.json",
                deepseek_state_file=root / "deepseek.json",
            )
            widget.show()
            self.assertFalse(widget._close_button.isVisible())
            self.assertFalse(widget._toggle_button.isVisible())
            widget.enterEvent(QtGui.QEnterEvent(
                QtCore.QPointF(8, 8), QtCore.QPointF(8, 8), QtCore.QPointF(8, 8),
            ))
            close = widget._close_button
            collapse = widget._toggle_button
            self.assertTrue(close.isVisible())
            self.assertTrue(collapse.isVisible())
            self.assertEqual(close.toolTip(), "Fechar")
            self.assertGreater(close.x(), collapse.x())
            self.assertLess(close.y(), widget.height() / 2)
            self.assertGreater(close.x() + close.width(), widget.width() - 80)
            widget._chrome_visible = False
            widget._apply_chrome_visibility()
            self.assertFalse(close.isVisible())
            # Child Enter via the event filter also shows chrome.
            widget.eventFilter(widget._codex_card, QtCore.QEvent(QtCore.QEvent.Type.Enter))
            self.assertTrue(close.isVisible())
            close.click()
            self.assertFalse(widget.isVisible())

            collapsed = CodexUsageWidget(
                auth_file=root / "missing-auth.json",
                base_url="http://127.0.0.1:1",
                ui_state_file=root / "collapsed.json",
                deepseek_state_file=root / "deepseek.json",
            )
            collapsed._collapsed = True
            collapsed._apply_scale()
            collapsed.show()
            self.assertFalse(collapsed._close_button.isVisible())
            collapsed.enterEvent(QtGui.QEnterEvent(
                QtCore.QPointF(8, 8), QtCore.QPointF(8, 8), QtCore.QPointF(8, 8),
            ))
            self.assertFalse(collapsed._toggle_button.isVisible())
            self.assertTrue(collapsed._close_button.isVisible())
            collapsed.close()

    def test_active_shortcut_keeps_only_cards_with_live_metrics(self) -> None:
        from codex_widget.view_model import MetricCardModel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            widget = CodexUsageWidget(
                auth_file=root / "missing-auth.json",
                base_url="http://127.0.0.1:1",
                ui_state_file=root / "ui.json",
                deepseek_state_file=root / "deepseek.json",
            )
            widget._codex_card.set_model(MetricCardModel(
                title="Codex",
                primary_label="Janela de 5 horas",
                primary_value="90% restantes",
                secondary_label="Janela semanal",
                secondary_value="97% restantes",
                detail_text="Assinatura Plus",
                footnote="",
                available=True,
                primary_percent=90,
                secondary_percent=97,
            ))
            widget._claude_card.set_error("Sessão indisponível")
            widget.show()
            self.assertFalse(widget._close_button.isVisible())
            self.assertIn("Ctrl+Shift+A", widget._shortcuts.text())
            self.assertIn("só as ativas", widget._shortcuts.text())

            widget._toggle_active_only()

            self.assertFalse(widget._codex_card.isHidden())
            self.assertTrue(widget._claude_card.isHidden())
            self.assertTrue(widget._badge._cells["Claude"].isHidden())
            self.assertFalse(widget._badge._cells["Codex"].isHidden())
            self.assertIn("todas", widget._shortcuts.text())
            self.assertTrue(load_ui_state(root / "ui.json").active_only)
            widget.close()


if __name__ == "__main__":
    unittest.main()
