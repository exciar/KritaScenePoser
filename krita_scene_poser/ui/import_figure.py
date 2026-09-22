"""File dialog and storage location for figure import; the conversion is in storage."""

import os

from PyQt5.QtWidgets import QFileDialog

from ..integration import settings as ksp_settings
from ..storage import figures as ksp_figures
from ..storage.import_blend import read_blend
from ..storage.import_figure import (
    MAP_SUFFIX, FigureImportError, convert, describe, read_map_file,
)
from ..storage.import_glb import custom_map_from_vrm, read_glb

FILTER = ("3D figures (*.glb *.vrm *.blend);;glTF binary (*.glb);;VRM (*.vrm);;"
          "Blender (*.blend)")
MAX_FILE_BYTES = 256 * 1024 * 1024


def figures_folder():
    return ksp_settings.data_directory("figures")


def folders():
    try:
        return [ksp_figures.default_folder(), figures_folder()]
    except Exception:
        return [ksp_figures.default_folder()]


def choose_file(parent):
    path, _ = QFileDialog.getOpenFileName(parent, "Import a figure", "", FILTER)
    return path


def import_file(path, known_ids=()):
    """Read, convert, and save one file. Returns ``(figure id, message)``."""
    if not path:
        return None, ""
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise FigureImportError(
            "That file is {:.0f} MB. KSP reads figures up to {:.0f} MB.".format(
                size / 1e6, MAX_FILE_BYTES / 1e6))
    name = os.path.basename(path)
    stem, extension = os.path.splitext(name)
    extension = extension.lower()
    custom = _custom_map(path)
    if extension in (".glb", ".vrm"):
        with open(path, "rb") as handle:
            data = handle.read()
        source = read_glb(data, name)
        if custom is None:
            custom = custom_map_from_vrm(data)
    elif extension == ".blend":
        source = read_blend(path, name)
    else:
        raise FigureImportError(
            "KSP imports .glb, .vrm, and .blend files. This one is {}.".format(
                extension or "unnamed"))
    figure = ksp_figures.figure_id(stem, known_ids)
    rig, mesh, report = convert(source, figure, _display_name(stem), custom_map=custom)
    ksp_figures.write_figure(figures_folder(), figure, rig, mesh)
    return figure, describe(report, rig.display_name)


def _display_name(stem):
    text = str(stem).replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else "Imported figure"


def _custom_map(path):
    for candidate in (os.path.splitext(path)[0] + MAP_SUFFIX, path + MAP_SUFFIX):
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as handle:
                return read_map_file(handle.read())
    return None
