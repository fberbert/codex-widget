"""Move a window on KDE Wayland.

Qt's Wayland client cannot set its own position, and the layer-shell plugin
shipped with KDE is built against the system Qt, not the Qt bundled with
PyQt. KWin accepts a new frame rectangle from a short script. A second script
reads the rectangle KWin actually used, so a drag is saved from the screen
and not from the centered position KWin chose first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6 import QtCore
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PyQt6.QtWidgets import QApplication

_SERVICE_ROOT = "org.kde.KWin"
_SCRIPTING_PATH = "/Scripting"
_SCRIPTING_IFACE = "org.kde.kwin.Scripting"
_SCRIPT_IFACE = "org.kde.kwin.Script"
_STATE_DIR = Path.home() / ".local/state/codex-widget/kwin"
_DETAIL_MARKER = "detalhe"


def placement_script(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    pid: int,
    service: str,
    interface: str,
    caption: str | None = None,
    resize: bool = False,
) -> str:
    if caption is None:
        match = (
            f"window.pid !== {int(pid)} || "
            f"String(window.caption).indexOf({json.dumps(_DETAIL_MARKER)}) >= 0"
        )
    else:
        match = f"String(window.caption) !== {json.dumps(caption)}"
    size = ""
    if resize:
        size = f"width: {max(1, int(width))}, height: {max(1, int(height))}"
    else:
        size = "width: geo.width, height: geo.height"
    return (
        "workspace.windowList().forEach(function (window) {\n"
        f"    if ({match})\n"
        "        return;\n"
        "    var geo = window.frameGeometry;\n"
        f"    window.frameGeometry = {{x: {int(x)}, y: {int(y)}, {size}}};\n"
        "    var placed = window.frameGeometry;\n"
        f'    callDBus({json.dumps(service)}, "/Place", {json.dumps(interface)}, "at", '
        "Math.round(placed.x), Math.round(placed.y));\n"
        "});\n"
    )


def report_script(*, pid: int, service: str, interface: str) -> str:
    return (
        "workspace.windowList().forEach(function (window) {\n"
        f"    if (window.pid !== {int(pid)} || "
        f"String(window.caption).indexOf({json.dumps(_DETAIL_MARKER)}) >= 0)\n"
        "        return;\n"
        "    var placed = window.frameGeometry;\n"
        f'    callDBus({json.dumps(service)}, "/Place", {json.dumps(interface)}, "at", '
        "Math.round(placed.x), Math.round(placed.y));\n"
        "});\n"
    )


class _Bridge(QtCore.QObject):
    def __init__(self, owner: KWinPlacer) -> None:
        super().__init__()
        self._owner = owner

    @QtCore.pyqtSlot(int, int)
    def at(self, x: int, y: int) -> None:
        self._owner.moved.emit(int(x), int(y))


class KWinPlacer(QtCore.QObject):
    moved = QtCore.pyqtSignal(int, int)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._available: bool | None = None
        self._sequence = 0
        self._place_plugin: str | None = None
        self._place_path: Path | None = None
        self._report_plugin: str | None = None
        self._report_path: Path | None = None
        self._service = f"org.codex.Widget.p{os.getpid()}"
        self._interface: str | None = None
        self._bridge = _Bridge(self)

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        app = QApplication.instance()
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        plasma = "KDE" in desktop or "plasma" in desktop.lower() or bool(os.environ.get("KDE_FULL_SESSION"))
        if app is None or app.platformName() != "wayland" or not plasma:
            self._available = False
            return False
        bus = QDBusConnection.sessionBus()
        scripting = QDBusInterface(_SERVICE_ROOT, _SCRIPTING_PATH, _SCRIPTING_IFACE, bus)
        if not scripting.isValid() or not self._export(bus):
            self._available = False
            return False
        self._available = True
        return True

    def place(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        caption: str | None = None,
        resize: bool = False,
    ) -> bool:
        if not self.available() or self._interface is None:
            return False
        script = placement_script(
            x,
            y,
            width,
            height,
            pid=os.getpid(),
            service=self._service,
            interface=self._interface,
            caption=caption,
            resize=resize,
        )
        return self._run(script, kind="place")

    def report(self) -> bool:
        if not self.available() or self._interface is None:
            return False
        script = report_script(pid=os.getpid(), service=self._service, interface=self._interface)
        return self._run(script, kind="report")

    def _export(self, bus: QDBusConnection) -> bool:
        if not bus.registerService(self._service):
            return False
        if not bus.registerObject("/Place", self._bridge, QDBusConnection.RegisterOption.ExportAllSlots):
            return False
        reply = bus.call(
            QDBusMessage.createMethodCall(
                self._service,
                "/Place",
                "org.freedesktop.DBus.Introspectable",
                "Introspect",
            )
        )
        if not _accepted(reply) or not reply.arguments():
            return False
        xml = str(reply.arguments()[0])
        for part in xml.split('interface name="')[1:]:
            name = part.split('"', 1)[0]
            if name.endswith("._Bridge") or name.endswith(".Bridge"):
                self._interface = name
                return True
        return False

    def _run(self, script: str, *, kind: str) -> bool:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        self._sequence += 1
        plugin = f"codex-widget-{kind}-{os.getpid()}-{self._sequence}"
        path = _STATE_DIR / f"{plugin}.js"
        path.write_text(script, encoding="utf-8")
        bus = QDBusConnection.sessionBus()
        scripting = QDBusInterface(_SERVICE_ROOT, _SCRIPTING_PATH, _SCRIPTING_IFACE, bus)
        previous = self._place_plugin if kind == "place" else self._report_plugin
        previous_path = self._place_path if kind == "place" else self._report_path
        loaded = scripting.call("loadScript", str(path), plugin)
        if not _accepted(loaded) or not loaded.arguments():
            path.unlink(missing_ok=True)
            return False
        number = loaded.arguments()[0]
        runner = QDBusInterface(_SERVICE_ROOT, f"/Scripting/Script{number}", _SCRIPT_IFACE, bus)
        if not _accepted(runner.call("run")):
            scripting.call("unloadScript", plugin)
            path.unlink(missing_ok=True)
            return False
        # Unload only after run so the script's async callDBus can deliver.
        if previous is not None:
            scripting.call("unloadScript", previous)
            if previous_path is not None:
                previous_path.unlink(missing_ok=True)
        if kind == "place":
            self._place_plugin = plugin
            self._place_path = path
        else:
            self._report_plugin = plugin
            self._report_path = path
        return True


def _accepted(message: QDBusMessage) -> bool:
    return message.type() == QDBusMessage.MessageType.ReplyMessage
