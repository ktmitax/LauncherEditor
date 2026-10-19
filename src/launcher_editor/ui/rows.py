"""Rows used by the application list and sidebar."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango

from ..catalog import AppRecord
from ..i18n import _
from .widgets import badge, icon_tile, label, open_containing_folder, symbolic_icon


class FilterRow(Gtk.ListBoxRow):
    def __init__(self, key: str, title: str, icon_name: str, count: int = 0) -> None:
        super().__init__()
        self.key = key
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class("filter-row")
        icon = symbolic_icon(icon_name)
        icon.add_css_class("filter-icon")
        box.append(icon)
        title_label = label(title, "filter-label")
        title_label.set_hexpand(True)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(title_label)
        self.count_label = label(str(count) if count else "", "filter-count")
        self.count_label.set_halign(Gtk.Align.END)
        box.append(self.count_label)
        self.set_child(box)

    def set_count(self, count: int) -> None:
        self.count_label.set_text(str(count) if count else "")


class AppRow(Gtk.ListBoxRow):
    def __init__(
        self,
        app: AppRecord,
        *,
        on_edit: Callable[[AppRecord], None],
        on_delete: Callable[[AppRecord], None],
        on_toggle_launcher: Callable[[AppRecord, bool], bool],
        on_toggle_desktop: Callable[[AppRecord, bool], bool],
        on_add_to_launcher: Callable[[AppRecord], None],
    ) -> None:
        super().__init__()
        self.app = app
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._on_toggle_launcher = on_toggle_launcher
        self._on_toggle_desktop = on_toggle_desktop
        self._on_add_to_launcher = on_add_to_launcher
        self._updating = False

        self.add_css_class("app-row")
        if app.hidden:
            self.add_css_class("hidden-app")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)
        content.set_valign(Gtk.Align.CENTER)
        self.set_child(content)

        content.append(icon_tile(app.icon_value, 40))

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_hexpand(True)
        content.append(text_box)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = label(app.name, "app-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        title_box.append(name)
        for text, kind in self._badges(app):
            title_box.append(badge(text, kind))
        text_box.append(title_box)

        description_text = app.comment or app.entry.exec_line or str(app.path)
        description = label(description_text, "app-description")
        description.set_ellipsize(Pango.EllipsizeMode.END)
        description.set_single_line_mode(True)
        text_box.append(description)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        controls.set_valign(Gtk.Align.CENTER)
        content.append(controls)

        self.launcher_switch = Gtk.Switch()
        self.launcher_switch.set_active(app.launcher_visible)
        self.launcher_switch.set_valign(Gtk.Align.CENTER)
        self.launcher_switch.set_tooltip_text(_("Show in the application launcher"))
        self.launcher_switch.set_sensitive(app.launcher_manageable)
        if not app.launcher_manageable:
            if app.desktop_only:
                self.launcher_switch.set_tooltip_text(
                    _("This file only exists on the desktop")
                )
            elif app.no_display:
                self.launcher_switch.set_tooltip_text(
                    _("This application declares NoDisplay=true")
                )
            elif app.override_path == app.path:
                self.launcher_switch.set_tooltip_text(
                    _("This hidden override has no application behind it")
                )
        self.launcher_switch.connect("notify::active", self._launcher_changed)
        controls.append(self._switch_column(self.launcher_switch))

        self.desktop_switch = Gtk.Switch()
        self.desktop_switch.set_active(app.desktop_visible)
        self.desktop_switch.set_valign(Gtk.Align.CENTER)
        self.desktop_switch.set_tooltip_text(_("Show an icon on the desktop"))
        if app.desktop_only:
            self.desktop_switch.set_sensitive(False)
            self.desktop_switch.set_tooltip_text(
                _("This desktop file is managed in the desktop folder")
            )
        self.desktop_switch.connect("notify::active", self._desktop_changed)
        controls.append(self._switch_column(self.desktop_switch))

        self.menu_button = Gtk.MenuButton()
        self.menu_button.add_css_class("flat")
        self.menu_button.add_css_class("row-menu")
        self.menu_button.set_tooltip_text(_("Application actions"))
        image = Gtk.Image.new_from_icon_name("view-more-symbolic")
        image.set_pixel_size(16)
        self.menu_button.set_child(image)
        self.menu_button.set_popover(self._build_popover())
        controls.append(self.menu_button)

        self.set_activatable(app.can_edit)

    @staticmethod
    def _switch_column(switch: Gtk.Switch) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("switch-column")
        box.set_halign(Gtk.Align.CENTER)
        box.append(switch)
        return box

    @staticmethod
    def _badges(app: AppRecord) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        if app.desktop_only:
            result.append((_("Desktop only"), "desktop"))
        elif app.source_key == "user":
            if app.shadowed_path:
                result.append((_("User override"), "user"))
            else:
                result.append((_("User"), "user"))
        elif app.source_key == "flatpak":
            result.append((_("System"), "system"))
            result.append(("Flatpak", "system"))
        elif app.source_key == "snap":
            result.append((_("System"), "system"))
            result.append(("Snap", "system"))
        else:
            result.append((_("System"), "system"))
        if app.hidden:
            result.append((_("Hidden"), "warning"))
        elif app.no_display:
            result.append((_("No display"), "warning"))
        return result

    def _set_switch(self, switch: Gtk.Switch, value: bool) -> None:
        self._updating = True
        try:
            switch.set_active(value)
        finally:
            self._updating = False

    def _launcher_changed(self, switch: Gtk.Switch, _param) -> None:
        if self._updating:
            return
        requested = switch.get_active()
        if not self._on_toggle_launcher(self.app, requested):
            self._set_switch(switch, not requested)

    def _desktop_changed(self, switch: Gtk.Switch, _param) -> None:
        if self._updating:
            return
        requested = switch.get_active()
        if not self._on_toggle_desktop(self.app, requested):
            self._set_switch(switch, not requested)

    def _build_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        if self.app.can_edit:
            box.append(self._action_button(_("Edit"), "document-edit-symbolic", self._edit))
        if self.app.desktop_only:
            box.append(
                self._action_button(
                    _("Add to launcher"), "list-add-symbolic", self._add_to_launcher
                )
            )
        box.append(
            self._action_button(
                _("Show file location"), "folder-open-symbolic", self._show_location
            )
        )
        if self.app.can_delete:
            box.append(
                self._action_button(
                    _("Delete"), "user-trash-symbolic", self._delete, destructive=True
                )
            )
        popover.set_child(box)
        return popover

    def _action_button(
        self,
        text: str,
        icon_name: str,
        callback: Callable,
        *,
        destructive: bool = False,
    ) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("flat")
        if destructive:
            button.add_css_class("destructive-action")
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        content.append(icon)
        content.append(label(text))
        button.set_child(content)

        def on_clicked(_button) -> None:
            self.menu_button.popdown()
            callback()

        button.connect("clicked", on_clicked)
        return button

    def _edit(self) -> None:
        self._on_edit(self.app)

    def _delete(self) -> None:
        self._on_delete(self.app)

    def _add_to_launcher(self) -> None:
        self._on_add_to_launcher(self.app)

    def _show_location(self) -> None:
        open_containing_folder(self.app.path)
