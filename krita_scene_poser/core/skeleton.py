"""Joint hierarchy: forward kinematics, IK application, and mirroring.

A ``Skeleton`` is the immutable rest rig; a ``Pose`` is the authored state
(per-joint local rotations on top of the rest pose, plus the root transform).
Joints are ordered so that every parent precedes its children.
"""

from dataclasses import dataclass, replace
from typing import NamedTuple

from .ik import solve_two_bone
from .limits import at_boundary, clamp_rotation, is_hinge, limits_for
from .math3d import IDENTITY, X_AXIS, ZERO, Mat4, Quat, Vec3

MIRROR_SUFFIXES = ((".L", ".R"), ("_L", "_R"), (".l", ".r"), ("_l", "_r"))


@dataclass(frozen=True)
class Joint:
    name: str
    parent: int  # Index of the parent joint; -1 for the root.
    offset: Vec3  # Rest position relative to the parent, in the parent's frame.
    rotation: Quat = IDENTITY  # Rest orientation relative to the parent.


@dataclass(frozen=True)
class Pose:
    rotations: tuple  # One local Quat per joint, applied after the rest rotation.
    root_translation: Vec3 = ZERO
    root_rotation: Quat = IDENTITY
    root_scale: float = 1.0

    def with_rotation(self, index, rotation):
        rotations = list(self.rotations)
        rotations[index] = rotation.normalized()
        return replace(self, rotations=tuple(rotations))


class JointTransform(NamedTuple):
    position: Vec3
    rotation: Quat


def mirror_name(name):
    """Counterpart across the body's center line; unpaired names map to themselves."""
    for left, right in MIRROR_SUFFIXES:
        if name.endswith(left):
            return name[:-len(left)] + right
        if name.endswith(right):
            return name[:-len(right)] + left
    return name


def mirror_vector(v):
    """Reflect across the YZ plane (x -> -x)."""
    return Vec3(-v.x, v.y, v.z)


def mirror_rotation(q):
    """The rotation seen in a mirror across the YZ plane."""
    return Quat(q.w, q.x, -q.y, -q.z)


class Skeleton:
    def __init__(self, joints):
        self.joints = tuple(joints)
        if not self.joints:
            raise ValueError("A skeleton needs at least one joint.")
        self._indices = {}
        for index, joint in enumerate(self.joints):
            if not joint.name or joint.name in self._indices:
                raise ValueError("Joint names must be unique and non-empty: {!r}".format(joint.name))
            valid_parent = joint.parent == -1 if index == 0 else 0 <= joint.parent < index
            if not valid_parent:
                raise ValueError(
                    "Joint {!r} must follow its parent; only the first joint is the root."
                    .format(joint.name))
            self._indices[joint.name] = index
        self.rest_transforms = self.transforms(self.rest_pose())
        self.inverse_bind = tuple(
            Mat4.from_trs(t.position, t.rotation).inverse() for t in self.rest_transforms)
        self.mirror_indices = tuple(
            self._indices.get(mirror_name(joint.name), index)
            for index, joint in enumerate(self.joints))
        self.limits = limits_for(self)
        self.limits_enabled = True  # Switched off from the docker's Limits toggle.

    def __len__(self):
        return len(self.joints)

    def index(self, name):
        try:
            return self._indices[name]
        except KeyError:
            raise KeyError("Unknown joint: {}".format(name)) from None

    def rest_pose(self):
        return Pose(tuple(IDENTITY for _ in self.joints))

    def _check(self, pose):
        if len(pose.rotations) != len(self.joints):
            raise ValueError("The pose has {} rotations for {} joints.".format(
                len(pose.rotations), len(self.joints)))

    def _parent_rotation(self, pose, transforms, index):
        parent = self.joints[index].parent
        return pose.root_rotation if parent < 0 else transforms[parent].rotation

    def transforms(self, pose):
        """World position and rotation of every joint (forward kinematics)."""
        self._check(pose)
        result = []
        for joint, local in zip(self.joints, pose.rotations):
            if joint.parent < 0:
                parent = JointTransform(pose.root_translation, pose.root_rotation)
            else:
                parent = result[joint.parent]
            result.append(JointTransform(
                parent.position + parent.rotation.rotate(joint.offset) * pose.root_scale,
                parent.rotation * joint.rotation * local))
        return tuple(result)

    def world_matrices(self, pose):
        return tuple(Mat4.from_trs(t.position, t.rotation, pose.root_scale)
                     for t in self.transforms(pose))

    def skinning_matrices(self, pose):
        """Per-joint bind-space to posed-world matrices, for GPU skinning."""
        return tuple(world @ bind for world, bind in
                     zip(self.world_matrices(pose), self.inverse_bind))

    def limit_for(self, index):
        """The joint's limit, or ``None`` when limits are off or it has none."""
        return self.limits[index] if self.limits_enabled else None

    def set_rotation(self, pose, index, local):
        """Set one joint's local rotation, clamped to its limit.

        Every per-joint rotation goes through here, so a pose can never hold a
        rotation its limit forbids.
        """
        return pose.with_rotation(index, clamp_rotation(self.limit_for(index), local))

    def at_limit(self, pose, index):
        """True when the joint is resting against one of its stops."""
        return at_boundary(self.limit_for(index), pose.rotations[index])

    def local_rotation_for(self, pose, index, world_rotation, transforms=None):
        """Pose rotation that gives joint ``index`` the requested world rotation."""
        transforms = transforms or self.transforms(pose)
        parent_rotation = self._parent_rotation(pose, transforms, index)
        return (self.joints[index].rotation.inverse() * parent_rotation.inverse()
                * world_rotation).normalized()

    def rotate_world(self, pose, index, delta):
        """Apply a world-space rotation to one joint, as a gizmo drag does."""
        transforms = self.transforms(pose)
        target = delta * transforms[index].rotation
        return self.set_rotation(pose, index, self.local_rotation_for(
            pose, index, target, transforms))

    def solve_ik(self, pose, end, target, pole=None, keep_end_rotation=True, passes=4):
        """Move joint ``end`` (a wrist or ankle) toward ``target`` with two-bone IK.

        Rotates the end's parent and grandparent. Returns ``(pose, reached)``.
        By default the end keeps its world rotation, so a planted foot stays flat.

        When a joint limit holds the middle bone back, the solve repeats so the
        upper bone takes up the slack; without limits one pass is exact and the
        loop stops there.
        """
        end_index = self.index(end) if isinstance(end, str) else end
        middle_index = self.joints[end_index].parent
        upper_index = self.joints[middle_index].parent if middle_index >= 0 else -1
        if upper_index < 0:
            raise ValueError("Two-bone IK needs a joint with a parent and grandparent.")
        start = self.transforms(pose)
        hinged = is_hinge(self.limit_for(middle_index))
        best, best_error, reached = pose, None, False
        for _ in range(max(1, passes)):
            before = self.transforms(pose)
            root = before[upper_index].position
            bend_pole = self._hinge_pole(before, middle_index, root, pole) if hinged else pole
            solution = solve_two_bone(root, before[middle_index].position,
                                      before[end_index].position, target, bend_pole)
            pose = self.rotate_world(pose, upper_index, Quat.between(
                before[middle_index].position - root, solution.middle - root))
            after = self.transforms(pose)
            middle = after[middle_index].position
            pose = self.rotate_world(pose, middle_index, Quat.between(
                after[end_index].position - middle, solution.end - middle))
            # A limit may have blocked part of the rotation; check where the end landed.
            placed = self.transforms(pose)[end_index].position
            reached = solution.reached and placed.is_close(solution.end, 1e-7)
            error = (placed - solution.end).length()
            if best_error is None or error < best_error:
                best, best_error = pose, error
            if reached or not self.limits_enabled:
                break
            # Later passes let the upper bone take up what a limit refused, but
            # they can also overshoot, so the closest pass is the one kept.
            reached = False
        pose = best
        if keep_end_rotation:
            pose = self.set_rotation(pose, end_index, self.local_rotation_for(
                pose, end_index, start[end_index].rotation))
        return pose, reached

    def _hinge_pole(self, transforms, middle_index, root, pole):
        """A pole in the plane an elbow or knee can actually bend in.

        A hinge turns about its own axis, so any bend the pole asks for outside
        that plane is unreachable and would be clamped away. Dropping the part
        along the hinge axis keeps the requested bend as close as the joint can
        manage, and the upper bone's own rotation still follows the pole.
        """
        axis = transforms[middle_index].rotation.rotate(X_AXIS)
        wanted = (pole - root) if pole is not None else (transforms[middle_index].position - root)
        flat = wanted - axis * wanted.dot(axis)
        return root + flat if flat.length() > 1e-6 else pole

    def mirror_pose(self, pose, joints=None):
        """Mirror across the YZ plane, using the counterpart of each joint.

        ``joints`` lists the joint indices to overwrite, such as a right arm to
        receive the left arm's pose. ``None`` mirrors the whole pose, root
        included. Works in world space, so it needs a symmetric rest pose but
        not matching local axis conventions on both sides.
        """
        current = self.transforms(pose)
        # World-space change from rest for every joint.
        deltas = [c.rotation * r.rotation.inverse()
                  for c, r in zip(current, self.rest_transforms)]
        if joints is None:
            targets = set(range(len(self.joints)))
            result = replace(pose, root_translation=mirror_vector(pose.root_translation),
                             root_rotation=mirror_rotation(pose.root_rotation))
        else:
            targets = set(joints)
            result = pose
        rotations = list(result.rotations)
        world = []
        for index, joint in enumerate(self.joints):
            parent_rotation = result.root_rotation if joint.parent < 0 else world[joint.parent]
            if index in targets:
                source = self.mirror_indices[index]
                rotation = mirror_rotation(deltas[source]) * self.rest_transforms[index].rotation
                rotations[index] = clamp_rotation(self.limit_for(index), (
                    joint.rotation.inverse() * parent_rotation.inverse() * rotation).normalized())
            world.append(parent_rotation * joint.rotation * rotations[index])
        return replace(result, rotations=tuple(rotations))
