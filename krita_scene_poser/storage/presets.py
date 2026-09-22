"""Bundled pose presets in assets/poses."""

import json
import os

from .scene_io import SceneFormatError, apply_pose, read_pose

SUFFIX = ".pose.json"


def default_folder():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "poses")


def _title(preset):
    return preset.replace("-", " ").replace("_", " ").strip().capitalize()


def available_presets(folder=None):
    """``([(id, display name)], [problems])``, ordered by the name shown."""
    folder = folder or default_folder()
    presets, problems = [], []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return [], []  # No preset folder at all is not an error worth showing.
    for name in names:
        if not name.endswith(SUFFIX):
            continue
        preset = name[:-len(SUFFIX)]
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or data.get("format") != "ksp-pose":
                raise SceneFormatError("not a KSP pose file")
            label = data.get("name")
            presets.append((preset, label if isinstance(label, str) and label else _title(preset)))
        except (OSError, ValueError) as error:
            problems.append("{}: {}".format(preset, error))
    presets.sort(key=lambda item: item[1])
    return presets, problems


def preset_text(preset, folder=None):
    folder = folder or default_folder()
    with open(os.path.join(folder, preset + SUFFIX), encoding="utf-8") as handle:
        return handle.read()


def load_preset(preset, skeleton, folder=None):
    """Apply a bundled preset to ``skeleton``; returns an ``Applied``."""
    return read_pose(preset_text(preset, folder), skeleton)


def load_all(skeleton, folder=None):
    """Every bundled preset applied to ``skeleton``: ``[(id, name, Applied)]``.

    Used by the tests and the headless probe to prove each one still loads.
    """
    result = []
    for preset, label in available_presets(folder)[0]:
        result.append((preset, label, load_preset(preset, skeleton, folder)))
    return result


def write_preset(folder, preset, text, name=None):
    """Write a preset file; development helper for authoring the bundled set."""
    data = json.loads(text)
    if name:
        data["name"] = name
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, preset + SUFFIX)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
    return path


__all__ = ["SUFFIX", "available_presets", "default_folder", "load_all", "load_preset",
           "preset_text", "write_preset", "apply_pose"]
