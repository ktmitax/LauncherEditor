from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from launcher_editor import DESKTOP_FOR_KEY, OVERRIDE_KEY
from launcher_editor.catalog import Catalog, CatalogError, DesktopConflictError
from launcher_editor.desktop_entry import DesktopFile
from launcher_editor.paths import ApplicationDirectory, Source


def write_app(path: Path, name: str, *, exec_line: str = "/bin/true", extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Exec={exec_line}\n"
        "Icon=application-x-executable\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"{extra}",
        encoding="utf-8",
    )


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.home = self.root / "home"
        self.system_share = self.root / "system" / "share"
        self.user_apps = self.home / ".local" / "share" / "applications"
        self.desktop = self.home / "Desktop"
        self.env = {
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_DATA_DIRS": str(self.system_share),
            "XDG_DESKTOP_DIR": str(self.desktop),
        }
        self._env_patch = patch.dict(os.environ, self.env, clear=False)
        self._env_patch.start()
        self._dirs_patch = patch(
            "launcher_editor.catalog.application_directories",
            return_value=[
                ApplicationDirectory(self.user_apps, Source.USER),
                ApplicationDirectory(self.system_share / "applications", Source.SYSTEM),
            ],
        )
        self._dirs_patch.start()
        write_app(self.system_share / "applications" / "org.example.system.desktop", "System App")
        write_app(
            self.system_share / "applications" / "org.example.override.desktop",
            "Overridden System",
        )
        write_app(self.user_apps / "com.example.user.desktop", "User App")
        write_app(
            self.user_apps / "org.example.override.desktop",
            "Override",
            extra=f"{OVERRIDE_KEY}=true\nHidden=true\n",
        )
        self.catalog = Catalog()

    def tearDown(self) -> None:
        self._dirs_patch.stop()
        self._env_patch.stop()
        self._temp.cleanup()

    def test_reload_classifies_and_detects_override(self) -> None:
        self.catalog.reload()
        self.assertEqual(len(self.catalog.issues), 0)
        user = self.catalog.get("com.example.user")
        self.assertIsNotNone(user)
        self.assertTrue(user.is_user_app)
        self.assertTrue(user.launcher_visible)
        system = self.catalog.get("org.example.system")
        self.assertTrue(system.is_system_app)
        overridden = self.catalog.get("org.example.override")
        self.assertTrue(overridden.hidden)
        self.assertIsNotNone(overridden.override_path)
        self.assertIs(system, self.catalog.get("org.example.system"))

    def test_user_visibility_toggle(self) -> None:
        self.catalog.reload()
        app = self.catalog.get("com.example.user")
        self.catalog.set_launcher_visible(app, False)
        self.catalog.reload()
        app = self.catalog.get("com.example.user")
        self.assertTrue(app.hidden)
        self.assertFalse(app.launcher_visible)
        self.catalog.set_launcher_visible(app, True)
        self.catalog.reload()
        app = self.catalog.get("com.example.user")
        self.assertFalse(app.hidden)
        self.assertTrue(app.launcher_visible)

    def test_system_override_toggle(self) -> None:
        self.catalog.reload()
        app = self.catalog.get("org.example.system")
        self.catalog.set_launcher_visible(app, False)
        override = self.user_apps / "org.example.system.desktop"
        self.assertTrue(override.exists())
        text = override.read_text(encoding="utf-8")
        self.assertIn("Hidden=true", text)
        self.assertIn(f"{OVERRIDE_KEY}=true", text)
        self.catalog.reload()
        app = self.catalog.get("org.example.system")
        self.assertTrue(app.hidden)
        self.assertFalse(app.launcher_visible)
        self.assertFalse(app.can_edit)
        self.catalog.set_launcher_visible(app, True)
        self.catalog.reload()
        app = self.catalog.get("org.example.system")
        self.assertFalse(app.hidden)
        self.assertTrue(app.launcher_visible)

    def test_desktop_shortcut_create_remove_and_conflict(self) -> None:
        self.catalog.reload()
        app = self.catalog.get("org.example.system")
        target = self.catalog.desktop_target(app.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("external", encoding="utf-8")
        with self.assertRaises(DesktopConflictError):
            self.catalog.set_desktop_visible(app, True)
        self.catalog.set_desktop_visible(app, True, force=True)
        self.catalog.reload()
        app = self.catalog.get("org.example.system")
        self.assertTrue(app.desktop_visible)
        self.assertTrue(app.desktop_managed)
        self.assertEqual(app.desktop_path.stat().st_mode & 0o777, 0o755)
        self.assertIn(
            f"{DESKTOP_FOR_KEY}={app.id}",
            app.desktop_path.read_text(encoding="utf-8"),
        )
        self.catalog.set_desktop_visible(app, False)
        self.catalog.reload()
        self.assertFalse(self.catalog.get("org.example.system").desktop_visible)

    def test_desktop_only_record_and_add_to_launcher(self) -> None:
        self.desktop.mkdir(parents=True, exist_ok=True)
        write_app(self.desktop / "only.desktop", "Only Desktop")
        self.catalog.reload()
        app = self.catalog.get("only")
        self.assertIsNotNone(app)
        self.assertTrue(app.desktop_only)
        self.assertFalse(app.launcher_visible)
        self.assertTrue(app.can_edit)
        self.catalog.add_desktop_only_to_launcher(app)
        self.catalog.reload()
        self.assertIsNotNone(self.catalog.get("only"))
        self.assertFalse(self.catalog.get("only").desktop_only)
        self.assertTrue(self.catalog.get("only").launcher_visible)

    def test_create_and_save_application(self) -> None:
        entry = DesktopFile.new(name="Created", exec_line="/bin/true")
        self.catalog.create_app("com.example.created", entry)
        target = self.user_apps / "com.example.created.desktop"
        self.assertTrue(target.exists())
        self.catalog.reload()
        app = self.catalog.get("com.example.created")
        self.assertEqual(app.name, "Created")
        entry = app.entry.clone()
        entry.set("Desktop Entry", "Comment", "Edited")
        self.catalog.save_app(app, entry)
        self.assertIn("Comment=Edited", target.read_text(encoding="utf-8"))
        with self.assertRaises(CatalogError):
            self.catalog.create_app("com.example.created", entry)

    def test_delete_uses_trash_helper(self) -> None:
        self.catalog.reload()
        app = self.catalog.get("com.example.user")
        removed: list[Path] = []

        def fake_trash(path: Path) -> None:
            removed.append(path)
            path.unlink()

        with patch("launcher_editor.catalog.move_to_trash", side_effect=fake_trash):
            self.catalog.delete_app(app)
        self.assertEqual(removed, [app.path])
        self.catalog.reload()
        self.assertIsNone(self.catalog.get("com.example.user"))

    def test_desktop_only_edit_keeps_executable_mode(self) -> None:
        self.desktop.mkdir(parents=True, exist_ok=True)
        path = self.desktop / "mode.desktop"
        write_app(path, "Mode")
        path.chmod(0o755)
        self.catalog.reload()
        app = self.catalog.get("mode")
        entry = app.entry.clone()
        entry.set("Desktop Entry", "Comment", "Changed")
        self.catalog.save_app(app, entry)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_counts(self) -> None:
        self.catalog.reload()
        counts = self.catalog.counts()
        self.assertEqual(counts["all"], 3)
        self.assertEqual(counts["user"], 1)
        self.assertEqual(counts["system"], 2)
        self.assertEqual(counts["hidden"], 1)


if __name__ == "__main__":
    unittest.main()
