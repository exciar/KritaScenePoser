"""Figure discovery and loading from the bundled folder and any extra folders.

Damaged figures are reported and skipped, so one bad file never stops KSP starting.
"""

import os
import re

from .mesh_io import MeshFormatError, read_mesh, write_mesh
from .rig_io import RigFormatError, read_rig, write_rig

RIG_SUFFIX, MESH_SUFFIX = ".rig.json", ".mesh"
DEFAULT_FIGURE = "body_chan"


def default_folder():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "figures")


def _folders(folder=None, folders=None):
    if folders:
        return [path for path in folders if path]
    return [folder or default_folder()]


def load_figure(figure, folder=None, folders=None):
    """``(RigData, MeshData)``; raises ValueError subclasses for bad files."""
    searched = _folders(folder, folders)
    for path in searched:
        rig_path = os.path.join(path, figure + RIG_SUFFIX)
        if not os.path.isfile(rig_path):
            continue
        with open(rig_path, encoding="utf-8") as handle:
            rig = read_rig(handle.read())
        with open(os.path.join(path, figure + MESH_SUFFIX), "rb") as handle:
            mesh = read_mesh(handle.read(), joint_count=len(rig.joints))
        return rig, mesh
    # Keep the original error for a missing file, which names the path.
    with open(os.path.join(searched[0], figure + RIG_SUFFIX), encoding="utf-8") as handle:
        rig = read_rig(handle.read())
    raise MeshFormatError("The mesh file for {} is missing.".format(figure))


def available_figures(folder=None, folders=None):
    """``([(id, display name)], [problems])`` for every loadable figure, sorted.

    A figure in a later folder replaces one of the same id in an earlier one,
    so a user's own copy wins over a bundled figure with the same name.
    """
    found, problems = {}, []
    for path in _folders(folder, folders):
        try:
            names = sorted(os.listdir(path))
        except FileNotFoundError:
            continue  # An empty user folder is normal, not a problem.
        except OSError as error:
            problems.append("The figure folder cannot be read: {}".format(error))
            continue
        for name in names:
            if not name.endswith(RIG_SUFFIX):
                continue
            figure = name[:-len(RIG_SUFFIX)]
            try:
                with open(os.path.join(path, name), encoding="utf-8") as handle:
                    rig = read_rig(handle.read())
                if not os.path.isfile(os.path.join(path, figure + MESH_SUFFIX)):
                    raise MeshFormatError("its mesh file is missing")
                found[figure] = rig.display_name
            except (OSError, RigFormatError, MeshFormatError) as error:
                problems.append("{}: {}".format(figure, error))
    figures = sorted(found.items(), key=lambda item: item[1].lower())
    return figures, problems


def figure_id(name, taken=()):
    """A safe, unique file name for an imported figure, from any text."""
    base = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_") or "figure"
    base = base[:40]
    if base not in taken:
        return base
    for number in range(2, 1000):
        candidate = "{}_{}".format(base, number)
        if candidate not in taken:
            return candidate
    raise ValueError("Too many figures share the name {!r}.".format(name))


def write_figure(folder, figure, rig, mesh):
    """Save a figure as the same pair of files the bundled figures use."""
    os.makedirs(folder, exist_ok=True)
    rig_path = os.path.join(folder, figure + RIG_SUFFIX)
    mesh_path = os.path.join(folder, figure + MESH_SUFFIX)
    text = write_rig(figure, rig.display_name, rig.joints, rig.source)
    payload = write_mesh(mesh)
    with open(rig_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    with open(mesh_path, "wb") as handle:
        handle.write(payload)
    return rig_path, mesh_path
