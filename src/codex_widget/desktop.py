from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6 import QtCore, QtGui

DESKTOP_FILE_NAME = "codex-widget"
ICON_NAME = "codex-widget"
ICON_SIZES = (48, 64, 128, 256, 512)


def data_home() -> Path:
    override = os.environ.get("XDG_DATA_HOME")
    return Path(override).expanduser() if override else Path.home() / ".local" / "share"


def find_icon() -> Path | None:
    override = os.environ.get("CODEX_WIDGET_ICON")
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    module = Path(__file__).resolve()
    candidates.extend(
        (
            module.parents[2] / "assets" / "codex_logo.png",
            module.parent / "codex_logo.png",
            Path.cwd() / "assets" / "codex_logo.png",
        )
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_window_icon() -> QtGui.QIcon:
    path = find_icon()
    if path is None:
        return QtGui.QIcon()
    return QtGui.QIcon(str(path))


def running_executable() -> str:
    argv0 = Path(sys.argv[0])
    if argv0.suffix == ".py" and argv0.exists():
        return f"{_quote(Path(sys.executable).resolve())} {_quote(argv0.resolve())}"
    if argv0.exists():
        return _quote(argv0.resolve())
    found = shutil.which("codex-widget")
    return _quote(Path(found)) if found else "codex-widget"


def desktop_entry_text(*, executable: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Codex Widget\n"
        "Comment=Floating desktop widget for Codex usage limits\n"
        f"Exec={executable}\n"
        "Terminal=false\n"
        f"Icon={ICON_NAME}\n"
        f"StartupWMClass={DESKTOP_FILE_NAME}\n"
        "StartupNotify=false\n"
        "Categories=Utility;\n"
    )


def ensure_desktop_integration(
    *,
    data_root: Path | None = None,
    executable: str | None = None,
    icon: Path | None = None,
    refresh_caches: bool = True,
) -> bool:
    """Install the hicolor icon and a desktop entry Plasma can match on Wayland.

    KDE looks the window icon up from the app id, not from the PNG path baked
    into the process. The app id has to be ``codex-widget`` and ``Icon`` has to
    be that theme name.
    """

    root = data_home() if data_root is None else data_root
    command = running_executable() if executable is None else executable
    source = find_icon() if icon is None else icon
    changed = False
    if source is not None and source.is_file():
        changed = _install_icon(source, root) or changed
    desktop = root / "applications" / f"{DESKTOP_FILE_NAME}.desktop"
    updated = _install_desktop_entry(desktop, command)
    changed = updated or changed
    if changed and refresh_caches:
        _refresh_caches(root)
    return changed


def _install_icon(source: Path, root: Path) -> bool:
    image = QtGui.QImage(str(source))
    if image.isNull():
        return False
    changed = False
    for size in ICON_SIZES:
        scaled = image.scaled(
            size,
            size,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if not scaled.save(buffer, "PNG"):
            continue
        destination = root / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"
        changed = _write_bytes(destination, bytes(buffer.data())) or changed
    return changed


def _install_desktop_entry(path: Path, executable: str) -> bool:
    updates = {
        "Type": "Application",
        "Name": "Codex Widget",
        "Comment": "Floating desktop widget for Codex usage limits",
        "Terminal": "false",
        "Icon": ICON_NAME,
        "StartupWMClass": DESKTOP_FILE_NAME,
        "StartupNotify": "false",
        "Categories": "Utility;",
    }
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        command = _entry_value(current, "Exec")
        if command is None or not _exec_exists(command):
            updates["Exec"] = executable
        updated = _upsert_keys(current, updates)
    else:
        updates["Exec"] = executable
        updated = desktop_entry_text(executable=executable)
    if path.is_file() and path.read_text(encoding="utf-8") == updated:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return True


def _upsert_keys(text: str, updates: dict[str, str]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        key, separator, _value = line.partition("=")
        if separator and not line.startswith("["):
            stripped = key.strip()
            if stripped in updates:
                lines.append(f"{stripped}={updates[stripped]}")
                seen.add(stripped)
                continue
        lines.append(line)
    lines.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    trailing = "\n" if text.endswith("\n") or not text else ""
    return "\n".join(lines) + trailing


def _entry_value(text: str, key: str) -> str | None:
    prefix = f"{key}="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip() or None
    return None


def _exec_exists(command: str) -> bool:
    try:
        parts = [part for part in shlex.split(command) if part and not part.startswith("%")]
    except ValueError:
        return False
    if not parts:
        return False
    program = parts[0]
    return Path(program).is_file() or shutil.which(program) is not None


def _write_bytes(path: Path, data: bytes) -> bool:
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return True


def _refresh_caches(root: Path) -> None:
    hicolor = root / "icons" / "hicolor"
    applications = root / "applications"
    commands = (
        ["gtk-update-icon-cache", "-f", "-t", str(hicolor)],
        ["update-desktop-database", str(applications)],
        ["kbuildsycoca6", "--noincremental"],
    )
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, check=False, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            continue


def _quote(path: Path | str) -> str:
    return shlex.quote(str(path))
