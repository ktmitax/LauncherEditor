from __future__ import annotations

import unittest

from launcher_editor import DESKTOP_FOR_KEY, OVERRIDE_KEY
from launcher_editor.desktop_entry import (
    DesktopFile,
    parse_list,
    serialize_list,
    validate_desktop_text,
)


SAMPLE = """[Desktop Entry]
# Keep this comment
Type=Application
Name=Test App
Name[zh_CN]=测试应用
GenericName=Test
Comment=Something useful
Exec=/usr/bin/test --flag %U
Icon=test-app
Terminal=false
Categories=Utility;Development;
Keywords=one;two;
MimeType=text/plain;image/png;
X-Custom=keep me

[Desktop Action new]
Name=New
Exec=/usr/bin/test --new
"""


class DesktopFileTests(unittest.TestCase):
    def test_parse_scalar_and_localized(self) -> None:
        entry = DesktopFile(SAMPLE)
        self.assertEqual(entry.unlocalized_name, "Test App")
        self.assertIn(entry.name, {"Test App", "测试应用"})
        self.assertEqual(entry.exec_line, "/usr/bin/test --flag %U")
        self.assertEqual(entry.categories, ["Utility", "Development"])
        self.assertEqual(entry.keywords, ["one", "two"])
        self.assertEqual(entry.mime_types, ["text/plain", "image/png"])
        self.assertFalse(entry.terminal)
        self.assertTrue(entry.is_application)

    def test_set_preserves_comments_unknown_keys_and_groups(self) -> None:
        entry = DesktopFile(SAMPLE)
        entry.set("Desktop Entry", "Comment", "Changed")
        entry.set("Desktop Entry", "X-New", "value")
        text = entry.to_text()
        self.assertIn("# Keep this comment", text)
        self.assertIn("X-Custom=keep me", text)
        self.assertIn("[Desktop Action new]", text)
        self.assertIn("Comment=Changed", text)
        self.assertIn("X-New=value", text)

    def test_remove_and_lists(self) -> None:
        entry = DesktopFile(SAMPLE)
        entry.remove("Desktop Entry", "Keywords")
        entry.set_list("Desktop Entry", "Categories", ["AudioVideo", "Utility"])
        self.assertEqual(entry.categories, ["AudioVideo", "Utility"])
        self.assertNotIn("Keywords", entry.keys())
        self.assertIn("Categories=AudioVideo;Utility;", entry.to_text())

    def test_duplicate_keys_use_last_and_are_normalized(self) -> None:
        entry = DesktopFile(
            "[Desktop Entry]\nType=Application\nName=First\nName=Last\nExec=/bin/true\n"
        )
        self.assertEqual(entry.name, "Last")
        entry.set("Desktop Entry", "Name", "Only")
        self.assertEqual(entry.to_text().count("Name="), 1)
        self.assertIn("Name=Only", entry.to_text())

    def test_escapes_and_list_parsing(self) -> None:
        entry = DesktopFile("[Desktop Entry]\nType=Application\nName=App\nExec=/bin/true\n")
        entry.set("Desktop Entry", "Comment", " leading and trailing ")
        entry.set("Desktop Entry", "X-Newline", "a\nb\tc\\d")
        self.assertEqual(entry.get("Desktop Entry", "Comment"), " leading and trailing ")
        self.assertEqual(entry.get("Desktop Entry", "X-Newline"), "a\nb\tc\\d")
        self.assertIn("\\sleading and trailing\\s", entry.to_text())
        self.assertEqual(parse_list("a;b\\;c;;"), ["a", "b;c"])
        self.assertEqual(serialize_list(["a", "b;c"]), "a;b\\;c;")

    def test_copy_for_desktop_strips_visibility_and_adds_marker(self) -> None:
        entry = DesktopFile(SAMPLE)
        entry.set("Desktop Entry", "Hidden", "true")
        entry.set("Desktop Entry", "NoDisplay", "true")
        copy = entry.copy_for_desktop("test-app")
        self.assertFalse(copy.hidden)
        self.assertFalse(copy.no_display)
        self.assertEqual(copy.get("Desktop Entry", DESKTOP_FOR_KEY), "test-app")
        self.assertTrue(copy.is_desktop_shortcut)

    def test_override_detection(self) -> None:
        entry = DesktopFile(
            f"[Desktop Entry]\nType=Application\nName=Hidden\nHidden=true\n{OVERRIDE_KEY}=true\n"
        )
        self.assertTrue(entry.is_our_override)
        self.assertTrue(entry.hidden)

    def test_validation_reports_missing_exec(self) -> None:
        result = validate_desktop_text(
            "[Desktop Entry]\nType=Application\nName=No Exec\n"
        )
        self.assertFalse(result.ok)

    def test_valid_entry(self) -> None:
        result = validate_desktop_text(
            "[Desktop Entry]\nType=Application\nName=Valid\nExec=/bin/true\n"
        )
        self.assertTrue(result.ok, result.summary)


if __name__ == "__main__":
    unittest.main()
