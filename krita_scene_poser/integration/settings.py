"""KSP preferences, stored through Krita's public settings API.

Values live in Krita's configuration under the ``krita_scene_poser`` group as
strings. Unreadable or invalid values fall back to their defaults, so a
damaged setting can never break plugin startup.
"""

from dataclasses import dataclass, fields, replace
import os

GROUP = "krita_scene_poser"


@dataclass(frozen=True)
class Settings:
    diagnostic_log: bool = False


def _parse(text, default):
    if isinstance(default, bool):
        value = str(text).strip().lower()
        return {"true": True, "1": True, "false": False, "0": False}.get(value, default)
    try:
        return type(default)(text)
    except (TypeError, ValueError):
        return default


def _format(value):
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def load(read=None):
    """``read(name, default_text)`` returns stored text; defaults to Krita's store."""
    read = read or _krita_reader()
    values = {}
    for field in fields(Settings):
        try:
            values[field.name] = _parse(read(field.name, _format(field.default)), field.default)
        except Exception:  # A broken store must not break startup.
            values[field.name] = field.default
    return Settings(**values)


def save(settings, write=None):
    """``write(name, text)`` persists one value; defaults to Krita's store."""
    write = write or _krita_writer()
    for field in fields(Settings):
        write(field.name, _format(getattr(settings, field.name)))


def update(settings, write=None, **changes):
    """Return and persist ``settings`` with ``changes`` applied."""
    changed = replace(settings, **changes)
    save(changed, write)
    return changed


def log_directory():
    """KSP's diagnostic-log folder inside Krita's application-data folder."""
    from krita import Krita
    return os.path.join(Krita.getAppDataLocation(), GROUP, "logs")


def _krita_reader():
    from krita import Krita
    application = Krita.instance()
    return lambda name, default: application.readSetting(GROUP, name, default)


def _krita_writer():
    from krita import Krita
    application = Krita.instance()
    return lambda name, value: application.writeSetting(GROUP, name, value)
