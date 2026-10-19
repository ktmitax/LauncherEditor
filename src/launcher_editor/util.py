"""Filesystem and small text helpers."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from pathlib import Path

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    """Write *text* atomically, replacing *path* only after a full flush."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not text.endswith("\n"):
        text += "\n"
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def move_to_trash(path: Path) -> None:
    """Move a user file to the trash, never permanently deleting it here."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    gfile = Gio.File.new_for_path(str(path))
    try:
        gfile.trash(None)
    except GLib.Error as error:
        raise OSError(f"Could not move {path} to the trash: {error.message}") from error


def unlink_managed(path: Path) -> None:
    """Remove a file that this application created and owns."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def validate_desktop_id(value: str) -> bool:
    return bool(_SAFE_ID_RE.fullmatch(value))


def slugify(value: str, fallback: str = "custom-app") -> str:
    """Create an ASCII desktop file ID stem from a display name."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower()
    ascii_value = re.sub(r"[^a-z0-9._-]+", "-", ascii_value)
    ascii_value = re.sub(r"-{2,}", "-", ascii_value).strip("-._")
    if not ascii_value:
        return fallback
    if not ascii_value[0].isalnum():
        ascii_value = fallback + "-" + ascii_value
    return ascii_value[:96]


def quote_exec_path(path: str) -> str:
    """Quote an executable path for the Exec key.

    The desktop entry Exec grammar is not a shell grammar.  Double quotes are
    the portable way to keep spaces in a single argument.  Characters that have
    a special meaning inside desktop-entry strings are escaped by the caller.
    """
    if not path:
        return path
    if not re.search(r"[\s\"'\\$`]", path):
        return path
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
