"""Small gettext wrapper used by the whole application.

The translation object is kept in a module-level variable and looked up on every
call.  UI modules can therefore do ``from .i18n import _`` at import time and
still pick up the locale selected in :func:`setup`.
"""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

DOMAIN = "launcher-editor"
_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_translation: gettext.NullTranslations = gettext.NullTranslations()


def _locale_candidates() -> list[str]:
    raw: list[str] = []
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if not value:
            continue
        raw.extend(value.split(":"))
    result: list[str] = []
    for item in raw:
        item = item.strip()
        if not item or item in {"C", "POSIX"}:
            continue
        # Strip encoding and modifier for gettext, but keep the original too.
        base = item.split(".", 1)[0]
        for candidate in (item, base):
            if candidate not in result:
                result.append(candidate)
    return result


def setup(localedir: str | Path | None = None) -> None:
    """Install the translation for the current process locale."""
    global _translation
    directory = Path(localedir) if localedir else _LOCALE_DIR
    try:
        _translation = gettext.translation(
            DOMAIN,
            localedir=str(directory),
            languages=_locale_candidates() or None,
            fallback=True,
        )
    except Exception:
        _translation = gettext.NullTranslations()


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def current_language_names() -> list[str]:
    """Return candidate locale names for localized .desktop keys.

    The Desktop Entry Specification stores localized keys as
    ``Name[lang_COUNTRY]``.  The list is ordered from most to least specific.
    """
    names: list[str] = []
    for item in _locale_candidates():
        item = item.split("@", 1)[0]
        if not item:
            continue
        parts = item.split("_", 1)
        candidates = [item, parts[0]]
        # Also try the current locale via locale.normalize for odd environment
        # combinations.
        try:
            normalized = locale.normalize(item)
            candidates.append(normalized.split(".", 1)[0])
        except Exception:
            pass
        for candidate in candidates:
            if candidate and candidate not in names and candidate not in {"C", "POSIX"}:
                names.append(candidate)
    if not names:
        try:
            language, _encoding = locale.getlocale()
        except Exception:
            language = None
        if language:
            names.extend([language, language.split("_", 1)[0]])
    return names
