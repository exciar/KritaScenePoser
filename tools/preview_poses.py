#!/usr/bin/env python3
"""Check the bundled poses in krita_scene_poser/assets/poses.

    py -3.14 tools/preview_poses.py
    py -3.14 tools/preview_poses.py --sheet docs/images/pose-presets.png
    py -3.14 tools/preview_poses.py --ground sitting kneeling

Poses are saved from the docker with Save Pose…, so this tool never writes one
of its own. It reports where each pose stands, renders a contact sheet in pure
Python (no Krita, no GPU), and with --ground drops or lifts a pose onto the
floor, which the docker cannot do yet.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from compile_assets import write_png  # noqa: E402
from krita_scene_poser.core.math3d import Vec3  # noqa: E402
from krita_scene_poser.core.picking import skin_point  # noqa: E402
from krita_scene_poser.storage.figures import load_figure  # noqa: E402
from krita_scene_poser.storage.presets import (  # noqa: E402
    SUFFIX, available_presets, default_folder, load_preset, preset_text, write_preset,
)
from krita_scene_poser.storage.scene_io import write_pose  # noqa: E402

FIGURES = ("body_kun", "body_chan")


def ground(skeleton, mesh, pose):
    """Drop or lift the figure so its lowest point rests on y = 0."""
    lowest = min(p[1] for p in skinned_positions(skeleton, mesh, pose))
    translation = pose.root_translation
    return type(pose)(pose.rotations,
                      Vec3(translation.x, translation.y - lowest, translation.z),
                      pose.root_rotation, pose.root_scale)


def floor_offset(skeleton, mesh, pose):
    """How far the lowest point sits above (+) or below (-) the floor, in metres."""
    return min(p[1] for p in skinned_positions(skeleton, mesh, pose))


def ground_preset(preset, folder=None):
    """Rewrite one pose file so it stands on the floor. Returns the metres moved."""
    folder = folder or default_folder()
    rig, mesh = load_figure(FIGURES[0])
    skeleton = rig.skeleton
    document = json.loads(preset_text(preset, folder))
    pose = load_preset(preset, skeleton, folder).pose
    moved = floor_offset(skeleton, mesh, pose)
    text = write_pose(skeleton, ground(skeleton, mesh, pose), document.get("figure", ""))
    write_preset(folder, preset, text, document.get("name"))
    return moved


# Preview --------------------------------------------------------------------


def skinned_positions(skeleton, mesh, pose):
    matrices = skeleton.skinning_matrices(pose)
    return [skin_point(matrices, mesh.joints, mesh.weights, mesh.positions, v)
            for v in range(mesh.vertex_count)]


PROJECTIONS = (lambda p: (p[0], p[1], p[2]), lambda p: (-p[2], p[1], p[0]))


def render_sheet(path, skeleton, mesh, poses, panel=(180, 380), columns=3):
    """A contact sheet of skinned figures, front and side, for a visual check."""
    cell = panel[0] * len(PROJECTIONS)
    height = panel[1]
    rows = (len(poses) + columns - 1) // columns
    stride, total_height = cell * columns, height * rows
    image = bytearray(b"\xf4" * (stride * total_height * 3))
    light = Vec3(-0.4, 0.6, 0.7).normalized()
    for index, (label, pose) in enumerate(poses):
        points = skinned_positions(skeleton, mesh, pose)
        for view, project in enumerate(PROJECTIONS):
            _draw(image, stride, points, mesh, light, panel,
                  (index % columns) * cell + view * panel[0], (index // columns) * height,
                  project)
    write_png(path, stride, total_height, image)
    return path


def _draw(image, stride, points, mesh, light, panel, x0, y0, project):
    width, height = panel
    scale = (height - 40) / 1.95
    screen = [(x0 + width / 2 + project(p)[0] * scale,
               y0 + height - 20 - project(p)[1] * scale, project(p)[2])
              for p in points]
    depth = [-1e9] * (width * height)
    for t in range(0, len(mesh.indices), 3):
        a, b, c = (screen[mesh.indices[t + k]] for k in range(3))
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area) < 1e-12:
            continue
        i0 = mesh.indices[t]
        normal = Vec3(*mesh.normals[i0 * 3:i0 * 3 + 3])
        shade = 0.30 + 0.70 * max(0.0, normal.dot(light))
        color = bytes(int(v * shade) for v in (215, 205, 190))
        xmin, xmax = max(int(min(a[0], b[0], c[0])), x0), min(int(max(a[0], b[0], c[0])) + 1, x0 + width)
        ymin, ymax = max(int(min(a[1], b[1], c[1])), y0), min(int(max(a[1], b[1], c[1])) + 1, y0 + height)
        for y in range(ymin, ymax):
            for x in range(xmin, xmax):
                px, py = x + 0.5, y + 0.5
                w0 = ((b[0] - px) * (c[1] - py) - (b[1] - py) * (c[0] - px)) / area
                w1 = ((c[0] - px) * (a[1] - py) - (c[1] - py) * (a[0] - px)) / area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * a[2] + w1 * b[2] + w2 * c[2]
                slot = (y - y0) * width + (x - x0)
                if z > depth[slot]:
                    depth[slot] = z
                    image[(y * stride + x) * 3:(y * stride + x) * 3 + 3] = color
    # A baseline marks the ground so sitting and crouching poses are readable.
    ground_line = y0 + height - 20
    for x in range(x0 + 6, x0 + width - 6):
        image[(ground_line * stride + x) * 3:(ground_line * stride + x) * 3 + 3] = b"\xc0\xc0\xc0"


def report(folder):
    """Print one line per pose: its name, joints, and where it stands."""
    presets, problems = available_presets(folder)
    for problem in problems:
        print("damaged:", problem)
    if not presets:
        print("No {} files in {}".format(SUFFIX, folder))
        return presets
    figures = {name: load_figure(name) for name in FIGURES}
    header = "{:<22}{:<22}{:>7}".format("file", "shown as", "joints")
    print(header + "".join("{:>12}".format(name) for name in FIGURES))
    for preset, label in presets:
        rig, mesh = figures[FIGURES[0]]
        applied = load_preset(preset, rig.skeleton, folder)
        offsets = []
        for name in FIGURES:
            rig, mesh = figures[name]
            pose = load_preset(preset, rig.skeleton, folder).pose
            offsets.append(floor_offset(rig.skeleton, mesh, pose))
        print("{:<22}{:<22}{:>7}".format(preset, label, len(applied.applied))
              + "".join("{:>+12.3f}".format(value) for value in offsets))
    print("\nFloor offsets are metres: + floats above the ground, - sinks into it.")
    return presets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path, default=Path(default_folder()),
                        help="Where the pose files are (default: the bundled folder).")
    parser.add_argument("--figure", default=FIGURES[0], help="Figure to draw the sheet with.")
    parser.add_argument("--sheet", type=Path, help="Render a contact sheet to this PNG.")
    parser.add_argument("--ground", nargs="+", metavar="ID", default=(),
                        help="Rewrite these pose files so they stand on the floor.")
    args = parser.parse_args(argv)
    folder = str(args.folder)

    for preset in args.ground:
        path = Path(folder) / (preset + SUFFIX)
        if not path.exists():
            parser.error("No such pose: {}".format(path))
        moved = ground_preset(preset, folder)
        print("grounded {:<20} moved {:+.3f} m".format(preset, -moved))

    presets = report(folder)
    if args.sheet and presets:
        rig, mesh = load_figure(args.figure)
        poses = [(label, load_preset(preset, rig.skeleton, folder).pose)
                 for preset, label in presets]
        print("sheet:", render_sheet(str(args.sheet), rig.skeleton, mesh, poses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
