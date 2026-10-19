"""Parsing, editing and validation of Freedesktop desktop entries.

The editor preserves the original line layout, comments and unknown groups.
Common fields are patched in place instead of regenerating the file, so editing
an application does not destroy hand-written configuration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import DESKTOP_FOR_KEY, OVERRIDE_KEY
from .i18n import current_language_names

MAIN_GROUP = "Desktop Entry"

# Keys that the graphical editor exposes directly.  Everything else is still
# available through the field table and the raw text editor.
COMMON_KEYS = {
    "Type",
    "Version",
    "Name",
    "GenericName",
    "Comment",
    "Exec",
    "TryExec",
    "Icon",
    "Terminal",
    "Categories",
    "Keywords",
    "MimeType",
    "NoDisplay",
    "Hidden",
    "OnlyShowIn",
    "NotShowIn",
    "StartupNotify",
    "StartupWMClass",
    "Path",
    "DBusActivatable",
}


@dataclass
class _Line:
    text: str
    group: str | None = None
    key: str | None = None
    value: str | None = None


@dataclass
class ValidationResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    tool_missing: bool = False

    @property
    def summary(self) -> str:
        if self.ok and not self.messages:
            return "Desktop entry is valid."
        return "\n".join(self.messages)


def _unescape(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            result.append(char)
            index += 1
            continue
        following = value[index + 1]
        replacements = {
            "s": " ",
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "\\": "\\",
            ";": ";",
        }
        if following in replacements:
            result.append(replacements[following])
            index += 2
            continue
        # Unknown escape: keep it intact so we do not silently lose data.
        result.append(char)
        result.append(following)
        index += 2
    return "".join(result)


def _escape(value: str, *, escape_semicolon: bool = False) -> str:
    result: list[str] = []
    last = len(value) - 1
    for index, char in enumerate(value):
        if char == "\\":
            result.append("\\\\")
        elif char == "\n":
            result.append("\\n")
        elif char == "\t":
            result.append("\\t")
        elif char == "\r":
            result.append("\\r")
        elif char == ";" and escape_semicolon:
            result.append("\\;")
        elif char == " " and (index == 0 or index == last):
            result.append("\\s")
        else:
            result.append(char)
    return "".join(result)


def _split_list(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            current.append(char)
            current.append(value[index + 1])
            index += 2
            continue
        if char == ";":
            items.append(_unescape("".join(current)))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        items.append(_unescape("".join(current)))
    # A trailing semicolon is normal and must not create an empty item.
    return [item for item in items if item != ""]


def serialize_list(values: list[str]) -> str:
    if not values:
        return ""
    return ";".join(_escape(item, escape_semicolon=True) for item in values) + ";"


def parse_list(value: str | None) -> list[str]:
    return _split_list(value or "")


def _parse_lines(text: str) -> list[_Line]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[_Line] = []
    group: str | None = None
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            lines.append(_Line(raw, group))
            continue
        if stripped.startswith("#"):
            lines.append(_Line(raw, group))
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            group = stripped[1:-1]
            lines.append(_Line(raw, group))
            continue
        if "=" in raw:
            key, value = raw.split("=", 1)
            lines.append(_Line(raw, group, key.strip(), value))
            continue
        lines.append(_Line(raw, group))
    return lines


class DesktopFile:
    """An editable desktop entry that preserves the source layout."""

    def __init__(self, text: str = "") -> None:
        self._lines: list[_Line] = _parse_lines(text)

    @classmethod
    def from_file(cls, path: Path) -> "DesktopFile":
        return cls(Path(path).read_text(encoding="utf-8", errors="replace"))

    @classmethod
    def new(
        cls,
        *,
        name: str,
        exec_line: str = "",
        icon: str = "",
        comment: str = "",
        terminal: bool = False,
    ) -> "DesktopFile":
        entry = cls()
        entry.set(MAIN_GROUP, "Type", "Application")
        entry.set(MAIN_GROUP, "Version", "1.0")
        entry.set(MAIN_GROUP, "Name", name)
        if comment:
            entry.set(MAIN_GROUP, "Comment", comment)
        entry.set(MAIN_GROUP, "Exec", exec_line)
        if icon:
            entry.set(MAIN_GROUP, "Icon", icon)
        entry.set(MAIN_GROUP, "Terminal", "true" if terminal else "false")
        entry.set(MAIN_GROUP, "Categories", "")
        return entry

    def clone(self) -> "DesktopFile":
        return DesktopFile(self.to_text())

    def to_text(self) -> str:
        return "\n".join(line.text for line in self._lines).rstrip("\n") + "\n"

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------
    @property
    def groups(self) -> list[str]:
        seen: list[str] = []
        for line in self._lines:
            if line.group is not None and line.key is None and line.text.strip().startswith("["):
                if line.group not in seen:
                    seen.append(line.group)
        return seen

    def has_group(self, group: str) -> bool:
        return group in self.groups

    def keys(self, group: str = MAIN_GROUP) -> list[str]:
        result: list[str] = []
        for line in self._lines:
            if line.group == group and line.key is not None:
                if line.key not in result:
                    result.append(line.key)
        return result

    def items(self, group: str = MAIN_GROUP) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for line in self._lines:
            if line.group == group and line.key is not None and line.value is not None:
                result.append((line.key, line.value))
        return result

    def get_raw(self, group: str, key: str, default: str | None = None) -> str | None:
        found: str | None = None
        for line in self._lines:
            if line.group == group and line.key == key and line.value is not None:
                found = line.value
        return default if found is None else found

    def get(self, group: str, key: str, default: str | None = None) -> str | None:
        raw = self.get_raw(group, key)
        return default if raw is None else _unescape(raw)

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(MAIN_GROUP, key)
        if raw is None:
            return default
        return raw.strip().lower() == "true"

    def get_list(self, key: str) -> list[str]:
        return parse_list(self.get_raw(MAIN_GROUP, key))

    def get_localized(self, key: str) -> str | None:
        for language in current_language_names():
            value = self.get(MAIN_GROUP, f"{key}[{language}]")
            if value:
                return value
        return None

    @property
    def type(self) -> str:
        return (self.get(MAIN_GROUP, "Type") or "").strip()

    @property
    def is_application(self) -> bool:
        return self.type == "Application"

    @property
    def hidden(self) -> bool:
        return self.get_bool("Hidden")

    @property
    def no_display(self) -> bool:
        return self.get_bool("NoDisplay")

    @property
    def terminal(self) -> bool:
        return self.get_bool("Terminal")

    @property
    def dbus_activatable(self) -> bool:
        return self.get_bool("DBusActivatable")

    @property
    def name(self) -> str:
        return (
            self.get_localized("Name")
            or self.get(MAIN_GROUP, "Name")
            or self.get(MAIN_GROUP, "GenericName")
            or "Untitled application"
        )

    @property
    def unlocalized_name(self) -> str:
        return self.get(MAIN_GROUP, "Name") or ""

    @property
    def generic_name(self) -> str:
        return self.get_localized("GenericName") or self.get(MAIN_GROUP, "GenericName") or ""

    @property
    def comment(self) -> str:
        return self.get_localized("Comment") or self.get(MAIN_GROUP, "Comment") or ""

    @property
    def exec_line(self) -> str:
        return self.get(MAIN_GROUP, "Exec") or ""

    @property
    def try_exec(self) -> str:
        return self.get(MAIN_GROUP, "TryExec") or ""

    @property
    def icon_value(self) -> str:
        return self.get(MAIN_GROUP, "Icon") or ""

    @property
    def categories(self) -> list[str]:
        return self.get_list("Categories")

    @property
    def keywords(self) -> list[str]:
        return self.get_list("Keywords")

    @property
    def mime_types(self) -> list[str]:
        return self.get_list("MimeType")

    @property
    def only_show_in(self) -> list[str]:
        return self.get_list("OnlyShowIn")

    @property
    def not_show_in(self) -> list[str]:
        return self.get_list("NotShowIn")

    @property
    def startup_wm_class(self) -> str:
        return self.get(MAIN_GROUP, "StartupWMClass") or ""

    @property
    def working_directory(self) -> str:
        return self.get(MAIN_GROUP, "Path") or ""

    @property
    def is_our_override(self) -> bool:
        marker = self.get(MAIN_GROUP, OVERRIDE_KEY)
        return marker is not None and self.hidden

    @property
    def is_desktop_shortcut(self) -> bool:
        return self.get(MAIN_GROUP, DESKTOP_FOR_KEY) is not None

    @property
    def desktop_shortcut_for(self) -> str:
        return self.get(MAIN_GROUP, DESKTOP_FOR_KEY) or ""

    @property
    def try_exec_available(self) -> bool:
        command = self.try_exec.strip()
        if not command:
            return True
        if "/" in command:
            return Path(command).is_file() and os.access(command, os.X_OK)
        return shutil.which(command) is not None

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------
    def _group_bounds(self, group: str) -> tuple[int, int] | None:
        header = None
        for index, line in enumerate(self._lines):
            if line.group == group and line.key is None and line.text.strip() == f"[{group}]":
                header = index
                break
        if header is None:
            return None
        end = len(self._lines)
        for index in range(header + 1, len(self._lines)):
            line = self._lines[index]
            if line.group is not None and line.key is None and line.text.strip().startswith("["):
                end = index
                break
        return header, end

    def set(self, group: str, key: str, value: str) -> None:
        encoded = _escape(value)
        self._set_raw(group, key, encoded)

    def set_list(self, group: str, key: str, values: list[str]) -> None:
        self._set_raw(group, key, serialize_list(values))

    def _set_raw(self, group: str, key: str, encoded: str) -> None:
        bounds = self._group_bounds(group)
        if bounds is None:
            if self._lines and self._lines[-1].text.strip():
                self._lines.append(_Line("", None))
            self._lines.append(_Line(f"[{group}]", group))
            self._lines.append(_Line(f"{key}={encoded}", group, key, encoded))
            return

        header, end = bounds
        matching = [
            index
            for index in range(header + 1, end)
            if self._lines[index].key == key
        ]
        new_line = _Line(f"{key}={encoded}", group, key, encoded)
        if matching:
            # Replace the last occurrence, which is the effective one, and drop
            # earlier duplicates so the result is deterministic.
            self._lines[matching[-1]] = new_line
            for index in reversed(matching[:-1]):
                del self._lines[index]
            return

        insert_at = header + 1
        for index in range(header + 1, end):
            line = self._lines[index]
            if line.key is not None:
                insert_at = index + 1
        self._lines.insert(insert_at, new_line)

    def remove(self, group: str, key: str) -> None:
        bounds = self._group_bounds(group)
        if bounds is None:
            return
        header, end = bounds
        self._lines = [
            line
            for index, line in enumerate(self._lines)
            if not (header < index < end and line.key == key)
        ]

    def remove_group(self, group: str) -> None:
        bounds = self._group_bounds(group)
        if bounds is None:
            return
        header, end = bounds
        del self._lines[header:end]

    def set_bool(self, key: str, value: bool, *, keep_false: bool = True) -> None:
        if value or keep_false:
            self.set(MAIN_GROUP, key, "true" if value else "false")
        else:
            self.remove(MAIN_GROUP, key)

    def copy_for_desktop(self, desktop_id: str) -> "DesktopFile":
        """Create a desktop-directory copy that is independent of launcher state."""
        copy = self.clone()
        copy.remove(MAIN_GROUP, "Hidden")
        copy.remove(MAIN_GROUP, "NoDisplay")
        copy.set(MAIN_GROUP, DESKTOP_FOR_KEY, desktop_id)
        return copy

    def strip_launcher_keys(self) -> None:
        self.remove(MAIN_GROUP, "Hidden")
        self.remove(MAIN_GROUP, "NoDisplay")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> ValidationResult:
        return validate_desktop_text(self.to_text())


def _internal_validation(entry: DesktopFile) -> list[str]:
    messages: list[str] = []
    if not entry.has_group(MAIN_GROUP):
        messages.append("Error: missing [Desktop Entry] group.")
        return messages
    if not entry.is_application:
        messages.append("Error: Type must be Application.")
    if not entry.get(MAIN_GROUP, "Name"):
        messages.append("Error: Name is required.")
    if not entry.exec_line and not entry.dbus_activatable:
        messages.append("Error: Exec is required unless DBusActivatable=true.")
    return messages


def validate_desktop_text(text: str) -> ValidationResult:
    internal = _internal_validation(DesktopFile(text))
    validator = shutil.which("desktop-file-validate")
    if not validator:
        return ValidationResult(not internal, internal, tool_missing=True)

    with tempfile.TemporaryDirectory(prefix="launcher-editor-validate-") as temp:
        path = Path(temp) / "candidate.desktop"
        path.write_text(text, encoding="utf-8")
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        try:
            completed = subprocess.run(
                [validator, str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return ValidationResult(
                not internal,
                internal + [f"Could not run desktop-file-validate: {error}"],
                tool_missing=True,
            )
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    ).strip()
    messages = list(internal)
    if output:
        messages.extend(line for line in output.splitlines() if line.strip())
    ok = completed.returncode == 0 and not internal
    return ValidationResult(ok, messages)


def validate_desktop_file(path: Path) -> ValidationResult:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return ValidationResult(False, [f"Could not read file: {error}"])
    return validate_desktop_text(text)
