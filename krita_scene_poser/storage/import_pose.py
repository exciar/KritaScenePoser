"""Take a pose off a posed `.glb` and put it on a KSP figure.

Only rotations travel between rigs, plus how far the hips moved. A rig's bone
lengths are its own, so copying positions would stretch the figure; copying the
turn of each bone puts the same pose on any body. Joint limits apply on the way
in, exactly as they do to a drag, so an imported pose is one the docker could
have made itself.
"""

from dataclasses import dataclass, replace
import math

from ..core.math3d import Quat, Vec3, Y_AXIS
from ..core.skeleton import Pose
from .import_figure import (
    FigureImportError, _facing, _rotation, _translation, map_bones, read_map_file,
)
from .import_glb import read_glb_pose

HALF_TURN = Quat.from_axis_angle(Y_AXIS, math.pi)


@dataclass
class PoseImport:
    """A pose read from a file, and what the mapping made of it."""

    pose: Pose
    scheme: str
    applied: tuple
    missing: tuple
    facing_flipped: bool
    source: dict

    def describe(self, figure_name):
        text = "Took {} joints onto {} ({}).".format(len(self.applied), figure_name,
                                                     self.scheme)
        if self.missing:
            text += " {} joints stayed at rest.".format(len(self.missing))
        if self.facing_flipped:
            text += " The file faces away, so the pose was turned around."
        return text


def read_pose(data, skeleton, name="", custom_map=None):
    """A :class:`PoseImport` from `.glb`/`.vrm` bytes, applied to ``skeleton``."""
    bones, posed, source = read_glb_pose(data, name)
    rest = {bone.name: bone.matrix for bone in bones}
    mapping, scheme, _ = map_bones(rest.keys(), custom_map)
    if "hips" not in mapping:
        raise FigureImportError(
            "KSP could not find the hips bone, so it cannot tell which bone is which. "
            "Name the bones as Rigify, Mixamo or VRM do, or put a .ksp-map.json file "
            "beside the model.")
    flipped = _facing({ksp: _translation(rest[bone]) for ksp, bone in mapping.items()}, ()) == -1

    pose = skeleton.rest_pose()
    at_rest = skeleton.transforms(pose)
    applied, missing = [], []
    for index, joint in enumerate(skeleton.joints):  # Parents first, so children follow.
        bone = mapping.get(joint.name)
        if bone is None or bone not in posed:
            missing.append(joint.name)
            continue
        turn = _delta(rest[bone], posed[bone], joint.name, flipped)
        target = (turn * at_rest[index].rotation).normalized()
        pose = skeleton.set_rotation(pose, index, skeleton.local_rotation_for(
            pose, index, target))
        applied.append(joint.name)
    pose = replace(pose, root_translation=_root_shift(rest, posed, mapping, skeleton,
                                                      at_rest, flipped))
    return PoseImport(pose=pose, scheme=scheme, applied=tuple(applied),
                      missing=tuple(missing), facing_flipped=flipped, source=source)


def read_pose_map(text):
    """A `.ksp-map.json` file, the same one figure import reads."""
    return read_map_file(text)


def _delta(rest_matrix, posed_matrix, name, flipped):
    """How far this bone turned from its bind pose, in KSP's axes."""
    try:
        turn = (_rotation(posed_matrix) * _rotation(rest_matrix).inverse()).normalized()
    except (ValueError, ZeroDivisionError, OverflowError) as error:
        raise FigureImportError(
            "Bone {!r} has a transform KSP cannot read.".format(name)) from error
    if flipped:
        turn = (HALF_TURN * turn * HALF_TURN.inverse()).normalized()
    return turn


def _root_shift(rest, posed, mapping, skeleton, at_rest, flipped):
    """Where the hips moved to, scaled from the file's rig to this figure's size."""
    bone = mapping["hips"]
    if bone not in posed:
        return Vec3(0.0, 0.0, 0.0)
    source_rest = _translation(rest[bone])
    shift = _translation(posed[bone]) - source_rest
    if flipped:
        shift = HALF_TURN.rotate(shift)
    try:
        hips = at_rest[skeleton.index("hips")].position
    except KeyError:
        return shift
    # Hip height stands in for the figure's size: a short rig's step is a short step.
    if source_rest.y > 1e-6 and math.isfinite(source_rest.y):
        shift = shift * (hips.y / source_rest.y)
    if not all(math.isfinite(value) for value in (shift.x, shift.y, shift.z)):
        raise FigureImportError("The file's hips move to a position KSP cannot use.")
    return shift


__all__ = ["PoseImport", "read_pose", "read_pose_map", "FigureImportError"]
