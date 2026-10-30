import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from codex_widget.desktop import ensure_desktop_integration


class DesktopIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _icon(self, directory: Path) -> Path:
        path = directory / "codex_logo.png"
        image = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32)
        image.fill(QtCore.Qt.GlobalColor.white)
        image.save(str(path), "PNG")
        return path

    def test_installs_a_theme_icon_and_repairs_a_broken_desktop_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            icon = self._icon(root)
            applications = root / "applications"
            applications.mkdir()
            desktop = applications / "codex-widget.desktop"
            desktop.write_text(
                "\n".join(
                    (
                        "[Desktop Entry]",
                        "Type=Application",
                        "Name=Codex Widget",
                        "Exec=/home/fabio/projetos/codex-widget/.venv/bin/codex-widget %u",
                        "Icon=/home/fabio/projetos/codex-widget/assets/codex_logo.png",
                        "Terminal=false",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            changed = ensure_desktop_integration(
                data_root=root,
                executable="/usr/bin/codex-widget",
                icon=icon,
                refresh_caches=False,
            )

            self.assertTrue(changed)
            text = desktop.read_text(encoding="utf-8")
            self.assertIn("Icon=codex-widget\n", text)
            self.assertIn("StartupWMClass=codex-widget\n", text)
            self.assertIn("Exec=/usr/bin/codex-widget\n", text)
            self.assertNotIn("/home/fabio/", text)
            for size in (48, 256, 512):
                installed = root / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "codex-widget.png"
                self.assertTrue(installed.is_file(), installed)
                restored = QtGui.QImage(str(installed))
                self.assertEqual(restored.width(), size)
                self.assertEqual(restored.height(), size)

    def test_keeps_a_working_exec_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            icon = self._icon(root)
            applications = root / "applications"
            applications.mkdir()
            desktop = applications / "codex-widget.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nExec=/bin/true\nIcon=codex-widget\n"
                "StartupWMClass=codex-widget\nStartupNotify=false\n",
                encoding="utf-8",
            )

            ensure_desktop_integration(
                data_root=root,
                executable="/usr/bin/codex-widget",
                icon=icon,
                refresh_caches=False,
            )

            self.assertIn("Exec=/bin/true\n", desktop.read_text(encoding="utf-8"))
