"""Application editor with details, field table and raw source views."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..catalog import AppRecord, Catalog, CatalogError
from ..desktop_entry import MAIN_GROUP, DesktopFile
from ..i18n import _
from ..icons import IconManager
from ..paths import user_applications_dir
from ..util import slugify
from .icon_picker import IconPickerWindow
from .widgets import (
    choose_executable,
    icon_tile,
    label,
    margin,
    open_containing_folder,
    show_confirm,
    show_error,
)

STANDARD_CATEGORIES = [
    ("AudioVideo", _("Audio & video")),
    ("Development", _("Development")),
    ("Education", _("Education")),
    ("Game", _("Games")),
    ("Graphics", _("Graphics")),
    ("Network", _("Network")),
    ("Office", _("Office")),
    ("Science", _("Science")),
    ("Settings", _("Settings")),
    ("System", _("System")),
    ("Utility", _("Utilities")),
]


def _split_user_list(text: str) -> list[str]:
    parts = re.split(r"[;,]", text)
    result: list[str] = []
    for part in parts:
        value = part.strip()
        if value and value not in result:
            result.append(value)
    return result


def _join_list(values: list[str]) -> str:
    return ";".join(values)


@dataclass
class FieldRow:
    key_entry: Gtk.Entry
    value_entry: Gtk.Entry
    original_key: str


class AppEditorWindow(Adw.Window):
    def __init__(
        self,
        *,
        parent: Gtk.Window,
        catalog: Catalog,
        record: AppRecord | None,
        on_saved: Callable[[], None],
    ) -> None:
        super().__init__(application=parent.get_application(), transient_for=parent, modal=True)
        self._catalog = catalog
        self._record = record
        self._on_saved = on_saved
        self._icon_manager = IconManager()
        self._entry = record.entry.clone() if record else DesktopFile.new(name="")
        self._current_view = "details"
        self._dirty = False
        self._loading = False
        self._loading_raw = False
        self._raw_dirty = False
        self._allow_close = False
        self._switching = False
        self._id_auto = record is None
        self._setting_id = False
        self._field_rows: list[FieldRow] = []
        self._field_original_keys: list[str] = []
        self._categories: list[str] = list(self._entry.categories)
        self._icon_picker: IconPickerWindow | None = None
        self._category_buttons: dict[str, Gtk.ToggleButton] = {}
        self._category_popover: Gtk.Popover | None = None

        self.set_default_size(840, 760)
        self.set_title(
            _("Edit application") if record else _("New application")
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)
        self._header = Adw.HeaderBar()
        self._header.set_show_end_title_buttons(False)

        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: self.close())
        self._header.pack_start(cancel)

        self._switcher = Adw.ViewSwitcher()
        self._switcher.set_stack(self._stack)
        self._switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        self._header.set_title_widget(self._switcher)

        self._save_button = Gtk.Button(label=_("Save"))
        self._save_button.add_css_class("suggested-action")
        self._save_button.connect("clicked", self._save)
        self._header.pack_end(self._save_button)

        outer.append(self._header)
        outer.append(self._stack)
        self.set_content(outer)

        self._details_page = self._build_details_page()
        self._fields_page = self._build_fields_page()
        self._source_page = self._build_source_page()
        self._stack.add_titled(self._details_page, "details", _("Details"))
        self._stack.add_titled(self._fields_page, "fields", _("Fields"))
        self._stack.add_titled(self._source_page, "source", _("Source"))

        self._refresh_details()
        self._refresh_fields()
        self._refresh_raw()
        self._stack.connect("notify::visible-child", self._on_view_changed)
        self.connect("close-request", self._on_close_request)
        self.present()

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    def _build_details_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        page.set_vexpand(True)

        identity = Adw.PreferencesGroup(title=_("Identity"))
        self.name_row = Adw.EntryRow(title=_("Name"))
        self.name_row.set_tooltip_text(_("The application name shown in the launcher"))
        identity.add(self.name_row)
        self.generic_row = Adw.EntryRow(title=_("Generic name"))
        identity.add(self.generic_row)
        self.comment_row = Adw.EntryRow(title=_("Comment"))
        identity.add(self.comment_row)

        if self._record is None:
            self.id_row = Adw.EntryRow(title=_("Application ID"))
            self.id_row.set_tooltip_text(
                _("Used as the desktop file name. Letters, numbers, dots, underscores and hyphens only.")
            )
            identity.add(self.id_row)
            self.id_row.set_text(slugify(self.name_row.get_text() or "", "new-application"))
            self.id_row.connect("changed", self._on_id_changed)
            location = Adw.ActionRow(
                title=_("Save location"),
                subtitle=str(user_applications_dir()),
            )
            identity.add(location)
        else:
            self.id_row = None
            identity.add(
                Adw.ActionRow(title=_("Application ID"), subtitle=self._record.id)
            )
            file_row = Adw.ActionRow(title=_("File"), subtitle=str(self._record.path))
            show_file = Gtk.Button(label=_("Show"))
            show_file.add_css_class("flat")
            show_file.connect(
                "clicked",
                lambda _button: open_containing_folder(self._record.path)
                if self._record
                else None,
            )
            file_row.add_suffix(show_file)
            identity.add(file_row)
        page.add(identity)

        launch = Adw.PreferencesGroup(title=_("Launch"))
        self.exec_row = Adw.EntryRow(title=_("Command"))
        self.exec_row.set_tooltip_text(
            _("The command to run, including any arguments or field codes such as %U")
        )
        choose_exec = Gtk.Button(icon_name="folder-open-symbolic")
        choose_exec.add_css_class("flat")
        choose_exec.set_tooltip_text(_("Choose an executable"))
        choose_exec.connect(
            "clicked",
            lambda _button: choose_executable(self, self._set_exec_from_chooser),
        )
        self.exec_row.add_suffix(choose_exec)
        launch.add(self.exec_row)

        self.path_row = Adw.EntryRow(title=_("Working directory"))
        self.path_row.set_tooltip_text(_("Optional working directory for the command"))
        launch.add(self.path_row)

        self.terminal_row = Adw.SwitchRow(title=_("Run in a terminal"))
        launch.add(self.terminal_row)
        self.startup_notify_row = Adw.SwitchRow(title=_("Startup notification"))
        self.startup_notify_row.set_subtitle(
            _("Let the desktop environment show a startup indicator")
        )
        launch.add(self.startup_notify_row)
        self.dbus_row = Adw.SwitchRow(title=_("D-Bus activation"))
        self.dbus_row.set_subtitle(
            _("The application is started through its D-Bus service")
        )
        launch.add(self.dbus_row)
        self.wm_class_row = Adw.EntryRow(title=_("Startup WM class"))
        self.wm_class_row.set_tooltip_text(
            _("Optional window class hint used to associate windows with this entry")
        )
        launch.add(self.wm_class_row)
        page.add(launch)

        appearance = Adw.PreferencesGroup(title=_("Appearance"))
        self.icon_row = Adw.ActionRow(title=_("Icon"))
        self.icon_preview = icon_tile(self._entry.icon_value, 32)
        self.icon_row.add_prefix(self.icon_preview)
        choose_icon = Gtk.Button(label=_("Choose"))
        choose_icon.add_css_class("flat")
        choose_icon.connect("clicked", self._choose_icon)
        self.icon_row.add_suffix(choose_icon)
        appearance.add(self.icon_row)

        self.categories_row = Adw.ActionRow(title=_("Categories"))
        self._category_button = Gtk.MenuButton()
        self._category_button.add_css_class("flat")
        self._category_button.set_child(label(_("Choose")))
        self._category_popover = self._build_category_popover()
        self._category_button.set_popover(self._category_popover)
        self.categories_row.add_suffix(self._category_button)
        appearance.add(self.categories_row)

        self.keywords_row = Adw.EntryRow(title=_("Keywords"))
        self.keywords_row.set_tooltip_text(_("Separate keywords with semicolons"))
        appearance.add(self.keywords_row)
        self.mime_row = Adw.EntryRow(title=_("MIME types"))
        self.mime_row.set_tooltip_text(_("Separate MIME types with semicolons"))
        appearance.add(self.mime_row)
        page.add(appearance)

        visibility = Adw.PreferencesGroup(title=_("Visibility"))
        self.no_display_row = Adw.SwitchRow(title=_("NoDisplay field"))
        self.no_display_row.set_subtitle(
            _(
                "Visible to the system but hidden from menus, overriding this "
                "tool's display settings."
            )
        )
        visibility.add(self.no_display_row)
        page.add(visibility)

        for widget in (
            self.name_row,
            self.generic_row,
            self.comment_row,
            self.exec_row,
            self.path_row,
            self.wm_class_row,
            self.keywords_row,
            self.mime_row,
        ):
            widget.connect("changed", self._mark_dirty_wrapper)
        for widget in (
            self.terminal_row,
            self.startup_notify_row,
            self.dbus_row,
            self.no_display_row,
        ):
            widget.connect("notify::active", self._mark_dirty_wrapper)
        self.name_row.connect("changed", self._on_name_changed)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(820)
        clamp.set_child(page)
        scroller.set_child(clamp)
        return scroller

    def _build_fields_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(22)
        root.set_margin_end(22)
        root.append(label(_("Fields in [Desktop Entry]"), "section-heading"))
        root.append(
            label(
                _(
                    "Edit top-level keys here. Use the source view for actions, "
                    "translations and complete control."
                ),
                "section-hint",
            )
        )

        self._field_list = Gtk.ListBox()
        self._field_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._field_list.add_css_class("boxed-list")
        self._field_list.add_css_class("field-table")
        root.append(self._field_list)

        add = Gtk.Button(label=_("Add field"))
        add.set_halign(Gtk.Align.START)
        add.add_css_class("flat")
        add.connect("clicked", lambda _button: self._add_field_row("", "", focus=True))
        root.append(add)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(820)
        clamp.set_child(root)
        scroller.set_child(clamp)
        return scroller

    def _build_source_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(22)
        root.set_margin_end(22)
        root.append(label(_("Complete desktop file"), "section-heading"))
        root.append(
            label(
                _(
                    "The source view is validated with desktop-file-validate when "
                    "you save. Unknown groups and actions are preserved."
                ),
                "section-hint",
            )
        )

        self.raw_view = Gtk.TextView()
        self.raw_view.set_monospace(True)
        self.raw_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.raw_view.add_css_class("source-view")
        self.raw_view.set_top_margin(12)
        self.raw_view.set_bottom_margin(12)
        self.raw_view.set_left_margin(14)
        self.raw_view.set_right_margin(14)
        self.raw_buffer = self.raw_view.get_buffer()
        self.raw_buffer.connect("changed", self._on_raw_changed)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.raw_view)
        root.append(scroller)
        return root

    # ------------------------------------------------------------------
    # Model synchronization
    # ------------------------------------------------------------------
    def _mark_dirty_wrapper(self, *_args) -> None:
        self._mark_dirty()

    def _mark_dirty(self, *_args) -> None:
        if not self._loading and not self._loading_raw:
            self._dirty = True

    def _on_raw_changed(self, _buffer) -> None:
        if self._loading_raw:
            return
        self._raw_dirty = True
        self._dirty = True

    def _on_name_changed(self, row: Adw.EntryRow) -> None:
        if not self._id_auto or self.id_row is None:
            return
        self._setting_id = True
        try:
            self.id_row.set_text(slugify(row.get_text(), "new-application"))
        finally:
            self._setting_id = False

    def _on_id_changed(self, _row: Adw.EntryRow) -> None:
        if not self._setting_id:
            self._id_auto = False
        self._mark_dirty()

    def _set_exec_from_chooser(self, path: str) -> None:
        if not path:
            return
        from ..util import quote_exec_path

        self.exec_row.set_text(quote_exec_path(path))

    def _choose_icon(self, _button) -> None:
        popular: list[str] = []
        for app in self._catalog.apps:
            value = app.icon_value
            if value and not value.startswith("/") and value not in popular:
                popular.append(value)
        self._icon_picker = IconPickerWindow(
            parent=self,
            icon_manager=self._icon_manager,
            current_icon=self._entry.icon_value,
            suggested_name=self._suggested_icon_name(),
            on_selected=self._set_icon,
            popular_icons=popular,
        )

    def _suggested_icon_name(self) -> str:
        if self.id_row is not None:
            return slugify(self.id_row.get_text(), "custom-icon")
        return slugify(self._record.id if self._record else "custom-icon", "custom-icon")

    def _set_icon(self, icon_value: str) -> None:
        self._entry.set(MAIN_GROUP, "Icon", icon_value)
        self.icon_row.remove(self.icon_preview)
        self.icon_preview = icon_tile(icon_value, 32)
        self.icon_row.add_prefix(self.icon_preview)
        self._update_icon_subtitle(icon_value)
        self._mark_dirty()

    def _update_icon_subtitle(self, icon_value: str) -> None:
        self.icon_row.set_subtitle(
            icon_value if icon_value else _("No icon selected")
        )

    def _build_category_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(440, -1)
        box.append(label(_("Categories"), "section-heading"))
        box.append(
            label(
                _(
                    "Pick the categories that match this application. "
                    "Desktop environments use them for grouping and search."
                ),
                "section-hint",
            )
        )
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(6)
        flow.set_row_spacing(6)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        for identifier, title in STANDARD_CATEGORIES:
            button = Gtk.ToggleButton(label=title)
            button.set_tooltip_text(identifier)
            self._category_buttons[identifier] = button
            flow.append(button)
        box.append(flow)
        box.append(label(_("Other categories"), "section-heading"))
        self._category_entry = Gtk.Entry()
        self._category_entry.set_placeholder_text("X-CustomCategory; AnotherCategory")
        box.append(self._category_entry)
        box.append(
            label(
                _("Separate additional category names with semicolons."),
                "section-hint",
            )
        )
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: popover.popdown())
        footer.append(cancel)
        apply_button = Gtk.Button(label=_("Apply"))
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", self._apply_categories)
        footer.append(apply_button)
        box.append(footer)
        popover.set_child(box)
        popover.connect("notify::visible", self._on_category_popover_visible)
        return popover

    def _on_category_popover_visible(self, popover: Gtk.Popover, _param) -> None:
        if not popover.get_visible():
            return
        known = {identifier for identifier, _title in STANDARD_CATEGORIES}
        selected = set(self._categories)
        for identifier, button in self._category_buttons.items():
            button.set_active(identifier in selected)
        extra = [item for item in self._categories if item not in known]
        self._category_entry.set_text(";".join(extra))

    def _apply_categories(self, _button) -> None:
        categories = [
            identifier
            for identifier, button in self._category_buttons.items()
            if button.get_active()
        ]
        for item in _split_user_list(self._category_entry.get_text()):
            if item not in categories:
                categories.append(item)
        self._set_categories(categories)
        if self._category_popover is not None:
            self._category_popover.popdown()

    def _set_categories(self, categories: list[str]) -> None:
        self._categories = list(categories)
        self._entry.set_list(MAIN_GROUP, "Categories", categories)
        self.categories_row.set_subtitle(
            ", ".join(categories) if categories else _("No categories")
        )
        self._mark_dirty()

    def _refresh_details(self) -> None:
        self._loading = True
        try:
            self.name_row.set_text(self._entry.unlocalized_name)
            self.generic_row.set_text(self._entry.get(MAIN_GROUP, "GenericName") or "")
            self.comment_row.set_text(self._entry.get(MAIN_GROUP, "Comment") or "")
            self.exec_row.set_text(self._entry.exec_line)
            self.path_row.set_text(self._entry.working_directory)
            self.terminal_row.set_active(self._entry.terminal)
            startup_notify = self._entry.get_bool("StartupNotify", True)
            self.startup_notify_row.set_active(startup_notify)
            self.dbus_row.set_active(self._entry.dbus_activatable)
            self.wm_class_row.set_text(self._entry.startup_wm_class)
            self.keywords_row.set_text(_join_list(self._entry.keywords))
            self.mime_row.set_text(_join_list(self._entry.mime_types))
            self.no_display_row.set_active(self._entry.no_display)
            self._categories = list(self._entry.categories)
            self.categories_row.set_subtitle(
                ", ".join(self._categories)
                if self._categories
                else _("No categories")
            )
            self._update_icon_subtitle(self._entry.icon_value)
            if self.icon_preview is not None:
                self.icon_row.remove(self.icon_preview)
                self.icon_preview = icon_tile(self._entry.icon_value, 32)
                self.icon_row.add_prefix(self.icon_preview)
            if self.id_row is not None and self._id_auto:
                self._setting_id = True
                try:
                    self.id_row.set_text(
                        slugify(self.name_row.get_text(), "new-application")
                    )
                finally:
                    self._setting_id = False
        finally:
            self._loading = False

    def _collect_details(self) -> None:
        name = self.name_row.get_text().strip()
        self._entry.set(MAIN_GROUP, "Name", name)
        self._set_or_remove("GenericName", self.generic_row.get_text().strip())
        self._set_or_remove("Comment", self.comment_row.get_text().strip())
        exec_line = self.exec_row.get_text().strip()
        if exec_line or not self.dbus_row.get_active():
            self._entry.set(MAIN_GROUP, "Exec", exec_line)
        else:
            self._entry.remove(MAIN_GROUP, "Exec")
        self._set_or_remove("Path", self.path_row.get_text().strip())
        self._entry.set(
            MAIN_GROUP, "Terminal", "true" if self.terminal_row.get_active() else "false"
        )
        if self.startup_notify_row.get_active():
            self._entry.set(MAIN_GROUP, "StartupNotify", "true")
        else:
            self._entry.set(MAIN_GROUP, "StartupNotify", "false")
        if self.dbus_row.get_active():
            self._entry.set(MAIN_GROUP, "DBusActivatable", "true")
        else:
            self._entry.remove(MAIN_GROUP, "DBusActivatable")
        self._set_or_remove("StartupWMClass", self.wm_class_row.get_text().strip())
        self._entry.set_list(MAIN_GROUP, "Categories", self._categories)
        self._entry.set_list(
            MAIN_GROUP, "Keywords", _split_user_list(self.keywords_row.get_text())
        )
        self._entry.set_list(
            MAIN_GROUP, "MimeType", _split_user_list(self.mime_row.get_text())
        )
        if self.no_display_row.get_active():
            self._entry.set(MAIN_GROUP, "NoDisplay", "true")
        else:
            self._entry.remove(MAIN_GROUP, "NoDisplay")

    def _set_or_remove(self, key: str, value: str) -> None:
        if value:
            self._entry.set(MAIN_GROUP, key, value)
        else:
            self._entry.remove(MAIN_GROUP, key)

    def _refresh_fields(self) -> None:
        self._field_rows = []
        self._field_list.remove_all()
        items = self._entry.items(MAIN_GROUP)
        self._field_original_keys = [key for key, _value in items]
        for key, value in items:
            self._add_field_row(key, value)
        if not items:
            self._add_field_row("", "")

    def _add_field_row(self, key: str, value: str, *, focus: bool = False) -> None:
        row = Gtk.ListBoxRow()
        row.add_css_class("field-row")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        key_entry = Gtk.Entry()
        key_entry.set_width_chars(20)
        key_entry.set_placeholder_text(_("Key"))
        key_entry.set_text(key)
        key_entry.set_hexpand(False)
        value_entry = Gtk.Entry()
        value_entry.set_placeholder_text(_("Value"))
        value_entry.set_text(value)
        value_entry.set_hexpand(True)
        remove = Gtk.Button(icon_name="list-remove-symbolic")
        remove.add_css_class("flat")
        remove.set_tooltip_text(_("Remove field"))
        box.append(key_entry)
        box.append(value_entry)
        box.append(remove)
        row.set_child(box)
        self._field_list.append(row)
        field = FieldRow(key_entry, value_entry, key)
        self._field_rows.append(field)
        key_entry.connect("changed", self._mark_dirty_wrapper)
        value_entry.connect("changed", self._mark_dirty_wrapper)
        remove.connect("clicked", lambda _button: self._remove_field_row(row, field))
        if focus:
            key_entry.grab_focus()

    def _remove_field_row(self, row: Gtk.ListBoxRow, field: FieldRow) -> None:
        self._field_list.remove(row)
        if field in self._field_rows:
            self._field_rows.remove(field)
        self._mark_dirty()

    def _collect_fields(self) -> None:
        values: list[tuple[str, str]] = []
        seen: set[str] = set()
        for field in self._field_rows:
            key = field.key_entry.get_text().strip()
            if not key:
                continue
            if key in seen:
                raise ValueError(_("Duplicate field: {key}").format(key=key))
            seen.add(key)
            values.append((key, field.value_entry.get_text()))

        # Remove fields that disappeared from the table, then write every
        # remaining field.  This also handles renamed keys.
        for key in set(self._field_original_keys) - seen:
            self._entry.remove(MAIN_GROUP, key)
        for key, value in values:
            self._entry.set(MAIN_GROUP, key, value)
        self._field_original_keys = [key for key, _value in values]

    def _refresh_raw(self) -> None:
        self._loading_raw = True
        try:
            self.raw_buffer.set_text(self._entry.to_text())
        finally:
            self._loading_raw = False
        self._raw_dirty = False

    def _collect_raw(self) -> None:
        start, end = self.raw_buffer.get_bounds()
        text = self.raw_buffer.get_text(start, end, True)
        candidate = DesktopFile(text)
        if not candidate.has_group(MAIN_GROUP):
            raise ValueError(_("The source must contain a [Desktop Entry] group."))
        self._entry = candidate
        self._raw_dirty = False

    def _on_view_changed(self, stack: Adw.ViewStack, _param) -> None:
        if self._switching:
            return
        new_view = stack.get_visible_child_name()
        if not new_view or new_view == self._current_view:
            return
        old_view = self._current_view
        self._switching = True
        try:
            try:
                self._collect_view(old_view)
            except Exception as error:
                self._show_switch_error(str(error))
                self._stack.set_visible_child_name(old_view)
                return
            self._current_view = new_view
            if new_view == "details":
                self._refresh_details()
            elif new_view == "fields":
                self._refresh_fields()
            elif new_view == "source":
                self._refresh_raw()
        finally:
            self._switching = False

    def _collect_view(self, view: str) -> None:
        if view == "details":
            self._collect_details()
        elif view == "fields":
            self._collect_fields()
        elif view == "source" and self._raw_dirty:
            self._collect_raw()

    def _show_switch_error(self, message: str) -> None:
        show_error(
            self,
            _("Cannot switch view"),
            message,
        )

    # ------------------------------------------------------------------
    # Save and close
    # ------------------------------------------------------------------
    def _save(self, _button=None) -> None:
        try:
            self._collect_view(self._current_view)
        except Exception as error:
            show_error(self, _("Cannot save the desktop entry"), str(error))
            return

        validation = self._entry.validate()
        if not validation.ok:
            show_error(
                self,
                _("The desktop entry is not valid"),
                validation.summary,
            )
            return

        try:
            if self._record is None:
                assert self.id_row is not None
                desktop_id = self.id_row.get_text().strip()
                self._catalog.create_app(desktop_id, self._entry)
            else:
                self._catalog.save_app(self._record, self._entry)
        except CatalogError as error:
            show_error(self, _("Could not save the application"), str(error))
            return
        except OSError as error:
            show_error(self, _("Could not save the application"), str(error))
            return

        self._allow_close = True
        self._on_saved()
        self.close()

    def _on_close_request(self, _window) -> bool:
        if self._allow_close or not self._dirty:
            return False
        show_confirm(
            self,
            _("Discard changes?"),
            _("Your edits have not been saved."),
            confirm_label=_("Discard"),
            destructive=True,
            callback=self._discard_and_close,
        )
        return True

    def _discard_and_close(self) -> None:
        self._allow_close = True
        self.destroy()
