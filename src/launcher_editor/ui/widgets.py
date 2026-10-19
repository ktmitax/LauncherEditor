"""Small reusable GTK widgets and view helpers."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from ..i18n import _

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FALLBACK_ICON = "application-x-executable"


def label(text: str = "", css_class: str | None = None, *, xalign: float = 0.0) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign)
    if css_class:
        widget.add_css_class(css_class)
    return widget


def margin(widget: Gtk.Widget, value: int) -> Gtk.Widget:
    widget.set_margin_top(value)
    widget.set_margin_bottom(value)
    widget.set_margin_start(value)
    widget.set_margin_end(value)
    return widget


def badge(text: str, kind: str = "system") -> Gtk.Label:
    widget = label(text, "badge")
    widget.add_css_class(f"badge-{kind}")
    widget.set_valign(Gtk.Align.CENTER)
    return widget


def _theme_has_icon(name: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return False
    try:
        theme = Gtk.IconTheme.get_for_display(display)
        return theme.has_icon(name)
    except Exception:
        return False


def _pixbuf_from_file(path: Path, size: int) -> GdkPixbuf.Pixbuf | None:
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), size, size, True
        )
    except Exception:
        return None


def image_for_icon(icon_value: str, size: int = 40) -> Gtk.Image:
    value = (icon_value or "").strip()
    if value:
        path = Path(value).expanduser()
        if path.is_absolute() and path.is_file():
            pixbuf = _pixbuf_from_file(path, size)
            if pixbuf is not None:
                image = Gtk.Image.new_from_pixbuf(pixbuf)
                image.set_pixel_size(size)
                return image
        if not value.startswith("/") and _theme_has_icon(value):
            image = Gtk.Image.new_from_icon_name(value)
            image.set_pixel_size(size)
            return image
    image = Gtk.Image.new_from_icon_name(_FALLBACK_ICON)
    image.set_pixel_size(size)
    return image


def icon_tile(icon_value: str, size: int = 40) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class("icon-tile")
    box.set_size_request(size + 12, size + 12)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    image = image_for_icon(icon_value, size)
    image.set_halign(Gtk.Align.CENTER)
    image.set_valign(Gtk.Align.CENTER)
    box.append(image)
    return box


def app_mark(size: int = 28) -> Gtk.Image:
    path = DATA_DIR / "app-icon.svg"
    pixbuf = _pixbuf_from_file(path, size)
    if pixbuf is not None:
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.set_pixel_size(size)
        return image
    image = Gtk.Image.new_from_icon_name(_FALLBACK_ICON)
    image.set_pixel_size(size)
    return image


def symbolic_icon(name: str, size: int = 16) -> Gtk.Image:
    fallback = "application-x-executable-symbolic"
    actual = name if _theme_has_icon(name) else fallback
    if not _theme_has_icon(actual):
        actual = _FALLBACK_ICON
    image = Gtk.Image.new_from_icon_name(actual)
    image.set_pixel_size(size)
    return image


def empty_illustration() -> Gtk.Image:
    path = DATA_DIR / "empty-apps.svg"
    pixbuf = _pixbuf_from_file(path, 220)
    if pixbuf is not None:
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.add_css_class("empty-illustration")
        return image
    return Gtk.Image.new_from_icon_name(_FALLBACK_ICON)


def open_containing_folder(path: Path) -> None:
    parent = path.parent if path.is_file() else path
    try:
        Gio.AppInfo.launch_default_for_uri(parent.as_uri(), None)
    except GLib.Error:
        pass


def show_error(parent: Gtk.Window, heading: str, body: str) -> None:
    # Gtk.MessageDialog is deprecated in newer GTK but is the only dialog that
    # exists on our GTK 4.6 baseline.  Using it keeps one code path and avoids
    # relying on Gtk.AlertDialog, which appeared in 4.10.
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=heading,
    )
    dialog.format_secondary_text(body)
    dialog.connect("response", lambda widget, _response: widget.destroy())
    dialog.present()


def show_confirm(
    parent: Gtk.Window,
    heading: str,
    body: str,
    *,
    confirm_label: str,
    destructive: bool = False,
    callback,
) -> None:
    from gi.repository import Adw

    dialog = Adw.MessageDialog(transient_for=parent, modal=True)
    dialog.set_heading(heading)
    dialog.set_body(body)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("confirm", confirm_label)
    if destructive:
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    else:
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog, response: str) -> None:
        if response == "confirm":
            callback()
        dialog.destroy()

    dialog.connect("response", on_response)
    dialog.present()


def file_filter_images() -> Gtk.FileFilter:
    file_filter = Gtk.FileFilter()
    file_filter.set_name(_("Images"))
    for mime in ("image/png", "image/jpeg", "image/webp", "image/svg+xml"):
        file_filter.add_mime_type(mime)
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.svg"):
        file_filter.add_pattern(pattern)
    return file_filter


def choose_image(
    parent: Gtk.Window, title: str, callback
) -> None:
    chooser = Gtk.FileChooserNative.new(
        title, parent, Gtk.FileChooserAction.OPEN, _("Open"), _("Cancel")
    )
    chooser.add_filter(file_filter_images())

    def on_response(dialog, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            gfile = dialog.get_file()
            if gfile is not None:
                callback(Path(gfile.get_path()))
        dialog.destroy()

    chooser.connect("response", on_response)
    chooser.show()


def choose_executable(parent: Gtk.Window, callback) -> None:
    chooser = Gtk.FileChooserNative.new(
        _("Choose an executable"),
        parent,
        Gtk.FileChooserAction.OPEN,
        _("Choose"),
        _("Cancel"),
    )

    def on_response(dialog, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            gfile = dialog.get_file()
            if gfile is not None:
                callback(gfile.get_path())
        dialog.destroy()

    chooser.connect("response", on_response)
    chooser.show()
