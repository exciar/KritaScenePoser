"""Joint hierarchy: forward kinematics, IK application, and mirroring.

A ``Skeleton`` is the immutable rest rig; a ``Pose`` is the authored state
(per-joint local rotations on top of the rest pose, plus the root transform).
Joints are ordered so that every parent precedes its children.
"""

from dataclasses import dataclass, replace
from typing import NamedTuple

from .ik import solve_two_bone
from .math3d import IDENTITY, ZERO, Mat4, Quat, Vec3

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
        return pose.with_rotation(index, self.local_rotation_for(pose, index, target, transforms))

    def solve_ik(self, pose, end, target, pole=None, keep_end_rotation=True):
        """Move joint ``end`` (a wrist or ankle) toward ``target`` with two-bone IK.

        Rotates the end's parent and grandparent. Returns ``(pose, reached)``.
        By default the end keeps its world rotation, so a planted foot stays flat.
        """
        end_index = self.index(end) if isinstance(end, str) else end
        middle_index = self.joints[end_index].parent
        upper_index = self.joints[middle_index].parent if middle_index >= 0 else -1
        if upper_index < 0:
            raise ValueError("Two-bone IK needs a joint with a parent and grandparent.")
        before = self.transforms(pose)
        root = before[upper_index].position
        solution = solve_two_bone(root, before[middle_index].position,
                                  before[end_index].position, target, pole)
        pose = self.rotate_world(pose, upper_index, Quat.between(
            before[middle_index].position - root, solution.middle - root))
        after = self.transforms(pose)
        middle = after[middle_index].position
        pose = self.rotate_world(pose, middle_index, Quat.between(
            after[end_index].position - middle, solution.end - middle))
        if keep_end_rotation:
            pose = pose.with_rotation(end_index, self.local_rotation_for(
                pose, end_index, before[end_index].rotation))
        return pose, solution.reached

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
                rotations[index] = (joint.rotation.inverse() * parent_rotation.inverse()
                                    * rotation).normalized()
            world.append(parent_rotation * joint.rotation * rotations[index])
        return replace(result, rotations=tuple(rotations))
