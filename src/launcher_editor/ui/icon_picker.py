"""Icon chooser: installed theme icons, image import, or absolute path."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk

from ..i18n import _
from ..icons import IconError, IconManager
from .widgets import choose_image, label, show_error

MAX_THEME_RESULTS = 280


class IconPickerWindow(Adw.Window):
    def __init__(
        self,
        *,
        parent: Gtk.Window,
        icon_manager: IconManager,
        current_icon: str,
        suggested_name: str,
        on_selected: Callable[[str], None],
        popular_icons: list[str] | None = None,
    ) -> None:
        super().__init__(application=parent.get_application(), transient_for=parent, modal=True)
        self._icon_manager = icon_manager
        self._current_icon = current_icon.strip()
        self._suggested_name = suggested_name
        self._on_selected = on_selected
        self._selected_theme: str | None = None
        self._selected_icon: str | None = None
        self._theme_buttons: dict[str, Gtk.ToggleButton] = {}
        self._updating_theme = False
        self._import_source: Path | None = None
        self._search_timeout: int | None = None
        self._all_theme_names: list[str] = []
        self._popular_icons: list[str] = []
        self._load_theme_names(popular_icons or [])

        self.set_default_size(780, 700)
        self.set_title(_("Choose icon"))

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._header = Adw.HeaderBar()
        self._header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: self.close())
        self._header.pack_start(cancel)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(120)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self._stack)
        self._header.set_title_widget(switcher)

        self._use_button = Gtk.Button(label=_("Use icon"))
        self._use_button.add_css_class("suggested-action")
        self._use_button.connect("clicked", self._use)
        self._header.pack_end(self._use_button)
        outer.append(self._header)
        outer.append(self._stack)

        self._theme_page = self._build_theme_page()
        self._import_page = self._build_import_page()
        self._path_page = self._build_path_page()
        self._stack.add_titled(self._theme_page, "theme", _("Theme icons"))
        self._stack.add_titled(self._import_page, "import", _("Import image"))
        self._stack.add_titled(self._path_page, "path", _("Absolute path"))
        self._stack.connect("notify::visible-child", self._on_page_changed)

        self.set_content(outer)
        self._update_use_button()
        self.present()

    # ------------------------------------------------------------------
    # Theme page
    # ------------------------------------------------------------------
    def _load_theme_names(self, popular_icons: list[str]) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        try:
            theme = Gtk.IconTheme.get_for_display(display)
            names = theme.get_icon_names()
        except Exception:
            names = []
        seen: set[str] = set()
        for name in names:
            if not name or name.endswith("-symbolic") or name.endswith("-rtl"):
                continue
            if name in seen:
                continue
            seen.add(name)
            self._all_theme_names.append(name)
        self._all_theme_names.sort(key=str.casefold)
        for value in popular_icons:
            if value and not value.startswith("/") and value in seen:
                if value not in self._popular_icons:
                    self._popular_icons.append(value)
        self._all_theme_names = [
            name for name in self._all_theme_names if name not in self._popular_icons
        ]
        if self._current_icon and not self._current_icon.startswith("/"):
            self._selected_theme = self._current_icon

    def _build_theme_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(14)
        root.set_margin_bottom(14)
        root.set_margin_start(18)
        root.set_margin_end(18)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search installed icons"))
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)
        root.append(self._search)

        self._theme_status = label("", "icon-selection")
        root.append(self._theme_status)

        self._theme_grid = Gtk.FlowBox()
        self._theme_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self._theme_grid.set_homogeneous(True)
        self._theme_grid.set_column_spacing(4)
        self._theme_grid.set_row_spacing(4)
        self._theme_grid.set_max_children_per_line(9)
        self._theme_grid.set_min_children_per_line(4)
        self._theme_grid.add_css_class("icon-grid")
        self._theme_grid.connect("child-activated", self._on_theme_activated)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._theme_grid)
        root.append(scroller)
        self._rebuild_theme_grid("")
        return root

    def _on_search_changed(self, _entry) -> None:
        if self._search_timeout is not None:
            GLib.source_remove(self._search_timeout)
        self._search_timeout = GLib.timeout_add(120, self._apply_search)

    def _apply_search(self) -> bool:
        self._search_timeout = None
        self._rebuild_theme_grid(self._search.get_text())
        return False

    def _rebuild_theme_grid(self, query: str) -> None:
        self._theme_grid.remove_all()
        self._theme_buttons = {}
        needle = query.casefold().strip()
        if needle:
            names = [name for name in self._all_theme_names if needle in name.casefold()]
        else:
            names = list(self._popular_icons) + list(self._all_theme_names)
        if (
            self._selected_theme
            and self._selected_theme in names
        ):
            names.remove(self._selected_theme)
            names.insert(0, self._selected_theme)
        names = names[:MAX_THEME_RESULTS]
        if not names:
            self._theme_status.set_text(_("No matching icons"))
            return
        self._theme_status.set_text(
            _("{count} icons").format(count=len(names))
        )
        for name in names:
            button = Gtk.ToggleButton()
            image = Gtk.Image.new_from_icon_name(name)
            image.set_pixel_size(40)
            button.set_child(image)
            button.set_tooltip_text(name)
            button.set_active(name == self._selected_theme)
            button.connect("toggled", self._on_theme_toggled, name)
            self._theme_grid.append(button)
            self._theme_buttons[name] = button
        if self._selected_theme and self._selected_theme not in self._theme_buttons:
            self._theme_status.set_text(
                _("Selected: {name}").format(name=self._selected_theme)
            )
        self._update_use_button()

    def _on_theme_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if self._updating_theme:
            return
        if button.get_active():
            self._updating_theme = True
            try:
                for other_name, other in self._theme_buttons.items():
                    if other_name != name and other.get_active():
                        other.set_active(False)
            finally:
                self._updating_theme = False
            self._selected_theme = name
            self._selected_icon = name
        elif self._selected_theme == name:
            self._selected_theme = None
            self._selected_icon = None
        self._update_use_button()

    def _on_theme_activated(self, _flow, child: Gtk.FlowBoxChild) -> None:
        button = child.get_child()
        if isinstance(button, Gtk.ToggleButton):
            button.set_active(True)
            self._use()

    # ------------------------------------------------------------------
    # Import page
    # ------------------------------------------------------------------
    def _build_import_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(24)
        root.set_margin_end(24)
        root.append(label(_("Import an image"), "section-heading"))
        root.append(
            label(
                _(
                    "The image is converted into square PNG sizes under your user "
                    "hicolor theme. SVG files stay scalable."
                ),
                "section-hint",
            )
        )

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        preview_box.add_css_class("import-preview")
        preview_box.set_halign(Gtk.Align.CENTER)
        self._import_preview = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        self._import_preview.set_pixel_size(96)
        preview_box.append(self._import_preview)
        self._import_file_label = label(_("No image selected"), "icon-selection")
        self._import_file_label.set_wrap(True)
        preview_box.append(self._import_file_label)
        root.append(preview_box)

        choose = Gtk.Button(label=_("Choose image"))
        choose.set_halign(Gtk.Align.CENTER)
        choose.connect("clicked", lambda _button: self._choose_import_image())
        root.append(choose)

        group = Adw.PreferencesGroup(title=_("Icon name"))
        self._import_name_row = Adw.EntryRow(title=_("Name"))
        self._import_name_row.set_text(self._suggested_name)
        group.add(self._import_name_row)
        root.append(group)
        self._import_existing_label = label("", "section-hint")
        root.append(self._import_existing_label)
        self._update_import_existing_label()
        self._import_name_row.connect("changed", lambda _row: self._update_import_existing_label())
        return root

    def _update_import_existing_label(self) -> None:
        name = self._import_name_row.get_text().strip()
        if name and self._icon_manager.is_managed(name):
            self._import_existing_label.set_text(
                _("An icon with this name already exists and will be replaced.")
            )
        else:
            self._import_existing_label.set_text("")

    def _choose_import_image(self) -> None:
        choose_image(self, _("Choose an image"), self._set_import_source)

    def _set_import_source(self, path: Path) -> None:
        self._import_source = path
        self._import_file_label.set_text(str(path))
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 96, 96, True)
            self._import_preview.set_from_pixbuf(pixbuf)
            self._import_preview.set_pixel_size(96)
        except Exception as error:
            show_error(self, _("Could not preview the image"), str(error))
        if not self._import_name_row.get_text().strip():
            self._import_name_row.set_text(self._suggested_name)
        self._update_use_button()

    def _import_and_use(self) -> None:
        if self._import_source is None:
            show_error(
                self,
                _("No image selected"),
                _("Choose an image before importing it."),
            )
            return
        name = self._import_name_row.get_text().strip()
        try:
            self._icon_manager.import_file(self._import_source, name)
        except IconError as error:
            show_error(self, _("Could not import the image"), str(error))
            return
        except OSError as error:
            show_error(self, _("Could not import the image"), str(error))
            return
        self._finish(name)

    # ------------------------------------------------------------------
    # Absolute path page
    # ------------------------------------------------------------------
    def _build_path_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(24)
        root.set_margin_end(24)
        root.append(label(_("Use an absolute path"), "section-heading"))
        warning = label(
            _(
                "This is not recommended. The icon will break if the file is moved, "
                "and some desktop environments cannot read it. Importing the image "
                "into the icon theme is more reliable."
            ),
            "validation-box",
            xalign=0,
        )
        warning.set_wrap(True)
        warning.add_css_class("validation-box")
        root.append(warning)

        group = Adw.PreferencesGroup(title=_("Image path"))
        self._path_row = Adw.EntryRow(title=_("Path"))
        browse = Gtk.Button(icon_name="folder-open-symbolic")
        browse.add_css_class("flat")
        browse.connect("clicked", lambda _button: choose_image(self, _("Choose an image"), self._set_absolute_path))
        self._path_row.add_suffix(browse)
        group.add(self._path_row)
        root.append(group)
        if self._current_icon.startswith("/"):
            self._path_row.set_text(self._current_icon)
        self._path_row.connect("changed", lambda _row: self._update_use_button())
        return root

    def _set_absolute_path(self, path: Path) -> None:
        self._path_row.set_text(str(path))
        self._update_use_button()

    # ------------------------------------------------------------------
    # State and actions
    # ------------------------------------------------------------------
    def _on_page_changed(self, stack: Gtk.Stack, _param) -> None:
        self._update_use_button()

    def _update_use_button(self) -> None:
        page = self._stack.get_visible_child_name() if self._stack.get_parent() else None
        if page is None:
            page = "theme"
        if page == "theme":
            self._use_button.set_sensitive(self._selected_theme is not None)
        elif page == "import":
            self._use_button.set_sensitive(self._import_source is not None)
        else:
            self._use_button.set_sensitive(bool(self._path_row.get_text().strip()))

    def _use(self, _button=None) -> None:
        page = self._stack.get_visible_child_name()
        if page == "theme":
            if not self._selected_theme:
                show_error(self, _("No icon selected"), _("Pick an icon first."))
                return
            self._finish(self._selected_theme)
        elif page == "import":
            self._import_and_use()
        else:
            path = self._path_row.get_text().strip()
            try:
                validated = self._icon_manager.validate_absolute_icon(path)
            except IconError as error:
                show_error(self, _("Invalid icon path"), str(error))
                return
            self._finish(str(validated))

    def _finish(self, icon_value: str) -> None:
        self._on_selected(icon_value)
        self.close()
