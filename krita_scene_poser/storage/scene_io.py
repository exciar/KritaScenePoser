"""Pose (ksp-pose v1) and scene (ksp-scene v1) files.

    {"format": "ksp-pose", "version": 1, "figure": "body_kun",
     "rotations": {"upper_arm.L": [w, x, y, z], ...},
     "root": {"translation": [x, y, z], "rotation": [w, x, y, z]}}

Rotations are keyed by joint name, so a pose moves between figures. A scene adds the
camera, view mode, line settings, opacities, output size and body shape. Unknown keys
are ignored within a version.
"""

import json

from ..core.camera import OrbitCamera
from ..core.lineart import LineArtSettings
from ..core.math3d import IDENTITY, ZERO, Quat, Vec3
from ..core.output import OutputSettings
from ..core.shape import BodyShape

POSE_FORMAT, SCENE_FORMAT, VERSION = "ksp-pose", "ksp-scene", 1
MODES = ("shaded", "lines", "both")
POSITION_PLACES, ROTATION_PLACES = 6, 8


class SceneFormatError(ValueError):
    """The pose or scene file is damaged, or from a newer KSP."""


class Applied:
    """What a loaded pose did: the joints it set, and the names it could not."""

    def __init__(self, pose, applied=(), unknown=()):
        self.pose = pose
        self.applied = tuple(applied)
        self.unknown = tuple(unknown)

    def describe(self, figure_name="the figure"):
        text = "Applied {} joints to {}.".format(len(self.applied), figure_name)
        if self.unknown:
            text += " {} joints in the file are not in this figure: {}.".format(
                len(self.unknown), ", ".join(sorted(self.unknown)[:4]))
        return text


def _rounded(values, places):
    return [round(float(value), places) + 0.0 for value in values]  # + 0.0 drops -0.0.


def _numbers(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise SceneFormatError("{} must be {} numbers.".format(label, size))
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise SceneFormatError("{} must be {} numbers.".format(label, size))
        number = float(item)
        if number != number or number in (float("inf"), float("-inf")):
            raise SceneFormatError("{} must be finite.".format(label))
        result.append(number)
    return result


def _quaternion(value, label):
    quaternion = Quat(*_numbers(value, 4, label))
    if abs(quaternion.length() - 1.0) > 1e-3:
        raise SceneFormatError("{} is not a unit quaternion.".format(label))
    return quaternion.normalized()


def _document(text, expected):
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise SceneFormatError("The file is not valid JSON.") from error
    if not isinstance(data, dict) or data.get("format") != expected:
        raise SceneFormatError("Not a KSP {} file.".format(
            "pose" if expected == POSE_FORMAT else "scene"))
    if data.get("version") != VERSION:
        raise SceneFormatError("Unsupported {} version {!r} (this KSP reads {}).".format(
            expected, data.get("version"), VERSION))
    return data


# Poses ----------------------------------------------------------------------


def pose_document(skeleton, pose, figure=""):
    rotations = {}
    for joint, rotation in zip(skeleton.joints, pose.rotations):
        if not rotation.is_close(IDENTITY, 1e-9):  # Rest joints need no entry.
            rotations[joint.name] = _rounded(rotation, ROTATION_PLACES)
    return {
        "format": POSE_FORMAT, "version": VERSION, "figure": figure,
        "rotations": rotations,
        "root": {"translation": _rounded(pose.root_translation, POSITION_PLACES),
                 "rotation": _rounded(pose.root_rotation, ROTATION_PLACES)},
    }


def write_pose(skeleton, pose, figure=""):
    text = json.dumps(pose_document(skeleton, pose, figure), indent=1, sort_keys=True) + "\n"
    read_pose(text, skeleton)  # Never write a file this KSP could not read back.
    return text


def read_pose(text, skeleton):
    """Apply a stored pose to ``skeleton``; returns an :class:`Applied`."""
    return apply_pose(_document(text, POSE_FORMAT), skeleton)


def apply_pose(data, skeleton):
    """Apply an already-parsed pose document to ``skeleton``."""
    rotations = data.get("rotations")
    if not isinstance(rotations, dict):
        raise SceneFormatError("The pose has no rotations.")
    pose = skeleton.rest_pose()
    applied, unknown = [], []
    for name in sorted(rotations):
        if not isinstance(name, str):
            raise SceneFormatError("Joint names must be text.")
        rotation = _quaternion(rotations[name], "Joint {!r} rotation".format(name))
        try:
            index = skeleton.index(name)
        except KeyError:
            unknown.append(name)
            continue
        # Limits apply to a loaded pose exactly as they do to a drag.
        pose = skeleton.set_rotation(pose, index, rotation)
        applied.append(name)
    root = data.get("root")
    if isinstance(root, dict):
        translation = root.get("translation")
        rotation = root.get("rotation")
        pose = type(pose)(
            pose.rotations,
            Vec3(*_numbers(translation, 3, "Root translation")) if translation is not None else ZERO,
            _quaternion(rotation, "Root rotation") if rotation is not None else IDENTITY,
            pose.root_scale)
    return Applied(pose, applied, unknown)


# Scenes ---------------------------------------------------------------------


def write_scene(skeleton, pose, *, figure="", camera=None, mode="shaded", lines=None,
                opacity=1.0, layer_opacity=1.0, output=None, shape=None):
    """A full scene: the pose plus everything else the docker is showing."""
    document = pose_document(skeleton, pose, figure)
    document["format"] = SCENE_FORMAT
    document["camera"] = (camera or OrbitCamera()).to_dict()
    document["display"] = mode if mode in MODES else "shaded"
    document["lines"] = json.loads((lines or LineArtSettings()).to_json())
    document["opacity"] = _clamped(opacity)
    document["layer_opacity"] = _clamped(layer_opacity)
    document["output"] = json.loads((output or OutputSettings()).to_json())
    document["shape"] = json.loads((shape or BodyShape()).to_json())
    text = json.dumps(document, indent=1, sort_keys=True) + "\n"
    read_scene(text, skeleton)
    return text


class Scene:
    """A loaded scene. Every field falls back to a default if the file lacks it."""

    def __init__(self, applied, figure="", camera=None, mode="shaded", lines=None,
                 opacity=1.0, layer_opacity=1.0, output=None, shape=None):
        self.applied = applied
        self.pose = applied.pose
        self.figure = figure
        self.camera = camera or OrbitCamera()
        self.mode = mode
        self.lines = lines or LineArtSettings()
        self.opacity = opacity
        self.layer_opacity = layer_opacity
        self.output = output or OutputSettings()
        self.shape = shape or BodyShape()


def read_scene(text, skeleton):
    data = _document(text, SCENE_FORMAT)
    mode = data.get("display")
    return Scene(
        apply_pose(data, skeleton),
        figure=data.get("figure") if isinstance(data.get("figure"), str) else "",
        camera=OrbitCamera.from_dict(data.get("camera")),
        mode=mode if mode in MODES else "shaded",
        lines=LineArtSettings.from_json(json.dumps(data.get("lines"))),
        opacity=_clamped(data.get("opacity", 1.0)),
        layer_opacity=_clamped(data.get("layer_opacity", 1.0)),
        output=OutputSettings.from_json(json.dumps(data.get("output"))),
        shape=BodyShape.from_json(json.dumps(data.get("shape"))))


def _clamped(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 1.0
    return min(1.0, max(0.0, float(value)))
