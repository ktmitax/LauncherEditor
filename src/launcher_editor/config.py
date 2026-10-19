"""Tiny JSON configuration store.

We intentionally avoid GSettings: the application only needs a handful of UI
preferences, and a JSON file keeps the Debian package free of schema compilation
and makes testing with an isolated HOME trivial.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import config_path
from .util import atomic_write_text


@dataclass
class Config:
    window_width: int = 1080
    window_height: int = 740
    last_filter: str = "all"
    show_system_details: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        target = path or config_path()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        allowed = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in allowed}
        try:
            config = cls(**values)
        except (TypeError, ValueError):
            return cls()
        try:
            config.window_width = max(760, min(2400, int(config.window_width)))
            config.window_height = max(520, min(1600, int(config.window_height)))
        except (TypeError, ValueError):
            return cls()
        if not isinstance(config.last_filter, str):
            config.last_filter = "all"
        return config

    def save(self, path: Path | None = None) -> None:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target,
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            mode=0o644,
        )
