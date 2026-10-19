from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launcher_editor.paths import (
    Source,
    application_directories,
    classify_application_path,
    desktop_dir,
    relative_desktop_id,
)


class PathTests(unittest.TestCase):
    def test_relative_ids(self) -> None:
        self.assertEqual(
            relative_desktop_id(Path("/apps"), Path("/apps/foo.desktop")),
            "foo",
        )
        self.assertEqual(
            relative_desktop_id(Path("/apps"), Path("/apps/sub/foo.desktop")),
            "sub-foo",
        )

    def test_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            user = home / ".local" / "share" / "applications" / "a.desktop"
            system = home / "system" / "applications" / "b.desktop"
            env = {
                "HOME": str(home),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_DATA_DIRS": str(home / "system"),
                "XDG_DESKTOP_DIR": str(home / "Desktop"),
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertIs(classify_application_path(user), Source.USER)
                self.assertIs(classify_application_path(system), Source.SYSTEM)

    def test_application_directory_order_and_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            env = {
                "HOME": str(home),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_DATA_DIRS": f"{home}/share1:{home}/share2:{home}/share1",
            }
            with patch.dict(os.environ, env, clear=False):
                directories = application_directories()
                self.assertIs(directories[0].source, Source.USER)
                paths = [str(item.path) for item in directories]
                self.assertEqual(len(paths), len(set(paths)))
                self.assertIn(str(home / "share1" / "applications"), paths)

    def test_desktop_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "桌面"
            with patch.dict(os.environ, {"XDG_DESKTOP_DIR": str(target)}, clear=False):
                self.assertEqual(desktop_dir(), target)

    def test_empty_xdg_data_dirs_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {"HOME": temp, "XDG_DATA_HOME": str(Path(temp) / ".local/share"), "XDG_DATA_DIRS": ""}
            with patch.dict(os.environ, env, clear=False):
                paths = [str(item.path) for item in application_directories()]
                self.assertIn("/usr/share/applications", paths)


if __name__ == "__main__":
    unittest.main()
