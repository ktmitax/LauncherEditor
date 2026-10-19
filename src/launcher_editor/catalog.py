"""Application discovery and reversible management operations."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import DESKTOP_FOR_KEY, OVERRIDE_KEY
from .desktop_entry import DesktopFile, ValidationResult
from .paths import (
    Source,
    application_directories,
    desktop_dir,
    ensure_directory,
    relative_desktop_id,
    user_applications_dir,
)
from .util import (
    atomic_write_text,
    move_to_trash,
    unlink_managed,
    validate_desktop_id,
)

_SAFE_DESKTOP_FILE = re.compile(r"[^A-Za-z0-9._-]+")


class CatalogError(Exception):
    """A user-facing management operation failed."""


class DesktopConflictError(CatalogError):
    """A desktop file exists that this application does not own."""


@dataclass
class AppRecord:
    id: str
    entry: DesktopFile
    path: Path
    source: Source
    desktop_path: Path | None = None
    desktop_managed: bool = False
    desktop_only: bool = False
    override_path: Path | None = None
    shadowed_path: Path | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def comment(self) -> str:
        return self.entry.comment

    @property
    def icon_value(self) -> str:
        return self.entry.icon_value

    @property
    def is_user_app(self) -> bool:
        return self.source is Source.USER and not self.desktop_only

    @property
    def is_system_app(self) -> bool:
        return self.source.is_system_managed and not self.desktop_only

    @property
    def is_flatpak(self) -> bool:
        return self.source is Source.FLATPAK

    @property
    def is_snap(self) -> bool:
        return self.source is Source.SNAP

    @property
    def can_edit(self) -> bool:
        if self.override_path == self.path and self.entry.is_our_override:
            return False
        return self.is_user_app or self.desktop_only

    @property
    def can_delete(self) -> bool:
        return self.is_user_app or self.desktop_only

    @property
    def hidden(self) -> bool:
        return self.entry.hidden or self.override_path is not None

    @property
    def no_display(self) -> bool:
        return self.entry.no_display

    @property
    def launcher_visible(self) -> bool:
        if self.desktop_only:
            return False
        return not self.hidden and not self.no_display

    @property
    def launcher_manageable(self) -> bool:
        if self.desktop_only or self.no_display:
            return False
        # An orphaned suppression stub has no application behind it and must
        # not be turned into an invalid launcher entry.
        if self.override_path == self.path and self.entry.is_our_override:
            return False
        return True

    @property
    def desktop_visible(self) -> bool:
        return self.desktop_path is not None

    @property
    def is_our_override(self) -> bool:
        return self.override_path is not None

    @property
    def source_key(self) -> str:
        if self.desktop_only:
            return "desktop"
        return self.source.value

    @property
    def source_label(self) -> str:
        # Translated in the UI layer; this is a stable technical label.
        if self.desktop_only:
            return "desktop"
        if self.source is Source.USER:
            return "user"
        if self.source is Source.FLATPAK:
            return "flatpak"
        if self.source is Source.SNAP:
            return "snap"
        return "system"

    def matches(self, query: str) -> bool:
        needle = query.casefold().strip()
        if not needle:
            return True
        haystack = " ".join(
            [
                self.id,
                self.name,
                self.comment,
                self.entry.generic_name,
                self.entry.exec_line,
                " ".join(self.entry.categories),
                " ".join(self.entry.keywords),
            ]
        ).casefold()
        return all(part in haystack for part in needle.split())


@dataclass
class DesktopAssociation:
    path: Path
    entry: DesktopFile
    managed: bool


class Catalog:
    """A snapshot of desktop entries and desktop-directory shortcuts."""

    def __init__(self) -> None:
        self.apps: list[AppRecord] = []
        self.issues: list[str] = []
        self.desktop_directory: Path = desktop_dir()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self.issues = []
        self.desktop_directory = desktop_dir()
        candidates: dict[str, list[tuple[Path, Source]]] = {}
        seen_paths: set[Path] = set()

        for directory in application_directories():
            base = directory.path
            if not base.is_dir():
                continue
            try:
                paths = sorted(base.rglob("*.desktop"))
            except OSError as error:
                self.issues.append(f"{base}: {error}")
                continue
            for path in paths:
                try:
                    resolved = path.resolve(strict=False)
                except OSError:
                    resolved = path
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                if not path.is_file():
                    continue
                desktop_id = relative_desktop_id(base, path)
                candidates.setdefault(desktop_id, []).append((path, directory.source))

        desktop_files = self._load_desktop_directory()
        records: dict[str, AppRecord] = {}

        for desktop_id in sorted(set(candidates) | set(desktop_files)):
            record = self._resolve_application(desktop_id, candidates.get(desktop_id, []))
            desktop = desktop_files.get(desktop_id)
            if record is None and desktop is not None:
                record = self._desktop_only_record(desktop_id, desktop)
            elif record is not None and desktop is not None:
                record.desktop_path = desktop.path
                record.desktop_managed = desktop.managed
            if record is None:
                continue
            records[desktop_id] = record

        # A desktop file whose marker points at a missing application is still a
        # user-owned file and should remain manageable.
        for desktop_id, desktop in desktop_files.items():
            if desktop_id in records:
                continue
            records[desktop_id] = self._desktop_only_record(desktop_id, desktop)

        self.apps = sorted(records.values(), key=_sort_key)

    def _load_desktop_directory(self) -> dict[str, DesktopAssociation]:
        result: dict[str, DesktopAssociation] = {}
        directory = self.desktop_directory
        if not directory.is_dir():
            return result
        try:
            paths = sorted(directory.glob("*.desktop"))
        except OSError as error:
            self.issues.append(f"{directory}: {error}")
            return result
        for path in paths:
            if not path.is_file():
                continue
            try:
                entry = DesktopFile.from_file(path)
            except OSError as error:
                self.issues.append(f"{path}: {error}")
                continue
            if not entry.is_application:
                continue
            marker = entry.desktop_shortcut_for
            desktop_id = marker or path.stem
            if desktop_id in result:
                self.issues.append(f"Multiple desktop shortcuts for {desktop_id}")
                continue
            result[desktop_id] = DesktopAssociation(
                path=path, entry=entry, managed=bool(marker)
            )
        return result

    def _resolve_application(
        self, desktop_id: str, files: list[tuple[Path, Source]]
    ) -> AppRecord | None:
        effective: tuple[Path, Source, DesktopFile] | None = None
        override: tuple[Path, Source, DesktopFile] | None = None

        for path, source in files:
            try:
                entry = DesktopFile.from_file(path)
            except OSError as error:
                self.issues.append(f"{path}: {error}")
                continue
            if entry.is_our_override and override is None:
                override = (path, source, entry)
                continue
            if entry.is_application:
                effective = (path, source, entry)
                break
            self.issues.append(f"{path}: not a Type=Application desktop entry")

        if effective is None:
            if override is not None:
                path, source, entry = override
                return AppRecord(
                    id=desktop_id,
                    entry=entry,
                    path=path,
                    source=source,
                    override_path=path,
                    issues=["Orphaned launcher override"],
                )
            return None

        path, source, entry = effective
        shadowed: Path | None = None
        if source is Source.USER:
            for lower_path, lower_source in files:
                if lower_source is not Source.USER:
                    shadowed = lower_path
                    break

        return AppRecord(
            id=desktop_id,
            entry=entry,
            path=path,
            source=source,
            override_path=override[0] if override else None,
            shadowed_path=shadowed,
        )

    def _desktop_only_record(
        self, desktop_id: str, desktop: DesktopAssociation
    ) -> AppRecord:
        return AppRecord(
            id=desktop_id,
            entry=desktop.entry,
            path=desktop.path,
            source=Source.DESKTOP,
            desktop_path=desktop.path,
            desktop_managed=desktop.managed,
            desktop_only=True,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get(self, desktop_id: str) -> AppRecord | None:
        for app in self.apps:
            if app.id == desktop_id:
                return app
        return None

    def counts(self) -> dict[str, int]:
        return {
            "all": len(self.apps),
            "user": sum(1 for app in self.apps if app.is_user_app),
            "system": sum(1 for app in self.apps if app.is_system_app),
            "hidden": sum(1 for app in self.apps if app.hidden and not app.desktop_only),
            "desktop": sum(1 for app in self.apps if app.desktop_visible),
        }

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def _validate_for_write(self, entry: DesktopFile) -> None:
        result = entry.validate()
        if not result.ok:
            raise CatalogError(_validation_message(result))

    def create_app(self, desktop_id: str, entry: DesktopFile) -> Path:
        if not validate_desktop_id(desktop_id):
            raise CatalogError(
                "The application ID may contain only letters, numbers, dots, "
                "underscores and hyphens."
            )
        directory = ensure_directory(user_applications_dir())
        target = directory / f"{desktop_id}.desktop"
        if target.exists() or target.is_symlink():
            raise CatalogError(f"An application with the ID {desktop_id} already exists.")
        self._validate_for_write(entry)
        atomic_write_text(target, entry.to_text(), mode=0o644)
        self._update_desktop_database()
        return target

    def save_app(self, record: AppRecord, entry: DesktopFile) -> None:
        if not record.can_edit:
            raise CatalogError("System applications are read-only.")
        target = record.path
        if target.is_symlink():
            raise CatalogError("Refusing to overwrite a symbolic link.")
        self._validate_for_write(entry)
        mode = 0o755 if record.desktop_only else 0o644
        atomic_write_text(target, entry.to_text(), mode=mode)
        if not record.desktop_only and record.desktop_path and record.desktop_managed:
            self._write_desktop_shortcut(record.id, entry, record.desktop_path)
        if record.is_user_app:
            self._update_desktop_database()

    def set_launcher_visible(self, record: AppRecord, visible: bool) -> None:
        if record.desktop_only:
            raise CatalogError(
                "This file only exists on the desktop. Add it to the launcher first."
            )
        if not record.launcher_manageable:
            raise CatalogError(
                "This application declares NoDisplay=true and cannot be shown "
                "from here. Edit it in the source view if it is a user application."
            )
        if record.source is Source.USER:
            entry = DesktopFile.from_file(record.path)
            if visible:
                entry.remove("Desktop Entry", "Hidden")
            else:
                entry.set("Desktop Entry", "Hidden", "true")
            self._validate_for_write(entry)
            if record.path.is_symlink():
                raise CatalogError("Refusing to overwrite a symbolic link.")
            atomic_write_text(record.path, entry.to_text(), mode=0o644)
            self._update_desktop_database()
            return

        if not record.source.is_system_managed:
            raise CatalogError("This application cannot be modified.")
        if visible:
            if record.entry.hidden:
                raise CatalogError(
                    "This system application is hidden by its package. "
                    "It cannot be shown without editing the package file."
                )
            if record.override_path:
                unlink_managed(record.override_path)
                self._update_desktop_database()
            return

        if record.entry.hidden:
            return
        override_path = ensure_directory(user_applications_dir()) / f"{record.id}.desktop"
        if override_path.exists() or override_path.is_symlink():
            raise CatalogError(
                f"{override_path} already exists and was not created by Launcher Editor."
            )
        stub = DesktopFile()
        stub.set("Desktop Entry", "Type", "Application")
        stub.set("Desktop Entry", "Name", record.entry.name)
        stub.set("Desktop Entry", "Hidden", "true")
        stub.set("Desktop Entry", OVERRIDE_KEY, "true")
        atomic_write_text(override_path, stub.to_text(), mode=0o644)
        self._update_desktop_database()

    def set_desktop_visible(
        self, record: AppRecord, visible: bool, *, force: bool = False
    ) -> None:
        if visible:
            if record.desktop_only:
                return
            target = self._desktop_target(record.id)
            if target.exists() or target.is_symlink():
                existing = None
                try:
                    existing = DesktopFile.from_file(target)
                except OSError:
                    pass
                managed = bool(existing and existing.is_desktop_shortcut)
                if not managed and not force:
                    raise DesktopConflictError(
                        f"{target.name} already exists on the desktop and was "
                        "not created by Launcher Editor."
                    )
            self._write_desktop_shortcut(record.id, record.entry, target)
            record.desktop_path = target
            record.desktop_managed = True
            return

        path = record.desktop_path
        if path is None:
            return
        if record.desktop_only:
            self._trash_desktop_file(path)
            record.desktop_path = None
            return
        if not record.desktop_managed and not force:
            raise DesktopConflictError(
                f"{path.name} was not created by Launcher Editor. "
                "Confirm that you want to remove it."
            )
        self._trash_desktop_file(path)
        record.desktop_path = None
        record.desktop_managed = False

    def delete_app(self, record: AppRecord) -> None:
        if not record.can_delete:
            raise CatalogError("System applications cannot be deleted.")
        if record.desktop_only:
            self._trash_desktop_file(record.path)
            return
        if record.desktop_path and record.desktop_managed:
            self._trash_desktop_file(record.desktop_path)
        if record.path.is_symlink():
            move_to_trash(record.path)
        else:
            move_to_trash(record.path)
        self._update_desktop_database()

    def add_desktop_only_to_launcher(self, record: AppRecord) -> None:
        if not record.desktop_only:
            return
        if not validate_desktop_id(record.id):
            raise CatalogError(
                "The application ID contains characters that are not safe for "
                "a launcher entry."
            )
        target = user_applications_dir() / f"{record.id}.desktop"
        if target.exists() or target.is_symlink():
            raise CatalogError(
                f"An application with the ID {record.id} already exists in the launcher."
            )
        entry = DesktopFile.from_file(record.path)
        entry.strip_launcher_keys()
        entry.remove("Desktop Entry", DESKTOP_FOR_KEY)
        self._validate_for_write(entry)
        ensure_directory(user_applications_dir())
        atomic_write_text(target, entry.to_text(), mode=0o644)
        self._update_desktop_database()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def desktop_target(self, desktop_id: str) -> Path:
        safe = _SAFE_DESKTOP_FILE.sub("-", desktop_id).strip("-.") or "application"
        return self.desktop_directory / f"{safe}.desktop"

    _desktop_target = desktop_target

    def _write_desktop_shortcut(
        self, desktop_id: str, entry: DesktopFile, target: Path
    ) -> None:
        if target.is_symlink():
            raise CatalogError("Refusing to overwrite a symbolic link on the desktop.")
        ensure_directory(target.parent)
        copy = entry.copy_for_desktop(desktop_id)
        atomic_write_text(target, copy.to_text(), mode=0o755)
        _set_trusted(target)

    def _trash_desktop_file(self, path: Path) -> None:
        try:
            move_to_trash(path)
        except OSError as error:
            raise CatalogError(str(error)) from error

    def _update_desktop_database(self) -> None:
        database = shutil.which("update-desktop-database")
        if not database:
            return
        directory = user_applications_dir()
        if not directory.is_dir():
            return
        try:
            subprocess.run(
                [database, str(directory)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def _validation_message(result: ValidationResult) -> str:
    if result.messages:
        return "\n".join(result.messages)
    return "The desktop entry is not valid."


def _sort_key(app: AppRecord) -> tuple[int, str]:
    # Desktop-only files after launcher entries, then alphabetical by localized
    # name.  Hidden entries stay in place instead of jumping around.
    return (1 if app.desktop_only else 0, app.name.casefold())


def _set_trusted(path: Path) -> None:
    """Best-effort GNOME trusted metadata for a desktop shortcut.

    KDE and most desktop icon implementations only need the executable bit.
    The metadata call is allowed to fail on systems without a metadata daemon.
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        gfile = Gio.File.new_for_path(str(path))
        try:
            gfile.set_attribute_string(
                "metadata::trusted",
                "true",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except GLib.Error:
            pass
    except (ImportError, ValueError):
        pass
