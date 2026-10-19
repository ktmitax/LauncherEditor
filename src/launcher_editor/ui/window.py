"""Main application window."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import APP_ID, __version__
from ..catalog import (
    AppRecord,
    Catalog,
    CatalogError,
    DesktopConflictError,
)
from ..config import Config
from ..i18n import _, ngettext
from ..icons import IconManager
from ..paths import desktop_dir, ensure_directory, user_applications_dir
from .editor import AppEditorWindow
from .icon_library import IconLibraryPage
from .rows import AppRow, FilterRow
from .widgets import (
    app_mark,
    empty_illustration,
    label,
    open_containing_folder,
    show_confirm,
    show_error,
)

_FILTER_DEFINITIONS = [
    ("all", lambda: _("All applications"), "view-app-grid-symbolic"),
    ("user", lambda: _("User applications"), "avatar-default-symbolic"),
    ("system", lambda: _("System applications"), "applications-system-symbolic"),
    ("hidden", lambda: _("Hidden"), "action-unavailable-symbolic"),
    ("desktop", lambda: _("On the desktop"), "user-desktop-symbolic"),
    ("icons", lambda: _("Custom icons"), "applications-graphics-symbolic"),
]


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, config: Config) -> None:
        super().__init__(application=application)
        self.config = config
        self.catalog = Catalog()
        self.icon_manager = IconManager()
        self.filter_key = config.last_filter if config.last_filter in {
            "all", "user", "system", "hidden", "desktop", "icons"
        } else "all"
        self.query = ""
        self._updating_filter = False
        self._refresh_source: int | None = None
        self._filter_rows: dict[str, FilterRow] = {}
        self._icons_page: IconLibraryPage | None = None
        self._empty_action_mode = "new"

        self.set_default_size(config.window_width, config.window_height)
        self.set_title(_("Launcher Editor"))
        self._install_actions()
        self._build_ui()
        self._refresh_all(initial=True)
        self._select_initial_filter()
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        header = Adw.HeaderBar()
        self._title_widget = Adw.WindowTitle(title=_("Launcher Editor"), subtitle="")
        header.set_title_widget(self._title_widget)

        self._new_button = Gtk.Button(icon_name="list-add-symbolic")
        self._new_button.add_css_class("flat")
        self._new_button.set_tooltip_text(_("New application"))
        self._new_button.connect("clicked", lambda _button: self._open_new())
        header.pack_end(self._new_button)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Refresh"))
        refresh.connect("clicked", lambda _button: self._refresh())
        header.pack_end(refresh)

        menu_button = Gtk.MenuButton()
        menu_button.add_css_class("flat")
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text(_("Menu"))
        menu = Gio.Menu()
        menu.append(_("New application"), "win.new")
        menu.append(_("Refresh"), "win.refresh")
        menu.append(_("Open applications folder"), "win.open-apps")
        menu.append(_("Open desktop folder"), "win.open-desktop")
        menu.append(_("About Launcher Editor"), "win.about")
        menu.append(_("Quit"), "app.quit")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._sidebar = self._build_sidebar()
        body.append(self._sidebar)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_hexpand(True)
        self._content_stack.set_vexpand(True)
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_transition_duration(120)
        self._apps_page = self._build_apps_page()
        self._content_stack.add_named(self._apps_page, "apps")
        self._icons_page = IconLibraryPage(
            parent=self,
            catalog=self.catalog,
            icon_manager=self.icon_manager,
            on_changed=self._icons_changed,
            toast=self.toast,
        )
        self._content_stack.add_named(self._icons_page, "icons")
        body.append(self._content_stack)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.append(header)
        root.append(body)
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(root)
        self.set_content(self._toast_overlay)

    def _build_sidebar(self) -> Gtk.Widget:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.set_size_request(238, -1)
        sidebar.add_css_class("sidebar")

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand.add_css_class("brand")
        brand.append(app_mark(34))
        brand_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        brand_text.append(label(_("Launcher Editor"), "brand-title"))
        brand_text.append(label(_("Launchers & icons"), "brand-subtitle"))
        brand.append(brand_text)
        sidebar.append(brand)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._filter_list = Gtk.ListBox()
        self._filter_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._filter_list.add_css_class("sidebar-list")
        self._filter_list.connect("row-selected", self._on_filter_selected)
        for key, title_fn, icon_name in _FILTER_DEFINITIONS:
            row = FilterRow(key, title_fn(), icon_name)
            self._filter_rows[key] = row
            self._filter_list.append(row)
        scroller.set_child(self._filter_list)
        sidebar.append(scroller)

        desktop_path = desktop_dir()
        display_path = str(desktop_path)
        try:
            display_path = "~/" + str(desktop_path.relative_to(Path.home()))
        except ValueError:
            pass
        footer = label(_("Desktop: {path}").format(path=display_path), "sidebar-footer")
        footer.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        footer.set_tooltip_text(str(desktop_path))
        sidebar.append(footer)
        return sidebar

    def _build_apps_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toolbar.add_css_class("content-toolbar")
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search applications"))
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)
        toolbar.append(self._search)
        page.append(toolbar)

        self._list_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._list_header.add_css_class("list-header")
        application_label = label(_("Application"))
        application_label.set_hexpand(True)
        self._list_header.append(application_label)
        launcher_label = label(_("Launcher"))
        launcher_label.set_size_request(58, -1)
        launcher_label.set_halign(Gtk.Align.CENTER)
        self._list_header.append(launcher_label)
        desktop_label = label(_("Desktop"))
        desktop_label.set_size_request(58, -1)
        desktop_label.set_halign(Gtk.Align.CENTER)
        self._list_header.append(desktop_label)
        spacer = Gtk.Box()
        spacer.set_size_request(32, -1)
        self._list_header.append(spacer)
        page.append(self._list_header)

        self._app_list = Gtk.ListBox()
        self._app_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._app_list.add_css_class("app-list")
        self._app_list.connect("row-activated", self._on_app_activated)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._app_list)

        self._empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._empty_box.set_halign(Gtk.Align.CENTER)
        self._empty_box.set_valign(Gtk.Align.CENTER)
        self._empty_box.set_margin_top(30)
        self._empty_box.set_margin_bottom(30)
        self._empty_box.set_margin_start(30)
        self._empty_box.set_margin_end(30)
        self._empty_box.append(empty_illustration())
        self._empty_title = label("", "empty-title", xalign=0.5)
        self._empty_box.append(self._empty_title)
        self._empty_body = label("", "empty-body", xalign=0.5)
        self._empty_body.set_wrap(True)
        self._empty_body.set_justify(Gtk.Justification.CENTER)
        self._empty_body.set_max_width_chars(52)
        self._empty_box.append(self._empty_body)
        self._empty_button = Gtk.Button(label=_("New application"))
        self._empty_button.add_css_class("suggested-action")
        self._empty_button.set_halign(Gtk.Align.CENTER)
        self._empty_button.connect("clicked", self._on_empty_action)
        self._empty_box.append(self._empty_button)

        empty_clamp = Adw.Clamp()
        empty_clamp.set_maximum_size(620)
        empty_clamp.set_vexpand(True)
        empty_clamp.set_valign(Gtk.Align.FILL)
        empty_clamp.set_child(self._empty_box)

        self._apps_stack = Gtk.Stack()
        self._apps_stack.set_vexpand(True)
        self._apps_stack.add_named(scroller, "list")
        self._apps_stack.add_named(empty_clamp, "empty")
        page.append(self._apps_stack)
        return page

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------
    def _refresh_all(self, *, initial: bool = False, message: str | None = None) -> None:
        self.catalog.reload()
        self._update_counts()
        self._rebuild_app_list()
        if self._icons_page is not None:
            self._icons_page.refresh()
        self._update_header()
        if message:
            self.toast(message)

    def _refresh(self, _action=None, _param=None) -> None:
        self._queue_refresh()

    def _queue_refresh(self, message: str | None = None) -> None:
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None
        self._pending_message = message

        def run() -> bool:
            self._refresh_source = None
            pending = getattr(self, "_pending_message", None)
            self._pending_message = None
            self._refresh_all(message=pending)
            return False

        self._refresh_source = GLib.timeout_add(20, run)

    def _update_counts(self) -> None:
        counts = self.catalog.counts()
        counts["icons"] = len(self.icon_manager.list_managed())
        for key, row in self._filter_rows.items():
            row.set_count(counts.get(key, 0))

    def _filtered_apps(self) -> list[AppRecord]:
        apps = self.catalog.apps
        if self.filter_key == "user":
            apps = [app for app in apps if app.is_user_app]
        elif self.filter_key == "system":
            apps = [app for app in apps if app.is_system_app]
        elif self.filter_key == "hidden":
            apps = [app for app in apps if app.hidden and not app.desktop_only]
        elif self.filter_key == "desktop":
            apps = [app for app in apps if app.desktop_visible]
        if self.query:
            apps = [app for app in apps if app.matches(self.query)]
        return apps

    def _rebuild_app_list(self) -> None:
        if self.filter_key == "icons":
            return
        apps = self._filtered_apps()
        self._app_list.remove_all()
        for app in apps:
            row = AppRow(
                app,
                on_edit=self._edit_app,
                on_delete=self._delete_app,
                on_toggle_launcher=self._toggle_launcher,
                on_toggle_desktop=self._toggle_desktop,
                on_add_to_launcher=self._add_to_launcher,
            )
            self._app_list.append(row)

        has_results = bool(apps)
        self._list_header.set_visible(has_results)
        self._apps_stack.set_visible_child_name("list" if has_results else "empty")
        if not has_results:
            self._configure_empty_state()
        self._update_header(len(apps))

    def _configure_empty_state(self) -> None:
        if self.query:
            self._empty_title.set_text(_("No matching applications"))
            self._empty_body.set_text(
                _("Try a different search, or clear the search to see everything.")
            )
            self._empty_button.set_label(_("Clear search"))
            self._empty_action_mode = "clear"
        elif self.filter_key != "all":
            self._empty_title.set_text(_("Nothing in this view"))
            self._empty_body.set_text(
                _("There are no applications that match this filter right now.")
            )
            self._empty_button.set_label(_("Show all applications"))
            self._empty_action_mode = "all"
        else:
            self._empty_title.set_text(_("No applications found"))
            self._empty_body.set_text(
                _(
                    "Create a launcher entry, or check that your desktop files are "
                    "readable."
                )
            )
            self._empty_button.set_label(_("New application"))
            self._empty_action_mode = "new"

    def _on_empty_action(self, _button) -> None:
        if self._empty_action_mode == "clear":
            self._search.set_text("")
            self._search.grab_focus()
        elif self._empty_action_mode == "all":
            self._set_filter("all")
        else:
            self._open_new()

    def _on_app_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, AppRow):
            self._edit_app(row.app)

    def _update_header(self, shown_count: int | None = None) -> None:
        if self.filter_key == "icons":
            self._title_widget.set_title(_("Custom icons"))
            icons = self.icon_manager.list_managed()
            self._title_widget.set_subtitle(
                ngettext("{count} icon", "{count} icons", len(icons)).format(
                    count=len(icons)
                )
            )
            self._new_button.set_visible(False)
            self._search.set_visible(False)
            return
        self._new_button.set_visible(True)
        self._search.set_visible(True)
        title = {
            "all": _("All applications"),
            "user": _("User applications"),
            "system": _("System applications"),
            "hidden": _("Hidden applications"),
            "desktop": _("On the desktop"),
        }.get(self.filter_key, _("All applications"))
        count = len(self._filtered_apps()) if shown_count is None else shown_count
        subtitle = ngettext("{count} application", "{count} applications", count).format(
            count=count
        )
        if self.query:
            subtitle = _("{count} matches for “{query}”").format(
                count=count, query=self.query
            )
        self._title_widget.set_title(title)
        self._title_widget.set_subtitle(subtitle)

    # ------------------------------------------------------------------
    # Filters and search
    # ------------------------------------------------------------------
    def _on_filter_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._updating_filter or not isinstance(row, FilterRow):
            return
        self._set_filter(row.key, update_row=False)

    def _set_filter(self, key: str, *, update_row: bool = True) -> None:
        if key not in self._filter_rows:
            return
        self.filter_key = key
        self.config.last_filter = key
        if update_row:
            self._updating_filter = True
            try:
                self._filter_list.select_row(self._filter_rows[key])
            finally:
                self._updating_filter = False
        if key == "icons":
            self._content_stack.set_visible_child_name("icons")
            self._icons_page.refresh()
        else:
            self._content_stack.set_visible_child_name("apps")
            self._rebuild_app_list()
        self._update_header()

    def _select_initial_filter(self) -> None:
        self._updating_filter = True
        try:
            self._filter_list.select_row(self._filter_rows[self.filter_key])
        finally:
            self._updating_filter = False
        if self.filter_key == "icons":
            self._content_stack.set_visible_child_name("icons")
        else:
            self._content_stack.set_visible_child_name("apps")
        self._update_header()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self.query = entry.get_text().strip()
        self._rebuild_app_list()

    def _focus_search(self, _action=None, _param=None) -> None:
        if self.filter_key == "icons":
            self._set_filter("all")
        self._search.grab_focus()
        self._search.select_region(0, -1)

    # ------------------------------------------------------------------
    # Application actions
    # ------------------------------------------------------------------
    def _open_new(self) -> None:
        AppEditorWindow(
            parent=self,
            catalog=self.catalog,
            record=None,
            on_saved=lambda: self._queue_refresh(_("Application created")),
        )

    def _edit_app(self, app: AppRecord) -> None:
        if not app.can_edit:
            return
        AppEditorWindow(
            parent=self,
            catalog=self.catalog,
            record=app,
            on_saved=lambda: self._queue_refresh(_("Application saved")),
        )

    def _toggle_launcher(self, app: AppRecord, visible: bool) -> bool:
        try:
            self.catalog.set_launcher_visible(app, visible)
        except CatalogError as error:
            show_error(self, _("Could not change launcher visibility"), str(error))
            return False
        self._queue_refresh(
            _("Shown in the launcher") if visible else _("Hidden from the launcher")
        )
        return True

    def _toggle_desktop(self, app: AppRecord, visible: bool) -> bool:
        try:
            self.catalog.set_desktop_visible(app, visible)
        except DesktopConflictError as error:
            self._confirm_desktop_conflict(app, visible, error)
            return False
        except CatalogError as error:
            show_error(self, _("Could not change the desktop icon"), str(error))
            return False
        self._queue_refresh(
            _("Desktop icon created") if visible else _("Desktop icon removed")
        )
        return True

    def _confirm_desktop_conflict(
        self, app: AppRecord, visible: bool, error: Exception
    ) -> None:
        target = self.catalog.desktop_target(app.id)
        if visible:
            heading = _("Replace an existing desktop file?")
            body = _(
                "{path} already exists and was not created by Launcher Editor. "
                "Replacing it will overwrite the file."
            ).format(path=target)
            confirm_label = _("Replace")
        else:
            heading = _("Remove a desktop file?")
            body = _(
                "{path} was not created by Launcher Editor. Removing it will move "
                "the file to the trash."
            ).format(path=target)
            confirm_label = _("Remove")

        def confirmed() -> None:
            try:
                self.catalog.set_desktop_visible(app, visible, force=True)
            except CatalogError as exc:
                show_error(self, _("Could not change the desktop icon"), str(exc))
                return
            self._queue_refresh(
                _("Desktop icon created") if visible else _("Desktop icon removed")
            )

        show_confirm(
            self,
            heading,
            body,
            confirm_label=confirm_label,
            destructive=not visible,
            callback=confirmed,
        )

    def _delete_app(self, app: AppRecord) -> None:
        if app.desktop_only:
            heading = _("Delete this desktop file?")
            body = _(
                "The file will be moved to the trash. It is not part of a launcher "
                "entry in your application folder."
            )
        else:
            heading = _("Delete “{name}”?").format(name=app.name)
            body = _(
                "The desktop entry will be moved to the trash. This does not "
                "uninstall the application. A desktop icon created by Launcher "
                "Editor will also be removed."
            )
        show_confirm(
            self,
            heading,
            body,
            confirm_label=_("Delete"),
            destructive=True,
            callback=lambda: self._delete_confirmed(app),
        )

    def _delete_confirmed(self, app: AppRecord) -> None:
        try:
            self.catalog.delete_app(app)
        except CatalogError as error:
            show_error(self, _("Could not delete the application"), str(error))
            return
        except OSError as error:
            show_error(self, _("Could not delete the application"), str(error))
            return
        self._queue_refresh(_("Deleted {name}").format(name=app.name))

    def _add_to_launcher(self, app: AppRecord) -> None:
        try:
            self.catalog.add_desktop_only_to_launcher(app)
        except (CatalogError, OSError) as error:
            show_error(self, _("Could not add the application to the launcher"), str(error))
            return
        self._queue_refresh(_("Added to the launcher"))

    def _icons_changed(self) -> None:
        # The icon library owns its own refresh; only the sidebar and header
        # counts need to be updated.
        self._update_counts()
        self._update_header()

    # ------------------------------------------------------------------
    # Header actions and dialogs
    # ------------------------------------------------------------------
    def _install_actions(self) -> None:
        application = self.get_application()
        specs = [
            ("new", self._open_new, ["<Primary>n"]),
            ("refresh", self._refresh, ["<Primary>r", "F5"]),
            ("search", self._focus_search, ["<Primary>f"]),
            ("open-apps", self._open_applications_folder, []),
            ("open-desktop", self._open_desktop_folder, []),
            ("about", self._show_about, []),
        ]
        for name, callback, accels in specs:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, cb=callback: cb())
            self.add_action(action)
            if accels and application is not None:
                application.set_accels_for_action(f"win.{name}", accels)

    def _open_applications_folder(self) -> None:
        directory = ensure_directory(user_applications_dir())
        open_containing_folder(directory)

    def _open_desktop_folder(self) -> None:
        directory = ensure_directory(desktop_dir())
        open_containing_folder(directory)

    def _show_about(self) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            modal=True,
            application_name=_("Launcher Editor"),
            application_icon=APP_ID,
            developer_name="Launcher Editor contributors",
            version=__version__,
            comments=_(
                "A focused tool for managing desktop entries and application icons."
            ),
            license_type=Gtk.License.GPL_3_0,
        )
        about.present()

    # ------------------------------------------------------------------
    # Feedback and lifecycle
    # ------------------------------------------------------------------
    def toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(3)
        self._toast_overlay.add_toast(toast)

    def _on_close_request(self, _window) -> bool:
        self.config.window_width = max(760, self.get_width() or self.config.window_width)
        self.config.window_height = max(520, self.get_height() or self.config.window_height)
        self.config.last_filter = self.filter_key
        try:
            self.config.save()
        except OSError:
            pass
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None
        return False
