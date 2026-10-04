"""Pose and scene file dialogs. Each call returns a status message and never raises."""

import os

from PyQt5.QtWidgets import QFileDialog

from ..integration import settings as ksp_settings
from ..storage import presets as ksp_presets
from ..storage.import_figure import FigureImportError
from . import import_figure as figure_import

POSE_FILTER = "KSP pose (*.pose.json)"
SCENE_FILTER = "KSP scene (*.scene.json)"
POSE_SUFFIX, SCENE_SUFFIX = ".pose.json", ".scene.json"


def _start_directory(name=""):
    try:
        folder = ksp_settings.poses_directory()
    except Exception:
        folder = ""
    return os.path.join(folder, name) if folder else name


def _with_suffix(path, suffix):
    return path if path.lower().endswith(suffix.lower()) else path + suffix


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def save_pose(parent, session):
    path, _ = QFileDialog.getSaveFileName(parent, "Save KSP Pose",
                                          _start_directory("pose" + POSE_SUFFIX), POSE_FILTER)
    if not path:
        return ""
    path = _with_suffix(path, POSE_SUFFIX)
    _write(path, session.pose_text())
    return "Saved the pose to {}.".format(os.path.basename(path))


def load_pose(parent, session):
    path, _ = QFileDialog.getOpenFileName(parent, "Load KSP Pose",
                                          _start_directory(), POSE_FILTER)
    if not path:
        return ""
    applied = session.load_pose_text(_read(path))
    return "{} {}".format(os.path.basename(path), applied.describe(_figure_name(session)))


def save_scene(parent, session):
    path, _ = QFileDialog.getSaveFileName(parent, "Save KSP Scene",
                                          _start_directory("scene" + SCENE_SUFFIX), SCENE_FILTER)
    if not path:
        return ""
    path = _with_suffix(path, SCENE_SUFFIX)
    _write(path, session.scene_text())
    return "Saved the scene to {}.".format(os.path.basename(path))


def load_scene(parent, session):
    path, _ = QFileDialog.getOpenFileName(parent, "Load KSP Scene",
                                          _start_directory(), SCENE_FILTER)
    if not path:
        return ""
    scene = session.load_scene_text(_read(path))
    return "{} {}".format(os.path.basename(path), scene.applied.describe(_figure_name(session)))


def import_pose(parent, session):
    """Take the pose from a posed model file, as one undo step."""
    path, _ = QFileDialog.getOpenFileName(parent, "Import a pose", _start_directory(),
                                          figure_import.POSE_FILTER)
    if not path:
        return ""
    size = os.path.getsize(path)
    if size > figure_import.MAX_FILE_BYTES:
        raise FigureImportError(
            "That file is {:.0f} MB. KSP reads models up to {:.0f} MB.".format(
                size / 1e6, figure_import.MAX_FILE_BYTES / 1e6))
    with open(path, "rb") as handle:
        data = handle.read()
    name = os.path.basename(path)
    result = session.load_pose_file(
        data, name, custom_map=figure_import.custom_map_for(path))
    return "{} {}".format(name, result.describe(_figure_name(session)))


def apply_preset(session, preset):
    applied = session.load_pose_text(ksp_presets.preset_text(preset))
    return applied.describe(_figure_name(session))


def _figure_name(session):
    rig = getattr(session, "rig", None)
    return getattr(rig, "display_name", None) or "the figure"
