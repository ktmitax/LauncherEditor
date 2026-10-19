"""XDG paths and source classification.

All write paths are user-owned.  System application directories are only ever
read, which keeps the tool usable without root privileges and on immutable
systems.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import APP_DATA_DIR


class Source(str, Enum):
    USER = "user"
    SYSTEM = "system"
    FLATPAK = "flatpak"
    SNAP = "snap"
    DESKTOP = "desktop"

    @property
    def is_system_managed(self) -> bool:
        return self in {Source.SYSTEM, Source.FLATPAK, Source.SNAP}


@dataclass(frozen=True)
class ApplicationDirectory:
    path: Path
    source: Source


def home() -> Path:
    return Path(os.path.expanduser("~"))


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if value:
        return Path(value).expanduser()
    return None


def user_data_home() -> Path:
    return _env_path("XDG_DATA_HOME") or home() / ".local" / "share"


def user_config_home() -> Path:
    return _env_path("XDG_CONFIG_HOME") or home() / ".config"


def user_cache_home() -> Path:
    return _env_path("XDG_CACHE_HOME") or home() / ".cache"


def user_applications_dir() -> Path:
    return user_data_home() / "applications"


def user_icons_dir() -> Path:
    return user_data_home() / "icons"


def user_hicolor_dir() -> Path:
    return user_icons_dir() / "hicolor"


def state_dir() -> Path:
    return user_data_home() / APP_DATA_DIR


def config_dir() -> Path:
    return user_config_home() / APP_DATA_DIR


def config_path() -> Path:
    return config_dir() / "config.json"


def icon_manifest_path() -> Path:
    return state_dir() / "icons.json"


def _read_xdg_data_dirs() -> list[Path]:
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    result: list[Path] = []
    for item in raw.split(":"):
        if not item:
            continue
        path = Path(item).expanduser()
        if path not in result:
            result.append(path)
    return result


def application_directories() -> list[ApplicationDirectory]:
    """Return application directories in XDG precedence order."""
    candidates: list[ApplicationDirectory] = [
        ApplicationDirectory(user_applications_dir(), Source.USER)
    ]

    user_flatpak = (
        user_data_home() / "flatpak" / "exports" / "share" / "applications"
    )
    system_flatpak = Path("/var/lib/flatpak/exports/share/applications")
    snap_dir = Path("/var/lib/snapd/desktop/applications")

    for base in _read_xdg_data_dirs():
        path = base / "applications"
        if path == user_flatpak or path == system_flatpak:
            source = Source.FLATPAK
        elif path == snap_dir:
            source = Source.SNAP
        else:
            source = Source.SYSTEM
        candidates.append(ApplicationDirectory(path, source))

    # Flatpak and Snap are not always present in XDG_DATA_DIRS (for example in
    # a minimal session), so add the conventional locations explicitly.
    candidates.extend(
        [
            ApplicationDirectory(user_flatpak, Source.FLATPAK),
            ApplicationDirectory(system_flatpak, Source.FLATPAK),
            ApplicationDirectory(snap_dir, Source.SNAP),
        ]
    )

    result: list[ApplicationDirectory] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            key = candidate.path.resolve(strict=False)
        except OSError:
            key = candidate.path
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _desktop_from_user_dirs_config() -> Path | None:
    """Read the XDG desktop directory without depending on xdg-user-dir.

    user-dirs.dirs is shell syntax, not a key file.  We accept the two common
    quoting styles and only understand the XDG_DESKTOP_DIR assignment.
    """
    config = user_config_home() / "user-dirs.dirs"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_DESKTOP_DIR="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        value = value.replace("$HOME", str(home()))
        if value:
            return Path(value)
    return None


def desktop_dir() -> Path:
    """Return the user's desktop directory.

    Preference order: explicit XDG_DESKTOP_DIR for tests, xdg-user-dir,
    user-dirs.dirs, an existing ~/Desktop, an existing ~/桌面, then ~/Desktop.
    """
    override = _env_path("XDG_DESKTOP_DIR")
    if override:
        return override

    if shutil.which("xdg-user-dir"):
        try:
            completed = subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            value = completed.stdout.strip()
            if completed.returncode == 0 and value:
                return Path(value)
        except (OSError, subprocess.SubprocessError):
            pass

    configured = _desktop_from_user_dirs_config()
    if configured:
        return configured

    for candidate in (home() / "Desktop", home() / "桌面"):
        if candidate.is_dir():
            return candidate
    return home() / "Desktop"


def classify_application_path(path: Path) -> Source:
    """Classify a path by the directory it lives in."""
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    user_apps = user_applications_dir().resolve(strict=False)
    if resolved == user_apps or user_apps in resolved.parents:
        return Source.USER

    for directory in application_directories():
        if directory.source is Source.USER:
            continue
        try:
            base = directory.path.resolve(strict=False)
        except OSError:
            base = directory.path
        if resolved == base or base in resolved.parents:
            return directory.source
    return Source.SYSTEM


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    return path


def relative_desktop_id(base: Path, path: Path) -> str:
    """Compute a desktop file ID from a path inside an application directory."""
    try:
        relative = path.relative_to(base)
    except ValueError:
        relative = Path(path.name)
    parts = list(relative.parts)
    if parts:
        parts[-1] = Path(parts[-1]).stem
    return "-".join(parts)


def desktop_file_locations() -> list[Path]:
    """Existing application directories, in precedence order."""
    return [item.path for item in application_directories() if item.path.is_dir()]
