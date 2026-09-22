"""Joint limits: a twist range about the bone and swing ranges about local X and Z.

Hinge directions are measured from the rest pose, because rigs disagree
about which way a bone's local X points.
"""

import math

from .math3d import IDENTITY, Quat, Vec3, X_AXIS, Y_AXIS

EPSILON = 1e-9


class JointLimit:
    """Allowed rotation of one joint, in degrees, in its rest frame."""

    __slots__ = ("swing_x", "swing_z", "twist")

    def __init__(self, swing_x=(-90.0, 90.0), swing_z=(-90.0, 90.0), twist=(-90.0, 90.0)):
        self.swing_x = _range(swing_x)
        self.swing_z = _range(swing_z)
        self.twist = _range(twist)

    def __eq__(self, other):
        return (isinstance(other, JointLimit) and self.swing_x == other.swing_x
                and self.swing_z == other.swing_z and self.twist == other.twist)

    def __repr__(self):
        return "JointLimit(swing_x={}, swing_z={}, twist={})".format(
            self.swing_x, self.swing_z, self.twist)

    def flipped(self):
        return JointLimit((-self.swing_x[1], -self.swing_x[0]), self.swing_z, self.twist)


def _range(pair):
    low, high = (float(pair[0]), float(pair[1]))
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("Joint limits must be finite angles.")
    low, high = max(-180.0, min(180.0, low)), max(-180.0, min(180.0, high))
    return (min(low, high), max(low, high))


def cone(swing, twist):
    return JointLimit((-swing, swing), (-swing, swing), (-twist, twist))


HYPEREXTENSION = 5.0  # Degrees past straight; elbows and knees do give a little.


def hinge(flexion, lateral=5.0, twist=10.0):
    """A one-way joint that bends up to ``flexion`` degrees about local +X.

    The straightening side is filled in by :func:`limits_for` from the rest
    bend, because a rigged elbow rests slightly bent and must still be able to
    straighten.
    """
    return JointLimit((0.0, flexion), (-lateral, lateral), (-twist, twist))


# Knees and elbows are the joints that look wrong when they invert, so they are
# hinges. Everything else is a permissive cone that only stops extreme poses.
DEFAULT_LIMITS = {
    "hips": None,  # The root carries the whole figure; it is never clamped.
    "waist": cone(25.0, 30.0),
    "torso": cone(25.0, 30.0),
    "chest": cone(25.0, 25.0),
    "neck": cone(35.0, 45.0),
    "head": cone(45.0, 70.0),
    "shoulder": cone(30.0, 20.0),
    "upper_arm": cone(110.0, 90.0),
    # Lateral play is what lets hand-drag IK still reach; swing_x is what stops
    # an elbow or knee inverting. Measured: +-10 deg costs little anatomically
    # and leaves 16 of 240 sampled IK targets short instead of 43.
    "forearm": hinge(150.0, lateral=10.0, twist=85.0),  # Twist is the forearm's own.
    "hand": cone(70.0, 25.0),
    "thigh": cone(95.0, 45.0),
    "shin": hinge(155.0, lateral=8.0, twist=20.0),
    "foot": cone(45.0, 20.0),
    "toe": cone(45.0, 5.0),
    "thumb": cone(60.0, 20.0),
    "index": cone(90.0, 15.0),
    "middle": cone(90.0, 15.0),
    "ring": cone(90.0, 15.0),
    "pinky": cone(90.0, 15.0),
}
HINGES = frozenset(name for name, limit in DEFAULT_LIMITS.items()
                   if limit is not None and limit.swing_x[0] >= 0.0)


def is_hinge(limit, span=30.0):
    """True when the limit lets the bone bend in one plane only, like an elbow."""
    return limit is not None and (limit.swing_z[1] - limit.swing_z[0]) < span


def limit_name(joint_name):
    """The lookup key for a joint: side suffixes and finger segments removed."""
    name = joint_name
    for suffix in (".L", ".R", "_L", "_R", ".l", ".r", "_l", "_r"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    head = name.split(".")[0]  # "index.01" -> "index"
    return head


def default_limit(joint_name):
    return DEFAULT_LIMITS.get(limit_name(joint_name))


def limits_for(skeleton):
    """Per-joint limits for ``skeleton``, with hinge directions from its rest pose."""
    children = {}
    for index, joint in enumerate(skeleton.joints):
        children.setdefault(joint.parent, []).append(index)
    result = []
    for index, joint in enumerate(skeleton.joints):
        limit = default_limit(joint.name)
        if limit is not None and limit_name(joint.name) in HINGES:
            positive, bend = _rest_bend(skeleton, children, index)
            # Straightening is allowed back to straight, plus a little give.
            limit = JointLimit((-(bend + HYPEREXTENSION), limit.swing_x[1]),
                               limit.swing_z, limit.twist)
            limit = limit if positive else limit.flipped()
        result.append(limit)
    return tuple(result)


def _rest_bend(skeleton, children, index):
    """``(bends toward local +X, rest bend in degrees)`` for a hinge joint.

    A rigged elbow or knee carries a few degrees of bend so solvers know which
    way it folds. ``cross(parent bone, this bone)`` points along that hinge.
    """
    joint = skeleton.joints[index]
    kids = children.get(index)
    if joint.parent < 0 or not kids:
        return True, 0.0
    rest = skeleton.rest_transforms
    parent_bone = rest[index].position - rest[joint.parent].position
    bone = rest[kids[0]].position - rest[index].position
    axis = parent_bone.cross(bone)
    if axis.length() < 1e-4:  # A perfectly straight limb: keep the default.
        return True, 0.0
    lengths = parent_bone.length() * bone.length()
    cosine = min(1.0, max(-1.0, parent_bone.dot(bone) / lengths)) if lengths > 0 else 1.0
    bend = math.degrees(math.acos(cosine))
    return axis.dot(rest[index].rotation.rotate(X_AXIS)) > 0.0, bend


def swing_twist(rotation):
    """Split a rotation into ``(swing, twist)`` about the bone axis (+Y).

    ``rotation == swing * twist``; the twist turns about +Y, and the swing
    tilts +Y away from itself.
    """
    q = rotation.normalized()
    if q.w < 0.0:  # Same rotation, shortest path, so angles stay in [-180, 180].
        q = Quat(-q.w, -q.x, -q.y, -q.z)
    length = math.hypot(q.w, q.y)
    twist = IDENTITY if length < EPSILON else Quat(q.w / length, 0.0, q.y / length, 0.0)
    return (q * twist.inverse()).normalized(), twist


def clamp_rotation(limit, rotation):
    """``rotation`` reduced to what ``limit`` allows. ``None`` allows everything."""
    if limit is None:
        return rotation
    swing, twist = swing_twist(rotation)
    x, z = _swing_vector(swing)
    clamped_x = min(max(x, math.radians(limit.swing_x[0])), math.radians(limit.swing_x[1]))
    clamped_z = min(max(z, math.radians(limit.swing_z[0])), math.radians(limit.swing_z[1]))
    angle = 2.0 * math.atan2(twist.y, twist.w)
    clamped_twist = min(max(angle, math.radians(limit.twist[0])), math.radians(limit.twist[1]))
    if (clamped_x, clamped_z, clamped_twist) == (x, z, angle):
        return rotation
    return (_swing_rotation(clamped_x, clamped_z)
            * Quat.from_axis_angle(Y_AXIS, clamped_twist)).normalized()


def is_limited(limit, rotation, tolerance=1e-6):
    if limit is None:
        return False
    return not clamp_rotation(limit, rotation).is_close(rotation.normalized(), tolerance)


def at_boundary(limit, rotation, tolerance=1.0):
    """True when the rotation rests against a stop, within ``tolerance`` degrees.

    A clamped rotation sits exactly on its limit, so this is what tells the
    user a joint stopped because of its limit rather than their gesture.
    """
    if limit is None:
        return False
    swing, twist = swing_twist(rotation)
    x, z = _swing_vector(swing)
    angle = 2.0 * math.atan2(twist.y, twist.w)
    margin = math.radians(tolerance)
    for value, (low, high) in ((x, limit.swing_x), (z, limit.swing_z), (angle, limit.twist)):
        if abs(value - math.radians(low)) <= margin or abs(value - math.radians(high)) <= margin:
            return True
    return False


def _swing_vector(swing):
    """The swing as (x, z) radians: its axis-angle vector, which has no Y part."""
    w = min(1.0, max(-1.0, swing.w))
    angle = 2.0 * math.acos(w)
    sine = math.sqrt(max(0.0, 1.0 - w * w))
    if sine < EPSILON:
        return 0.0, 0.0
    return angle * swing.x / sine, angle * swing.z / sine


def _swing_rotation(x, z):
    angle = math.hypot(x, z)
    if angle < EPSILON:
        return IDENTITY
    return Quat.from_axis_angle(Vec3(x / angle, 0.0, z / angle), angle)
