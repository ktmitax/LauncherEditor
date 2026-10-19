"""User hicolor icon library and import dialog."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GdkPixbuf, Gtk

from ..catalog import AppRecord, Catalog
from ..i18n import _
from ..icons import IconError, IconManager, ManagedIcon
from ..util import slugify
from .widgets import (
    choose_image,
    icon_tile,
    label,
    open_containing_folder,
    show_confirm,
    show_error,
)

# Imported icons are square; use the middle size for the library preview.
_PREVIEW_SIZE = 64


class ImportIconDialog(Adw.Window):
    def __init__(
        self,
        *,
        parent: Gtk.Window,
        icon_manager: IconManager,
        on_imported: Callable[[str], None],
    ) -> None:
        super().__init__(application=parent.get_application(), transient_for=parent, modal=True)
        self._icon_manager = icon_manager
        self._on_imported = on_imported
        self._source: Path | None = None
        self.set_default_size(640, 600)
        self.set_title(_("Import icon"))

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)
        import_button = Gtk.Button(label=_("Import"))
        import_button.add_css_class("suggested-action")
        import_button.connect("clicked", self._import)
        header.pack_end(import_button)
        outer.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(24)
        content.set_margin_end(24)

        content.append(label(_("Add an icon to your library"), "section-heading"))
        content.append(
            label(
                _(
                    "Launcher Editor stores the icon in the user hicolor theme so "
                    "applications can refer to it by name."
                ),
                "section-hint",
            )
        )

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        preview_box.add_css_class("import-preview")
        preview_box.set_halign(Gtk.Align.CENTER)
        self._preview = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        self._preview.set_pixel_size(96)
        preview_box.append(self._preview)
        self._file_label = label(_("No image selected"), "icon-selection")
        self._file_label.set_wrap(True)
        preview_box.append(self._file_label)
        content.append(preview_box)

        choose = Gtk.Button(label=_("Choose image"))
        choose.set_halign(Gtk.Align.CENTER)
        choose.connect("clicked", lambda _button: choose_image(self, _("Choose an image"), self._set_source))
        content.append(choose)

        group = Adw.PreferencesGroup(title=_("Icon name"))
        self._name_row = Adw.EntryRow(title=_("Name"))
        group.add(self._name_row)
        content.append(group)
        self._existing_label = label("", "section-hint")
        content.append(self._existing_label)
        self._name_row.connect("changed", lambda _row: self._update_existing_label())

        content.append(
            label(
                _(
                    "You can delete imported icons here later. Icons that are still "
                    "used by an application are protected."
                ),
                "section-hint",
            )
        )
        outer.append(content)
        self.set_content(outer)
        self.present()

    def _set_source(self, path: Path) -> None:
        self._source = path
        self._file_label.set_text(str(path))
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 96, 96, True)
            self._preview.set_from_pixbuf(pixbuf)
            self._preview.set_pixel_size(96)
        except Exception as error:
            show_error(self, _("Could not preview the image"), str(error))
        if not self._name_row.get_text().strip():
            self._name_row.set_text(slugify(path.stem, "custom-icon"))
        self._update_existing_label()

    def _update_existing_label(self) -> None:
        name = self._name_row.get_text().strip()
        if name and self._icon_manager.is_managed(name):
            self._existing_label.set_text(
                _("An icon with this name already exists and will be replaced.")
            )
        else:
            self._existing_label.set_text("")

    def _import(self, _button=None) -> None:
        if self._source is None:
            show_error(
                self,
                _("No image selected"),
                _("Choose an image before importing it."),
            )
            return
        name = self._name_row.get_text().strip()
        try:
            self._icon_manager.import_file(self._source, name)
        except (IconError, OSError) as error:
            show_error(self, _("Could not import the icon"), str(error))
            return
        self._on_imported(name)
        self.close()


class IconLibraryPage(Gtk.Box):
    def __init__(
        self,
        *,
        parent: Gtk.Window,
        catalog: Catalog,
        icon_manager: IconManager,
        on_changed: Callable[[], None],
        toast: Callable[[str], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._parent = parent
        self._catalog = catalog
        self._icon_manager = icon_manager
        self._on_changed = on_changed
        self._toast = toast

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toolbar.add_css_class("content-toolbar")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        titles.set_hexpand(True)
        self._subtitle = label(
            _("Only icons imported by Launcher Editor are listed here."),
            "page-subtitle",
        )
        titles.append(self._subtitle)
        toolbar.append(titles)
        open_folder = Gtk.Button(icon_name="folder-open-symbolic")
        open_folder.add_css_class("flat")
        open_folder.set_tooltip_text(_("Open the icon folder"))
        open_folder.connect(
            "clicked",
            lambda _button: open_containing_folder(self._icon_manager.hicolor_dir),
        )
        toolbar.append(open_folder)
        import_button = Gtk.Button(label=_("Import image"))
        import_button.add_css_class("suggested-action")
        import_button.connect("clicked", self._open_import)
        toolbar.append(import_button)
        self.append(toolbar)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_vexpand(True)
        self.append(self._stack)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_valign(Gtk.Align.START)
        self._list.set_margin_top(4)
        self._list.set_margin_bottom(18)
        self._list.set_margin_start(18)
        self._list.set_margin_end(18)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(840)
        clamp.set_child(self._list)
        scroller.set_child(clamp)
        self._stack.add_named(scroller, "list")

        empty = Adw.StatusPage()
        empty.set_icon_name("applications-graphics-symbolic")
        empty.set_title(_("No custom icons yet"))
        empty.set_description(
            _(
                "Import a PNG, JPEG, WebP or SVG image. It will be converted into "
                "the sizes desktop environments expect."
            )
        )
        empty_button = Gtk.Button(label=_("Import image"))
        empty_button.add_css_class("suggested-action")
        empty_button.set_halign(Gtk.Align.CENTER)
        empty_button.connect("clicked", self._open_import)
        empty.set_child(empty_button)
        self._stack.add_named(empty, "empty")

        self.refresh()

    def refresh(self) -> None:
        icons = self._icon_manager.list_managed()
        self._list.remove_all()
        if not icons:
            self._stack.set_visible_child_name("empty")
            return
        self._stack.set_visible_child_name("list")
        for icon in icons:
            self._list.append(self._make_row(icon))

    def _used_by(self, icon: ManagedIcon) -> list[AppRecord]:
        used: list[AppRecord] = []
        installed = {str(path) for path in self._icon_manager.installed_paths(icon.name)}
        for app in self._catalog.apps:
            value = app.icon_value
            if value == icon.name or value in installed:
                used.append(app)
        return used

    def _make_row(self, icon: ManagedIcon) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(icon_tile(self._preview_value(icon), 44))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.append(label(icon.name, "library-name"))
        used = self._used_by(icon)
        if used:
            used_text = _("Used by {count} applications").format(count=len(used))
        else:
            used_text = _("Not used by any application")
        text.append(
            label(
                _("Sizes {sizes}. {usage}").format(
                    sizes=icon.size_label, usage=used_text
                ),
                "library-meta",
            )
        )
        box.append(text)

        delete = Gtk.Button(icon_name="user-trash-symbolic")
        delete.add_css_class("flat")
        delete.set_tooltip_text(_("Delete icon"))
        delete.connect("clicked", lambda _button: self._delete(icon, used))
        box.append(delete)
        row.set_child(box)
        return row

    def _preview_value(self, icon: ManagedIcon) -> str:
        # The icon manager will resolve installed paths; for the row we prefer
        # the scalable image, then the largest PNG.
        base = self._icon_manager.hicolor_dir
        if icon.scalable:
            candidate = base / "scalable" / "apps" / f"{icon.name}.svg"
            if candidate.is_file():
                return str(candidate)
        for size in sorted(icon.sizes, reverse=True):
            candidate = base / f"{size}x{size}" / "apps" / f"{icon.name}.png"
            if candidate.is_file():
                return str(candidate)
        return ""

    def _open_import(self, _button=None) -> None:
        ImportIconDialog(
            parent=self._parent,
            icon_manager=self._icon_manager,
            on_imported=self._imported,
        )

    def _imported(self, name: str) -> None:
        self.refresh()
        self._on_changed()
        self._toast(_("Imported icon {name}").format(name=name))

    def _delete(self, icon: ManagedIcon, used: list[AppRecord]) -> None:
        if used:
            names = ", ".join(app.name for app in used[:4])
            if len(used) > 4:
                names += ", …"
            show_error(
                self._parent,
                _("Icon is still in use"),
                _(
                    "Change the icon of these applications first: {names}"
                ).format(names=names),
            )
            return
        show_confirm(
            self._parent,
            _("Delete {name}?").format(name=icon.name),
            _(
                "The icon files will be removed from your user icon theme. "
                "Applications are not affected."
            ),
            confirm_label=_("Delete"),
            destructive=True,
            callback=lambda: self._delete_confirmed(icon),
        )

    def _delete_confirmed(self, icon: ManagedIcon) -> None:
        try:
            self._icon_manager.delete_icon(icon.name)
        except OSError as error:
            show_error(self._parent, _("Could not delete the icon"), str(error))
            return
        self.refresh()
        self._on_changed()
        self._toast(_("Deleted icon {name}").format(name=icon.name))
