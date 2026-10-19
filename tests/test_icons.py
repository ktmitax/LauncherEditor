from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from launcher_editor.icons import IconError, IconManager


def make_png(path: Path, width: int = 120, height: int = 60) -> None:
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, True, 8, width, height
    )
    pixbuf.fill(0x2ec4a6ff)
    pixbuf.savev(str(path), "png", [], [])


class IconManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.manifest = self.root / "state" / "icons.json"
        self.hicolor = self.root / "icons" / "hicolor"
        self.manager = IconManager(self.manifest, self.hicolor)
        self.source = self.root / "source.png"
        make_png(self.source)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_import_generates_square_sizes_and_manifest(self) -> None:
        icon = self.manager.import_file(self.source, "test-icon", sizes=(48, 64))
        self.assertEqual(icon.sizes, [48, 64])
        self.assertFalse(icon.scalable)
        for size in (48, 64):
            path = self.hicolor / f"{size}x{size}" / "apps" / "test-icon.png"
            self.assertTrue(path.exists())
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
            self.assertEqual((pixbuf.get_width(), pixbuf.get_height()), (size, size))
            self.assertTrue(pixbuf.get_has_alpha())
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertIn("test-icon", manifest["icons"])
        self.assertEqual(manifest["icons"]["test-icon"]["sizes"], [48, 64])
        self.assertTrue(Path(manifest["icons"]["test-icon"]["source"]).is_absolute())

    def test_reimport_removes_stale_sizes(self) -> None:
        self.manager.import_file(self.source, "test-icon", sizes=(48, 64))
        self.manager.import_file(self.source, "test-icon", sizes=(128,))
        self.assertFalse((self.hicolor / "48x48" / "apps" / "test-icon.png").exists())
        self.assertFalse((self.hicolor / "64x64" / "apps" / "test-icon.png").exists())
        self.assertTrue((self.hicolor / "128x128" / "apps" / "test-icon.png").exists())

    def test_svg_import(self) -> None:
        svg = self.root / "source.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
            '<rect width="64" height="64" rx="12" fill="#2ec4a6"/></svg>',
            encoding="utf-8",
        )
        try:
            icon = self.manager.import_file(svg, "vector-icon")
        except IconError as error:
            self.skipTest(f"SVG loader unavailable: {error}")
        self.assertTrue(icon.scalable)
        target = self.hicolor / "scalable" / "apps" / "vector-icon.svg"
        self.assertTrue(target.exists())

    def test_rejects_bad_name_and_missing_file(self) -> None:
        with self.assertRaises(IconError):
            self.manager.import_file(self.source, "../escape")
        with self.assertRaises(IconError):
            self.manager.import_file(self.root / "missing.png", "missing")

    def test_delete_icon(self) -> None:
        self.manager.import_file(self.source, "delete-me", sizes=(48,))
        self.manager.delete_icon("delete-me")
        self.assertFalse((self.hicolor / "48x48" / "apps" / "delete-me.png").exists())
        self.assertEqual(self.manager.list_managed(), [])

    def test_absolute_path_validation(self) -> None:
        self.assertEqual(
            self.manager.validate_absolute_icon(str(self.source)), self.source
        )
        with self.assertRaises(IconError):
            self.manager.validate_absolute_icon("relative.png")
        with self.assertRaises(IconError):
            self.manager.validate_absolute_icon(str(self.root / "missing.png"))

    def test_existing_icon_lookup_lists_files(self) -> None:
        self.manager.import_file(self.source, "listed", sizes=(48, 128))
        paths = self.manager.installed_paths("listed")
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(path.exists() for path in paths))
        managed = self.manager.list_managed()
        self.assertEqual([icon.name for icon in managed], ["listed"])


if __name__ == "__main__":
    unittest.main()
