#!/usr/bin/env python3
"""Turn posed .glb files into KSP pose files.

    py -3.14 tools/import_pose.py poses/*.glb
    py -3.14 tools/import_pose.py sitting.glb --name "Sitting on floor" --ground

Pose the figure in Blender, export glTF Binary with the armature, and this
writes `<name>.pose.json` into the bundled pose folder, where the docker lists
it. A `<model>.ksp-map.json` beside the file is picked up on its own.
"""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from krita_scene_poser.storage.figures import load_figure  # noqa: E402
from krita_scene_poser.storage.import_figure import FigureImportError, MAP_SUFFIX  # noqa: E402
from krita_scene_poser.storage.import_pose import read_pose, read_pose_map  # noqa: E402
from krita_scene_poser.storage.presets import default_folder, write_preset  # noqa: E402
from krita_scene_poser.storage.scene_io import write_pose  # noqa: E402
from preview_poses import floor_offset, ground  # noqa: E402


def preset_id(stem):
    """`Sitting On Floor` becomes `sitting-on-floor`, which the docker titles back."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(stem).lower()).strip("-")
    return slug or "pose"


def label_for(stem):
    return preset_id(stem).replace("-", " ").capitalize()


def convert(path, skeleton, mesh, to_ground=False):
    """Read one posed file; returns ``(PoseImport, pose, floor offset)``."""
    custom = None
    map_file = path.with_suffix("").with_name(path.stem + MAP_SUFFIX)
    if map_file.exists():
        custom = read_pose_map(map_file.read_text(encoding="utf-8"))
    result = read_pose(path.read_bytes(), skeleton, path.name, custom_map=custom)
    pose = ground(skeleton, mesh, result.pose) if to_ground else result.pose
    return result, pose, floor_offset(skeleton, mesh, pose)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="Posed .glb or .vrm files.")
    parser.add_argument("--output", type=Path, default=Path(default_folder()),
                        help="Where to write the pose files.")
    parser.add_argument("--figure", default="body_kun",
                        help="The figure to read the pose onto (default: body_kun).")
    parser.add_argument("--name", help="The name shown in the docker, for a single file.")
    parser.add_argument("--ground", action="store_true",
                        help="Stand the figure on the floor after reading the pose.")
    args = parser.parse_args(argv)
    if args.name and len(args.files) > 1:
        parser.error("--name takes one file at a time.")

    rig, mesh = load_figure(args.figure)
    failed = 0
    for path in args.files:
        if not path.exists():
            print("{}: no such file".format(path))
            failed += 1
            continue
        try:
            result, pose, offset = convert(path, rig.skeleton, mesh, args.ground)
        except (FigureImportError, OSError) as error:
            print("{}: {}".format(path.name, error))
            failed += 1
            continue
        preset = preset_id(path.stem)
        written = write_preset(str(args.output), preset,
                               write_pose(rig.skeleton, pose, args.figure),
                               args.name or label_for(path.stem))
        print("{:<24} {:>3} joints  floor {:+.3f} m  {}".format(
            preset, len(result.applied), offset, result.scheme))
        if result.missing:
            print("{:<24} {} joints stayed at rest".format("", len(result.missing)))
        if result.facing_flipped:
            print("{:<24} the file faces away, so the pose was turned around".format(""))
        if abs(offset) > 0.01 and not args.ground:
            print("{:<24} off the floor; rerun with --ground to drop it".format(""))
        print("{:<24} {}".format("", written))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
