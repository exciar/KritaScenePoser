#!/usr/bin/env python3
"""Author KSP's bundled pose presets. Development tool; never shipped.

    py -3.14 tools/make_presets.py [--preview presets.png] [--figure body_kun]

Poses are written in **world** terms - aim this bone that way, bend that joint
about a world axis - so one description suits any KSP figure and reads like
the pose it makes. Joint limits apply while building, so a preset can never
contain a rotation the docker itself would refuse.

The preview rasterizes the skinned figures in pure Python, so presets can be
checked without Krita or a GPU.
"""

import argparse
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from compile_assets import write_png  # noqa: E402
from krita_scene_poser.core.math3d import Quat, Vec3, X_AXIS, Y_AXIS, Z_AXIS  # noqa: E402
from krita_scene_poser.core.picking import skin_point  # noqa: E402
from krita_scene_poser.storage.figures import load_figure  # noqa: E402
from krita_scene_poser.storage.presets import write_preset  # noqa: E402
from krita_scene_poser.storage.scene_io import write_pose  # noqa: E402

OUTPUT = ROOT / "krita_scene_poser" / "assets" / "poses"


class Builder:
    """Builds a pose with world-space instructions, clamped by joint limits."""

    def __init__(self, skeleton):
        self.skeleton = skeleton
        self.pose = skeleton.rest_pose()

    def _index(self, name):
        return self.skeleton.index(name)

    def _child(self, index):
        for other, joint in enumerate(self.skeleton.joints):
            if joint.parent == index:
                return other
        return None

    def direction(self, name):
        """Which way the bone points now, in world space."""
        index = self._index(name)
        transforms = self.skeleton.transforms(self.pose)
        child = self._child(index)
        if child is None:
            return transforms[index].rotation.rotate(Y_AXIS)
        return (transforms[child].position - transforms[index].position).normalized()

    def aim(self, name, direction):
        """Point the bone along a world direction."""
        index = self._index(name)
        self.pose = self.skeleton.rotate_world(
            self.pose, index, Quat.between(self.direction(name), Vec3(*direction).normalized()))
        return self

    def bend(self, name, axis, degrees):
        """Turn the bone about a world axis."""
        index = self._index(name)
        self.pose = self.skeleton.rotate_world(
            self.pose, index, Quat.from_axis_angle(Vec3(*axis), math.radians(degrees)))
        return self

    def both(self, name, method, *args):
        """Apply to the .L and .R counterparts, mirroring x for the right side."""
        for side in ("L", "R"):
            values = []
            for value in args:
                if isinstance(value, (tuple, list)) and len(value) == 3 and side == "R":
                    values.append((-value[0], value[1], value[2]))
                elif isinstance(value, (int, float)) and side == "R" and method == "bend":
                    values.append(-value)
                else:
                    values.append(value)
            getattr(self, method)("{}.{}".format(name, side), *values)
        return self

    def fold(self, name, degrees):
        """Bend a hinge - an elbow or knee - the way it actually folds.

        The hinge axis is the bone's own, so this works whatever direction the
        limb currently points, and positive always means "fold further".
        """
        index = self._index(name)
        transforms = self.skeleton.transforms(self.pose)
        limit = self.skeleton.limits[index]
        axis = transforms[index].rotation.rotate(X_AXIS)
        if limit is not None and limit.swing_x[1] <= 0.0:
            axis = axis * -1.0  # This rig folds toward local -X.
        self.pose = self.skeleton.rotate_world(
            self.pose, index, Quat.from_axis_angle(axis, math.radians(degrees)))
        return self

    def twist(self, name, degrees):
        """Roll the bone about its own length, which aims a hinge fold plane."""
        self.pose = self.skeleton.rotate_world(
            self.pose, self._index(name),
            Quat.from_axis_angle(self.direction(name), math.radians(degrees)))
        return self

    def align_hinge(self, name, axis):
        """Roll the parent bone so this hinge turns about a chosen world axis.

        An elbow can only fold in one plane, so which way a forearm sweeps is
        decided by the upper arm's roll, not by the elbow itself.
        """
        index = self._index(name)
        parent = self.skeleton.joints[index].parent
        parent_name = self.skeleton.joints[parent].name
        bone = self.direction(parent_name)
        transforms = self.skeleton.transforms(self.pose)
        limit = self.skeleton.limits[index]
        hinge = transforms[index].rotation.rotate(X_AXIS)
        if limit is not None and limit.swing_x[1] <= 0.0:
            hinge = hinge * -1.0
        target = Vec3(*axis)
        target = target - bone * target.dot(bone)
        current = hinge - bone * hinge.dot(bone)
        if target.length() < 1e-6 or current.length() < 1e-6:
            return self
        target, current = target.normalized(), current.normalized()
        angle = math.atan2(current.cross(target).dot(bone),
                           min(1.0, max(-1.0, current.dot(target))))
        return self.twist(parent_name, math.degrees(angle))

    def move(self, translation):
        self.pose = type(self.pose)(self.pose.rotations, Vec3(*translation),
                                    self.pose.root_rotation, self.pose.root_scale)
        return self

    def turn(self, degrees):
        self.pose = type(self.pose)(
            self.pose.rotations, self.pose.root_translation,
            Quat.from_axis_angle(Y_AXIS, math.radians(degrees)), self.pose.root_scale)
        return self


def t_pose(b):
    """Arms straight out to the sides."""
    for side, x in (("L", 1.0), ("R", -1.0)):
        b.aim("upper_arm." + side, (x, 0.0, 0.0)).aim("forearm." + side, (x, 0.0, 0.0))
        b.aim("hand." + side, (x, 0.0, 0.0))
    return b


def relaxed(b):
    """Weight on one leg, hips tilted, the other knee soft: a standing rest."""
    b.bend("hips", Z_AXIS, -6).bend("waist", Z_AXIS, 4).bend("torso", Z_AXIS, 3)
    b.bend("chest", Y_AXIS, -6).bend("neck", Z_AXIS, -3).bend("head", Y_AXIS, 8)
    b.aim("thigh.R", (-0.03, -1.0, 0.02)).fold("shin.R", 4)
    b.aim("thigh.L", (0.09, -1.0, -0.12)).fold("shin.L", 16)
    b.aim("foot.L", (0.1, -0.3, 0.95))
    b.bend("upper_arm.L", Z_AXIS, -4).bend("upper_arm.R", Z_AXIS, 3)
    b.fold("forearm.L", 14).fold("forearm.R", 10)
    return b


def sitting(b):
    """Seated on a surface at knee height, hands resting beside the hips."""
    b.both("thigh", "aim", (0.07, -0.12, 1.0))
    b.fold("shin.L", 88).fold("shin.R", 88)
    b.both("foot", "aim", (0.0, -1.0, 0.15))
    b.bend("waist", X_AXIS, 6).bend("torso", X_AXIS, 4)
    b.both("upper_arm", "aim", (0.16, -1.0, 0.05))
    b.fold("forearm.L", 22).fold("forearm.R", 22)
    return b


def kneeling(b):
    """One knee down, the other foot planted forward."""
    b.aim("thigh.R", (-0.09, -0.97, -0.22)).fold("shin.R", 142)
    b.aim("foot.R", (0.0, -0.12, -0.99))
    b.aim("thigh.L", (0.1, -0.72, 0.69)).fold("shin.L", 95)
    b.aim("foot.L", (0.0, -0.18, 0.98))
    b.bend("waist", X_AXIS, -4).bend("chest", X_AXIS, -3)
    b.both("upper_arm", "aim", (0.16, -1.0, 0.08))
    b.fold("forearm.L", 20).fold("forearm.R", 20)
    return b


def walking(b):
    """Mid-stride, with the opposite arm forward."""
    b.aim("thigh.L", (0.05, -1.0, 0.42)).fold("shin.L", 12)
    b.aim("foot.L", (0.0, -0.35, 1.0))
    b.aim("thigh.R", (-0.05, -1.0, -0.3)).fold("shin.R", 32)
    b.aim("foot.R", (0.0, -0.8, 0.55))
    b.bend("chest", Y_AXIS, 6).bend("head", Y_AXIS, -4)
    b.aim("upper_arm.L", (0.12, -1.0, -0.4)).fold("forearm.L", 25)
    b.aim("upper_arm.R", (-0.12, -1.0, 0.42)).fold("forearm.R", 35)
    return b


def running(b):
    """A bigger stride, leaning forward, elbows folded."""
    b.bend("waist", X_AXIS, 10).bend("torso", X_AXIS, 6).bend("head", X_AXIS, -12)
    b.aim("thigh.L", (0.05, -0.5, 0.86)).fold("shin.L", 75)
    b.aim("foot.L", (0.0, -0.55, 0.85))
    b.aim("thigh.R", (-0.05, -1.0, -0.45)).fold("shin.R", 80)
    b.aim("foot.R", (0.0, -0.5, -0.85))
    b.aim("upper_arm.L", (0.12, -0.85, -0.5)).fold("forearm.L", 95)
    b.aim("upper_arm.R", (-0.12, -0.9, 0.4)).fold("forearm.R", 85)
    return b


def hands_clasped(b):
    """Forearms drawn in, hands meeting at the chest."""
    # An elbow folds in one plane only, so the upper arm is rolled until that
    # plane is horizontal; the fold then sweeps each forearm across the chest.
    b.aim("upper_arm.L", (0.34, -0.82, 0.46)).align_hinge("forearm.L", (0.0, 0.0, -1.0))
    b.fold("forearm.L", 118)
    b.aim("upper_arm.R", (-0.40, -0.78, 0.48)).align_hinge("forearm.R", (0.0, 0.0, 1.0))
    b.fold("forearm.R", 126)
    b.bend("chest", X_AXIS, 4).bend("head", Y_AXIS, -5)
    return b


def reaching_up(b):
    """One arm stretched overhead, looking after it."""
    b.aim("upper_arm.L", (0.25, 1.0, 0.1)).aim("forearm.L", (0.15, 1.0, 0.05))
    b.aim("hand.L", (0.1, 1.0, 0.0))
    b.bend("chest", Z_AXIS, -6).bend("waist", Z_AXIS, -4)
    b.bend("head", X_AXIS, -18).bend("neck", X_AXIS, -12)
    b.bend("upper_arm.R", Z_AXIS, 8).fold("forearm.R", 22)
    b.aim("thigh.R", (-0.05, -1.0, 0.0))
    return b


def crouching(b):
    """Low crouch, knees apart, hands forward for balance."""
    b.aim("thigh.L", (0.16, -0.3, 0.94)).fold("shin.L", 130)
    b.aim("thigh.R", (-0.16, -0.3, 0.94)).fold("shin.R", 130)
    b.both("foot", "aim", (0.05, -0.75, 0.66))
    b.bend("waist", X_AXIS, 14).bend("torso", X_AXIS, 8).bend("head", X_AXIS, -14)
    b.both("upper_arm", "aim", (0.14, -0.9, 0.42))
    b.fold("forearm.L", 55).fold("forearm.R", 55)
    return b


PRESETS = (
    ("t-pose", "T-pose", t_pose),
    ("relaxed", "Relaxed stance", relaxed),
    ("sitting", "Sitting", sitting),
    ("kneeling", "Kneeling", kneeling),
    ("walking", "Walking", walking),
    ("running", "Running", running),
    ("hands-clasped", "Hands clasped", hands_clasped),
    ("reaching-up", "Reaching up", reaching_up),
    ("crouching", "Crouching", crouching),
)


def build(skeleton, mesh, maker):
    builder = Builder(skeleton)
    maker(builder)
    return ground(skeleton, mesh, builder.pose)


def ground(skeleton, mesh, pose):
    """Drop or lift the figure so its lowest point rests on y = 0.

    Sitting and kneeling poses would otherwise float or sink, and every pose
    should stand on the grid the viewport draws.
    """
    lowest = min(p[1] for p in skinned_positions(skeleton, mesh, pose))
    translation = pose.root_translation
    return type(pose)(pose.rotations,
                      Vec3(translation.x, translation.y - lowest, translation.z),
                      pose.root_rotation, pose.root_scale)


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
        ground = y0 + height - 20
        for x in range(x0 + 6, x0 + width - 6):
            image[(ground * stride + x) * 3:(ground * stride + x) * 3 + 3] = b"\xc0\xc0\xc0"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--figure", default="body_kun")
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--only", nargs="*", help="Build just these presets.")
    args = parser.parse_args(argv)

    rig, mesh = load_figure(args.figure)
    skeleton = rig.skeleton
    chosen = [p for p in PRESETS if not args.only or p[0] in args.only]
    built = []
    for preset, label, maker in chosen:
        pose = build(skeleton, mesh, maker)
        write_preset(str(args.output), preset, write_pose(skeleton, pose), label)
        built.append((label, pose))
        print("{:<14} {}".format(preset, label))
    if args.preview:
        print("preview:", render_sheet(str(args.preview), skeleton, mesh, built))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
