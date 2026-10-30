"""Place the widget with KDE's layer-shell on Wayland.

A normal Wayland toplevel cannot choose its position: KWin ignores
``QWidget.move()`` and opens the window with the placement policy, which on
this desktop is the middle of the screen. Layer shell can anchor the surface
to the top-right corner, and dragging updates those margins.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import CDLL, c_int, c_uint, c_void_p
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication

_LIBRARY = Path("/usr/lib64/libLayerShellQtInterface.so.6")

# LayerShellQt::Window::Anchor in layer-shell-qt 6.
ANCHOR_TOP = 1
ANCHOR_LEFT = 4
ANCHOR_RIGHT = 8
LAYER_TOP = 2
KEYBOARD_ON_DEMAND = 2

_GET = "_ZN12LayerShellQt6Window3getEP7QWindow"
_SET_ANCHORS = "_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE"
_SET_MARGINS = "_ZN12LayerShellQt6Window10setMarginsERK8QMargins"
_SET_LAYER = "_ZN12LayerShellQt6Window8setLayerENS0_5LayerE"
_SET_ZONE = "_ZN12LayerShellQt6Window16setExclusiveZoneEi"
_SET_KEYBOARD = "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE"
_SET_ACTIVATE = "_ZN12LayerShellQt6Window17setActivateOnShowEb"
_SET_SIZE = "_ZN12LayerShellQt6Window14setDesiredSizeERK5QSize"
_SET_SCREEN = "_ZN12LayerShellQt6Window9setScreenEP7QScreen"


class _QMargins(ctypes.Structure):
    _fields_ = (
        ("left", c_int),
        ("top", c_int),
        ("right", c_int),
        ("bottom", c_int),
    )


class _QSize(ctypes.Structure):
    _fields_ = (("width", c_int), ("height", c_int))


class LayerShell:
    def __init__(self, library: CDLL) -> None:
        self._get = library[_GET]
        self._get.argtypes = [c_void_p]
        self._get.restype = c_void_p
        self._set_anchors = library[_SET_ANCHORS]
        self._set_anchors.argtypes = [c_void_p, c_uint]
        self._set_anchors.restype = None
        self._set_margins = library[_SET_MARGINS]
        self._set_margins.argtypes = [c_void_p, ctypes.POINTER(_QMargins)]
        self._set_margins.restype = None
        self._set_layer = library[_SET_LAYER]
        self._set_layer.argtypes = [c_void_p, c_uint]
        self._set_layer.restype = None
        self._set_zone = library[_SET_ZONE]
        self._set_zone.argtypes = [c_void_p, c_int]
        self._set_zone.restype = None
        self._set_keyboard = library[_SET_KEYBOARD]
        self._set_keyboard.argtypes = [c_void_p, c_uint]
        self._set_keyboard.restype = None
        self._set_activate = library[_SET_ACTIVATE]
        self._set_activate.argtypes = [c_void_p, ctypes.c_bool]
        self._set_activate.restype = None
        self._set_size = library[_SET_SIZE]
        self._set_size.argtypes = [c_void_p, ctypes.POINTER(_QSize)]
        self._set_size.restype = None
        self._set_screen = library[_SET_SCREEN]
        self._set_screen.argtypes = [c_void_p, c_void_p]
        self._set_screen.restype = None

    def place(
        self,
        window: c_void_p,
        *,
        anchors: int,
        margins: tuple[int, int, int, int],
        size: tuple[int, int],
        screen: c_void_p | None = None,
    ) -> bool:
        shell = self._get(window)
        if not shell:
            return False
        left, top, right, bottom = margins
        qmargins = _QMargins(int(left), int(top), int(right), int(bottom))
        qsize = _QSize(max(1, int(size[0])), max(1, int(size[1])))
        self._set_layer(shell, LAYER_TOP)
        self._set_zone(shell, 0)
        self._set_keyboard(shell, KEYBOARD_ON_DEMAND)
        self._set_activate(shell, False)
        if screen:
            self._set_screen(shell, screen)
        self._set_anchors(shell, anchors)
        self._set_margins(shell, ctypes.byref(qmargins))
        self._set_size(shell, ctypes.byref(qsize))
        return True


def use_layer_shell() -> bool:
    """Ask Qt to create layer-shell surfaces. Must run before ``QApplication``.

    The shell plugin has to belong to the same Qt the process loads. The
    system copy is built against KDE's Qt and aborts a PyQt wheel, so it is
    ignored unless it sits in that Qt's own plugin directory.
    """

    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return False
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    if platform.startswith("offscreen") or platform.startswith("minimal"):
        return False
    if _bundled_layer_plugin() is None:
        return False
    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
    return True


def _bundled_layer_plugin() -> Path | None:
    try:
        import PyQt6
    except ImportError:
        return None
    plugin = Path(PyQt6.__file__).resolve().parent / "Qt6/plugins/wayland-shell-integration/liblayer-shell.so"
    return plugin if plugin.is_file() else None


def load_layer_shell() -> LayerShell | None:
    if os.environ.get("QT_WAYLAND_SHELL_INTEGRATION") != "layer-shell":
        return None
    app = QApplication.instance()
    if app is not None and app.platformName() != "wayland":
        return None
    if not _LIBRARY.is_file():
        return None
    try:
        return LayerShell(ctypes.CDLL(str(_LIBRARY)))
    except OSError:
        return None


def top_right_margins(screen: QRect, origin: QPoint, size_width: int) -> tuple[int, int, int, int]:
    """Margins for a top+right anchor that puts the window's top-left at ``origin``."""

    top = max(0, origin.y() - screen.y())
    right = max(0, screen.x() + screen.width() - (origin.x() + size_width))
    return (0, top, right, 0)


def top_left_margins(screen: QRect, origin: QPoint) -> tuple[int, int, int, int]:
    return (max(0, origin.x() - screen.x()), max(0, origin.y() - screen.y()), 0, 0)
