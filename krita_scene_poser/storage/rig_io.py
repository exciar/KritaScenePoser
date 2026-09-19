"""Versioned JSON rig format for posable figures (``.rig.json``).

Joints are listed parents-first with their *world* rest transforms, which keeps
the file readable and independent of how the source tool parented bones:

    {"format": "ksp-rig", "version": 1, "figure": "body_kun",
     "display_name": "Body-kun", "units": "meters", "up": "+Y", "front": "+Z",
     "source": {...provenance...},
     "joints": [{"name": "hips", "parent": null, "position": [x, y, z],
                 "rotation": [w, x, y, z], "tail": [x, y, z]}, ...]}

Joint local axes follow Blender's bone convention: +Y points along the bone.
Unknown keys are ignored so optional fields can be added within a version.
"""

from dataclasses import dataclass
import json
import math

from ..core.math3d import Quat, Vec3
from ..core.skeleton import Joint, Skeleton

FORMAT = "ksp-rig"
VERSION = 1


class RigFormatError(ValueError):
    """The rig file is damaged, from a newer KSP, or inconsistent."""


@dataclass(frozen=True)
class RigJoint:
    name: str
    parent: object  # str or None
    position: Vec3  # World rest position.
    rotation: Quat  # World rest orientation; local +Y runs along the bone.
    tail: Vec3  # World position of the bone's far end.


@dataclass(frozen=True)
class RigData:
    figure: str
    display_name: str
    joints: tuple  # RigJoint, parents first
    source: dict
    skeleton: Skeleton

    def tails(self):
        return tuple(joint.tail for joint in self.joints)


def build_skeleton(joints):
    """Skeleton whose rest pose reproduces the given world rest transforms."""
    index, world, result = {}, {}, []
    for joint in joints:
        if joint.parent is None:
            result.append(Joint(joint.name, -1, joint.position, joint.rotation))
        else:
            parent_position, parent_rotation = world[joint.parent]
            inverse = parent_rotation.inverse()
            result.append(Joint(joint.name, index[joint.parent],
                                inverse.rotate(joint.position - parent_position),
                                (inverse * joint.rotation).normalized()))
        index[joint.name] = len(result) - 1
        world[joint.name] = (joint.position, joint.rotation)
    return Skeleton(result)


def _vector(value, size, label):
    if (not isinstance(value, list) or len(value) != size
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in value)):
        raise RigFormatError("{} must be {} finite numbers.".format(label, size))
    return value


def read_rig(text):
    try:
        data = json.loads(text)
    except ValueError as error:
        raise RigFormatError("The rig file is not valid JSON.") from error
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise RigFormatError("Not a KSP rig file.")
    if data.get("version") != VERSION:
        raise RigFormatError("Unsupported rig version {!r} (this KSP reads {}).".format(
            data.get("version"), VERSION))
    entries = data.get("joints")
    if not isinstance(entries, list) or not entries:
        raise RigFormatError("The rig has no joints.")
    joints, seen = [], set()
    for number, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RigFormatError("Joint {} is not an object.".format(number))
        name, parent = entry.get("name"), entry.get("parent")
        if not isinstance(name, str) or not name or name in seen:
            raise RigFormatError("Joint {} needs a unique name.".format(number))
        if (parent is None) != (number == 0) or (parent is not None and parent not in seen):
            raise RigFormatError(
                "Joint {!r}: only the first joint is the root; parents come first.".format(name))
        label = "Joint {!r}".format(name)
        rotation = Quat(*_vector(entry.get("rotation"), 4, label + " rotation"))
        if abs(rotation.length() - 1.0) > 1e-3:
            raise RigFormatError(label + " rotation is not a unit quaternion.")
        joints.append(RigJoint(
            name, parent, Vec3(*_vector(entry.get("position"), 3, label + " position")),
            rotation.normalized(), Vec3(*_vector(entry.get("tail"), 3, label + " tail"))))
        seen.add(name)
    figure, display = data.get("figure"), data.get("display_name")
    if not isinstance(figure, str) or not figure or not isinstance(display, str) or not display:
        raise RigFormatError("The rig needs a figure id and a display name.")
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    try:
        skeleton = build_skeleton(joints)
    except ValueError as error:
        raise RigFormatError(str(error)) from error
    return RigData(figure, display, tuple(joints), source, skeleton)


def write_rig(figure, display_name, joints, source):
    """Deterministic JSON text; coordinates rounded to 1 micrometer."""
    def rounded(values, places):
        return [round(v, places) + 0.0 for v in values]  # + 0.0 drops -0.0

    document = {
        "format": FORMAT, "version": VERSION, "figure": figure, "display_name": display_name,
        "units": "meters", "up": "+Y", "front": "+Z", "source": source,
        "joints": [{
            "name": j.name, "parent": j.parent, "position": rounded(j.position, 6),
            "rotation": rounded(j.rotation, 8), "tail": rounded(j.tail, 6),
        } for j in joints],
    }
    text = json.dumps(document, indent=1, sort_keys=True) + "\n"
    read_rig(text)  # Never write a file this KSP could not read back.
    return text
