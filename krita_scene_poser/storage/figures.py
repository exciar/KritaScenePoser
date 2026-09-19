"""Bundled figure discovery and loading.

A figure is a ``<id>.rig.json`` plus ``<id>.mesh`` pair. Damaged or
unreadable figures are reported and skipped, so they never stop KSP from
starting.
"""

import os

from .mesh_io import MeshFormatError, read_mesh
from .rig_io import RigFormatError, read_rig

RIG_SUFFIX, MESH_SUFFIX = ".rig.json", ".mesh"
DEFAULT_FIGURE = "body_chan"


def default_folder():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "figures")


def load_figure(figure, folder=None):
    """``(RigData, MeshData)``; raises ValueError subclasses for bad files."""
    folder = folder or default_folder()
    with open(os.path.join(folder, figure + RIG_SUFFIX), encoding="utf-8") as handle:
        rig = read_rig(handle.read())
    with open(os.path.join(folder, figure + MESH_SUFFIX), "rb") as handle:
        mesh = read_mesh(handle.read(), joint_count=len(rig.joints))
    return rig, mesh


def available_figures(folder=None):
    """``([(id, display name)], [problems])`` for every loadable figure, sorted."""
    folder = folder or default_folder()
    figures, problems = [], []
    try:
        names = sorted(os.listdir(folder))
    except OSError as error:
        return [], ["The figure folder cannot be read: {}".format(error)]
    for name in names:
        if not name.endswith(RIG_SUFFIX):
            continue
        figure = name[:-len(RIG_SUFFIX)]
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                rig = read_rig(handle.read())
            if not os.path.isfile(os.path.join(folder, figure + MESH_SUFFIX)):
                raise MeshFormatError("its mesh file is missing")
            figures.append((figure, rig.display_name))
        except (OSError, RigFormatError, MeshFormatError) as error:
            problems.append("{}: {}".format(figure, error))
    return figures, problems
