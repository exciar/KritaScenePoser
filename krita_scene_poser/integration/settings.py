"""Preferences in Krita's settings store. Unreadable values fall back to defaults, so a
bad setting cannot stop the plugin loading.
"""

from dataclasses import dataclass, fields, replace
import os

GROUP = "krita_scene_poser"


@dataclass(frozen=True)
class Settings:
    diagnostic_log: bool = False
    lineart: str = ""  # LineArtSettings as JSON; empty means defaults.
    output: str = ""  # OutputSettings as JSON.
    workspace: str = ""  # The last scene, restored when the docker opens again.
    joint_limits: bool = True  # Stop joints bending further than a body could.


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


def save(settings, write=None, names=None):
    """``write(name, text)`` persists one value; defaults to Krita's store.

    ``names`` limits which values are written (all by default).
    """
    write = write or _krita_writer()
    for field in fields(Settings):
        if names is None or field.name in names:
            write(field.name, _format(getattr(settings, field.name)))


def update(settings, write=None, **changes):
    """Return ``settings`` with ``changes`` applied, persisting only the changes.

    The extension and each docker hold their own copies, so a save from a
    stale copy must never undo another owner's change.
    """
    changed = replace(settings, **changes)
    save(changed, write, names=changes.keys())
    return changed


def data_directory(*parts):
    from krita import Krita
    return os.path.join(Krita.getAppDataLocation(), GROUP, *parts)


def log_directory():
    return data_directory("logs")


def poses_directory():
    directory = data_directory("poses")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return ""  # A dialog with no starting folder still works.
    return directory


def _krita_reader():
    from krita import Krita
    application = Krita.instance()
    return lambda name, default: application.readSetting(GROUP, name, default)


def _krita_writer():
    from krita import Krita
    application = Krita.instance()
    return lambda name, value: application.writeSetting(GROUP, name, value)
