"""Application entry point and global GTK setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME, __version__
from .config import Config
from .i18n import _, setup as setup_i18n
from .ui.window import MainWindow

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent / "data"


class LauncherEditorApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name(_("Launcher Editor"))
        GLib.set_prgname("launcher-editor")
        Gtk.Window.set_default_icon_name(APP_ID)
        self._load_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda _action, _param: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self) -> None:
        if self._window is not None:
            self._window.present()
            return
        config = Config.load()
        self._window = MainWindow(self, config)
        self._window.present()

    def _load_css(self) -> None:
        css_path = DATA_DIR / "style.css"
        try:
            css = css_path.read_text(encoding="utf-8")
        except OSError:
            log.warning("Could not read %s", css_path)
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    setup_i18n()
    app = LauncherEditorApplication()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
