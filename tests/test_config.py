from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from launcher_editor.config import Config


class ConfigTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            config = Config(window_width=1000, window_height=700, last_filter="user")
            config.save(path)
            loaded = Config.load(path)
            self.assertEqual(loaded.window_width, 1000)
            self.assertEqual(loaded.window_height, 700)
            self.assertEqual(loaded.last_filter, "user")

    def test_invalid_values_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(
                json.dumps({"window_width": "wide", "window_height": {}, "last_filter": 4}),
                encoding="utf-8",
            )
            loaded = Config.load(path)
            self.assertEqual(loaded.window_width, 1080)
            self.assertEqual(loaded.window_height, 740)
            self.assertEqual(loaded.last_filter, "all")

    def test_broken_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(Config.load(path).last_filter, "all")


if __name__ == "__main__":
    unittest.main()
