"""Icon discovery, import and conversion into the user hicolor theme."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .paths import ensure_directory, icon_manifest_path, user_hicolor_dir
from .util import atomic_write_bytes, atomic_write_text

DEFAULT_SIZES = (48, 64, 128, 256, 512)
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000
MAX_SOURCE_DIMENSION = 10_000
ICON_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


class IconError(Exception):
    """A user-facing icon operation failed."""


@dataclass
class ManagedIcon:
    name: str
    source: str = ""
    sizes: list[int] = field(default_factory=list)
    scalable: bool = False
    created: str = ""

    @property
    def size_label(self) -> str:
        if self.scalable and not self.sizes:
            return "SVG"
        parts = [str(size) for size in self.sizes]
        if self.scalable:
            parts.append("SVG")
        return " / ".join(parts)


def _load_gdkpixbuf():
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    return GdkPixbuf


class IconManager:
    def __init__(self, manifest_path: Path | None = None, hicolor_dir: Path | None = None):
        self.manifest_path = manifest_path or icon_manifest_path()
        self.hicolor_dir = hicolor_dir or user_hicolor_dir()

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------
    def _load_manifest(self) -> dict:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"version": 1, "icons": {}}
        if not isinstance(data, dict):
            return {"version": 1, "icons": {}}
        icons = data.get("icons")
        if not isinstance(icons, dict):
            data["icons"] = {}
        data.setdefault("version", 1)
        return data

    def _save_manifest(self, data: dict) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic_write_text(
            self.manifest_path,
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o644,
        )

    def list_managed(self) -> list[ManagedIcon]:
        icons: list[ManagedIcon] = []
        manifest = self._load_manifest()
        for name, raw in manifest.get("icons", {}).items():
            if not isinstance(raw, dict):
                continue
            sizes = [int(size) for size in raw.get("sizes", []) if isinstance(size, int)]
            scalable = bool(raw.get("scalable"))
            if not sizes and not scalable:
                continue
            icons.append(
                ManagedIcon(
                    name=name,
                    source=str(raw.get("source", "")),
                    sizes=sorted(sizes),
                    scalable=scalable,
                    created=str(raw.get("created", "")),
                )
            )
        return sorted(icons, key=lambda icon: icon.name.casefold())

    def is_managed(self, name: str) -> bool:
        return name in self._load_manifest().get("icons", {})

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    def import_file(
        self,
        source: Path,
        name: str,
        sizes: tuple[int, ...] = DEFAULT_SIZES,
    ) -> ManagedIcon:
        source = Path(source)
        if not ICON_NAME_RE.fullmatch(name):
            raise IconError(
                "The icon name may contain only letters, numbers, dots, "
                "underscores and hyphens."
            )
        if not source.is_file():
            raise IconError(f"{source} is not a file.")
        try:
            if source.stat().st_size > MAX_SOURCE_BYTES:
                raise IconError("The image is larger than 25 MB.")
        except OSError as error:
            raise IconError(str(error)) from error

        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise IconError("Supported image formats are PNG, JPEG, WebP and SVG.")

        manifest = self._load_manifest()
        old = manifest.get("icons", {}).get(name, {})
        old_sizes = [int(size) for size in old.get("sizes", []) if isinstance(size, int)]
        old_scalable = bool(old.get("scalable"))

        if suffix == ".svg":
            self._import_svg(source, name)
            new_sizes: list[int] = []
            new_scalable = True
        else:
            new_sizes = self._import_raster(source, name, sizes)
            new_scalable = False

        self._remove_stale(name, old_sizes, old_scalable, new_sizes, new_scalable)
        record = {
            "source": str(source.resolve()),
            "sizes": new_sizes,
            "scalable": new_scalable,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        manifest.setdefault("icons", {})[name] = record
        self._save_manifest(manifest)
        self._update_icon_cache()
        return ManagedIcon(
            name=name,
            source=str(source),
            sizes=new_sizes,
            scalable=new_scalable,
            created=record["created"],
        )

    def _import_svg(self, source: Path, name: str) -> None:
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise IconError("The SVG is larger than 25 MB.")
        data = source.read_bytes()
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise IconError("SVG files with a DOCTYPE or entity declaration are not accepted.")
        # Make sure the installed icon can actually be loaded by GdkPixbuf.
        GdkPixbuf = _load_gdkpixbuf()
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(source))
        except Exception as error:
            raise IconError(f"The SVG could not be loaded: {error}") from error
        if pixbuf.get_width() > MAX_SOURCE_DIMENSION or pixbuf.get_height() > MAX_SOURCE_DIMENSION:
            raise IconError("The SVG renders to an image that is too large.")
        target = self.hicolor_dir / "scalable" / "apps" / f"{name}.svg"
        ensure_directory(target.parent)
        if target.is_symlink():
            raise IconError("Refusing to overwrite a symbolic link in the icon theme.")
        atomic_write_bytes(target, data, mode=0o644)

    def _import_raster(
        self, source: Path, name: str, sizes: tuple[int, ...]
    ) -> list[int]:
        GdkPixbuf = _load_gdkpixbuf()
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(source))
        except Exception as error:
            raise IconError(f"The image could not be loaded: {error}") from error
        try:
            oriented = pixbuf.apply_embedded_orientation()
            if oriented is not None:
                pixbuf = oriented
        except Exception:
            pass
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            raise IconError("The image has no usable pixels.")
        if width > MAX_SOURCE_DIMENSION or height > MAX_SOURCE_DIMENSION:
            raise IconError("The image dimensions are too large.")
        if width * height > MAX_SOURCE_PIXELS:
            raise IconError("The image contains too many pixels.")

        # Generate every size before touching the installed icon, so a failed
        # conversion cannot leave a half-written theme entry behind.
        generated: dict[int, bytes] = {}
        with tempfile.TemporaryDirectory(prefix="launcher-editor-icon-") as temp:
            temp_dir = Path(temp)
            for size in sorted(set(sizes)):
                if size < 16 or size > 1024:
                    continue
                canvas = self._square_icon(GdkPixbuf, pixbuf, size)
                temp_path = temp_dir / f"{size}.png"
                canvas.savev(str(temp_path), "png", [], [])
                generated[size] = temp_path.read_bytes()
        if not generated:
            raise IconError("No valid icon sizes were requested.")

        for size, data in generated.items():
            target = self.hicolor_dir / f"{size}x{size}" / "apps" / f"{name}.png"
            ensure_directory(target.parent)
            if target.is_symlink():
                raise IconError(f"Refusing to overwrite the symbolic link {target}.")
            atomic_write_bytes(target, data, mode=0o644)
        return sorted(generated)

    @staticmethod
    def _square_icon(GdkPixbuf, pixbuf, size: int):
        width, height = pixbuf.get_width(), pixbuf.get_height()
        factor = min(size / width, size / height)
        target_width = max(1, min(size, round(width * factor)))
        target_height = max(1, min(size, round(height * factor)))
        interpolation = GdkPixbuf.InterpType.BILINEAR
        scaled = pixbuf.scale_simple(target_width, target_height, interpolation)
        if scaled is None:
            raise IconError("The image could not be scaled.")
        canvas = GdkPixbuf.Pixbuf.new(
            GdkPixbuf.Colorspace.RGB, True, 8, size, size
        )
        canvas.fill(0x00000000)
        left = (size - target_width) // 2
        top = (size - target_height) // 2
        scaled.composite(
            canvas,
            left,
            top,
            target_width,
            target_height,
            0,
            0,
            1,
            1,
            interpolation,
            255,
        )
        return canvas

    def _remove_stale(
        self,
        name: str,
        old_sizes: list[int],
        old_scalable: bool,
        new_sizes: list[int],
        new_scalable: bool,
    ) -> None:
        for size in set(old_sizes) - set(new_sizes):
            path = self.hicolor_dir / f"{size}x{size}" / "apps" / f"{name}.png"
            try:
                path.unlink()
            except OSError:
                pass
        if old_scalable and not new_scalable:
            path = self.hicolor_dir / "scalable" / "apps" / f"{name}.svg"
            try:
                path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete_icon(self, name: str) -> None:
        manifest = self._load_manifest()
        record = manifest.get("icons", {}).pop(name, None)
        if record is None:
            return
        sizes = [int(size) for size in record.get("sizes", []) if isinstance(size, int)]
        for size in sizes:
            path = self.hicolor_dir / f"{size}x{size}" / "apps" / f"{name}.png"
            try:
                path.unlink()
            except OSError:
                pass
        if record.get("scalable"):
            path = self.hicolor_dir / "scalable" / "apps" / f"{name}.svg"
            try:
                path.unlink()
            except OSError:
                pass
        self._save_manifest(manifest)
        self._update_icon_cache()

    # ------------------------------------------------------------------
    # Absolute paths and validation
    # ------------------------------------------------------------------
    @staticmethod
    def validate_absolute_icon(path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise IconError("An absolute icon path must start with /.")
        if not candidate.is_file():
            raise IconError(f"{candidate} is not a file.")
        try:
            if candidate.stat().st_size > MAX_SOURCE_BYTES:
                raise IconError("The image is larger than 25 MB.")
        except OSError as error:
            raise IconError(str(error)) from error
        if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise IconError("Supported image formats are PNG, JPEG, WebP and SVG.")
        GdkPixbuf = _load_gdkpixbuf()
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(candidate))
        except Exception as error:
            raise IconError(f"The image could not be loaded: {error}") from error
        if pixbuf.get_width() > MAX_SOURCE_DIMENSION or pixbuf.get_height() > MAX_SOURCE_DIMENSION:
            raise IconError("The image dimensions are too large.")
        return candidate

    def installed_paths(self, name: str) -> list[Path]:
        record = self._load_manifest().get("icons", {}).get(name, {})
        paths: list[Path] = []
        for size in record.get("sizes", []):
            if isinstance(size, int):
                paths.append(self.hicolor_dir / f"{size}x{size}" / "apps" / f"{name}.png")
        if record.get("scalable"):
            paths.append(self.hicolor_dir / "scalable" / "apps" / f"{name}.svg")
        return paths

    def _update_icon_cache(self) -> None:
        # A user hicolor overlay normally has no index.theme, so the cache tool
        # is optional.  Failure is never fatal: icon lookup scans the theme
        # directories even without a cache file.
        if not (self.hicolor_dir / "index.theme").is_file():
            return
        tool = shutil.which("gtk4-update-icon-cache") or shutil.which(
            "gtk-update-icon-cache"
        )
        if not tool:
            return
        try:
            subprocess.run(
                [tool, "-f", "-t", str(self.hicolor_dir)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
