"""Forward kinematics, skinning, IK application, and mirroring."""

import math
import random
import unittest

from krita_scene_poser.core.math3d import (
    IDENTITY, X_AXIS, Y_AXIS, Z_AXIS, Mat4, Quat, Vec3,
)
from krita_scene_poser.core.skeleton import (
    Joint, Pose, Skeleton, mirror_name, mirror_vector,
)


def build(spec):
    """Skeleton from (name, parent, world position, world rotation) at rest."""
    joints, world, index = [], {}, {}
    for name, parent, position, rotation in spec:
        if parent is None:
            joints.append(Joint(name, -1, position, rotation))
        else:
            parent_position, parent_rotation = world[parent]
            joints.append(Joint(
                name, index[parent],
                parent_rotation.inverse().rotate(position - parent_position),
                parent_rotation.inverse() * rotation))
        world[name], index[name] = (position, rotation), len(joints) - 1
    return Skeleton(joints)


def figure_spec():
    """Symmetric rest pose; the left side uses arbitrary local axes, the right identity."""
    left = [
        ("shoulder", "chest", Vec3(0.15, 1.5, 0), Quat.from_axis_angle(Z_AXIS, -0.3)),
        ("upper_arm", "shoulder", Vec3(0.3, 1.45, 0), Quat.from_axis_angle(Vec3(1, 1, 0), 0.7)),
        ("forearm", "upper_arm", Vec3(0.6, 1.4, 0.02), Quat.from_axis_angle(Y_AXIS, 0.4)),
        ("hand", "forearm", Vec3(0.88, 1.38, 0.0), Quat.from_axis_angle(X_AXIS, 1.0)),
        ("thigh", "hips", Vec3(0.1, 0.95, 0), Quat.from_axis_angle(Z_AXIS, 3.0)),
        ("shin", "thigh", Vec3(0.1, 0.5, 0.03), Quat.from_axis_angle(X_AXIS, -0.2)),
        ("foot", "shin", Vec3(0.1, 0.08, 0), IDENTITY),
    ]
    spec = [("hips", None, Vec3(0, 1.0, 0), IDENTITY),
            ("spine", "hips", Vec3(0, 1.3, 0), IDENTITY),
            ("chest", "spine", Vec3(0, 1.5, 0), IDENTITY)]
    for side, flip, axes in ((".L", False, True), (".R", True, False)):
        for name, parent, position, rotation in left:
            parent_name = parent + side if parent not in ("chest", "hips") else parent
            spec.append((name + side, parent_name,
                         mirror_vector(position) if flip else position,
                         rotation if axes else IDENTITY))
    return spec


def random_rotation(rng, scale=1.0):
    axis = Vec3(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1) + 1.5)
    return Quat.from_axis_angle(axis, rng.uniform(-1, 1) * scale)


class SkeletonTests(unittest.TestCase):
    def setUp(self):
        self.spec = figure_spec()
        self.skeleton = build(self.spec)

    def positions(self, pose):
        return [t.position for t in self.skeleton.transforms(pose)]

    def test_invalid_hierarchies_are_rejected(self):
        a, b = Joint("a", -1, Vec3(0, 0, 0)), Joint("b", 0, Vec3(1, 0, 0))
        for joints in ([], [a, Joint("a", 0, Vec3(1, 0, 0))], [b, a], [a, Joint("c", -1, Vec3(0, 0, 0))],
                       [a, Joint("c", -2, Vec3(0, 0, 0))], [a, Joint("c", 1, Vec3(0, 0, 0))]):
            with self.subTest(names=[j.name for j in joints]), self.assertRaises(ValueError):
                Skeleton(joints)
        with self.assertRaisesRegex(KeyError, "Unknown joint"):
            Skeleton([a, b]).index("elbow")

    def test_rest_pose_reproduces_the_authored_rig(self):
        for transform, (name, _, position, rotation) in zip(
                self.skeleton.transforms(self.skeleton.rest_pose()), self.spec):
            with self.subTest(joint=name):
                self.assertTrue(transform.position.is_close(position, 1e-12))
                self.assertTrue(transform.rotation.is_close(rotation, 1e-12))

    def test_chain_rotation_and_root_transform(self):
        chain = Skeleton([Joint("a", -1, Vec3(0, 0, 0)), Joint("b", 0, Vec3(1, 0, 0)),
                          Joint("c", 1, Vec3(1, 0, 0))])
        pose = chain.rest_pose().with_rotation(0, Quat.from_axis_angle(Z_AXIS, math.pi / 2))
        pose = pose.with_rotation(1, Quat.from_axis_angle(Z_AXIS, math.pi / 2))
        self.assertTrue(chain.transforms(pose)[1].position.is_close(Vec3(0, 1, 0)))
        self.assertTrue(chain.transforms(pose)[2].position.is_close(Vec3(-1, 1, 0)))
        root_rotation = Quat.from_axis_angle(Y_AXIS, 0.8)
        moved = Pose(pose.rotations, Vec3(5, 0, 2), root_rotation, 2.0)
        for original, placed in zip(chain.transforms(pose), chain.transforms(moved)):
            expected = Vec3(5, 0, 2) + root_rotation.rotate(original.position * 2.0)
            self.assertTrue(placed.position.is_close(expected))
        with self.assertRaises(ValueError):
            chain.transforms(Pose((IDENTITY,)))

    def test_skinning_is_identity_at_rest_and_follows_joints_when_posed(self):
        for matrix in self.skeleton.skinning_matrices(self.skeleton.rest_pose()):
            self.assertTrue(matrix.is_close(Mat4.identity(), 1e-12))
        forearm = self.skeleton.index("forearm.L")
        pose = self.skeleton.rotate_world(self.skeleton.rest_pose(), forearm,
                                          Quat.from_axis_angle(Z_AXIS, 1.2))
        vertex = Vec3(0.7, 1.42, 0.05)  # Rest-pose vertex on the forearm.
        local = self.skeleton.inverse_bind[forearm].transform_point(vertex)
        posed = self.skeleton.transforms(pose)[forearm]
        expected = posed.position + posed.rotation.rotate(local)
        skinned = self.skeleton.skinning_matrices(pose)[forearm].transform_point(vertex)
        self.assertTrue(skinned.is_close(expected, 1e-12))

    def test_world_rotation_pivots_children_about_the_joint(self):
        upper = self.skeleton.index("upper_arm.L")
        hand = self.skeleton.index("hand.L")
        delta = Quat.from_axis_angle(Vec3(0.3, 1, 0.2), 0.9)
        rest = self.positions(self.skeleton.rest_pose())
        posed = self.positions(self.skeleton.rotate_world(self.skeleton.rest_pose(), upper, delta))
        expected = rest[upper] + delta.rotate(rest[hand] - rest[upper])
        self.assertTrue(posed[hand].is_close(expected, 1e-12))
        self.assertTrue(posed[upper].is_close(rest[upper], 1e-12))

    def test_ik_reaches_target_keeps_lengths_and_hand_orientation(self):
        rng = random.Random(4)
        pose = self.skeleton.rest_pose()
        indices = [self.skeleton.index(n) for n in ("upper_arm.L", "forearm.L", "hand.L")]
        rest = self.positions(pose)
        lengths = [(rest[indices[1]] - rest[indices[0]]).length(),
                   (rest[indices[2]] - rest[indices[1]]).length()]
        for _ in range(25):
            target = rest[indices[0]] + Vec3(rng.uniform(0.1, 0.4), rng.uniform(-0.4, 0.2),
                                             rng.uniform(-0.3, 0.3))
            before = self.skeleton.transforms(pose)[indices[2]].rotation
            pose, reached = self.skeleton.solve_ik(pose, "hand.L", target, Vec3(0.5, 1.4, -1))
            self.assertTrue(reached)
            after = self.skeleton.transforms(pose)
            self.assertTrue(after[indices[2]].position.is_close(target, 1e-9))
            self.assertTrue(after[indices[2]].rotation.is_close(before, 1e-9))
            self.assertAlmostEqual((after[indices[1]].position - after[indices[0]].position).length(),
                                   lengths[0], places=9)
            self.assertAlmostEqual((after[indices[2]].position - after[indices[1]].position).length(),
                                   lengths[1], places=9)

    def test_ik_reports_unreachable_targets_and_needs_two_parents(self):
        pose, reached = self.skeleton.solve_ik(self.skeleton.rest_pose(), "foot.R", Vec3(-0.1, -3, 0))
        self.assertFalse(reached)
        thigh, foot = self.skeleton.index("thigh.R"), self.skeleton.index("foot.R")
        positions = self.positions(pose)
        direction = (positions[foot] - positions[thigh]).normalized()
        self.assertTrue(direction.is_close((Vec3(-0.1, -3, 0) - positions[thigh]).normalized(), 1e-3))
        with self.assertRaises(ValueError):
            self.skeleton.solve_ik(self.skeleton.rest_pose(), "spine", Vec3(0, 2, 0))

    def test_mirror_whole_pose_reflects_every_joint_and_is_an_involution(self):
        rng = random.Random(9)
        pose = self.skeleton.rest_pose()
        for name in ("spine", "upper_arm.L", "forearm.L", "hand.L", "thigh.R", "shin.R", "shoulder.R"):
            pose = pose.with_rotation(self.skeleton.index(name), random_rotation(rng))
        pose = Pose(pose.rotations, Vec3(0.3, 0.1, -0.2), random_rotation(rng, 0.5), 1.0)
        original = self.positions(pose)
        mirrored_pose = self.skeleton.mirror_pose(pose)
        mirrored = self.positions(mirrored_pose)
        for index, counterpart in enumerate(self.skeleton.mirror_indices):
            with self.subTest(joint=self.skeleton.joints[index].name):
                self.assertTrue(mirrored[index].is_close(mirror_vector(original[counterpart]), 1e-9))
        twice = self.positions(self.skeleton.mirror_pose(mirrored_pose))
        for before, after in zip(original, twice):
            self.assertTrue(before.is_close(after, 1e-9))

    def test_mirror_limb_copies_one_arm_onto_the_other(self):
        rng = random.Random(12)
        pose = self.skeleton.rest_pose()
        for name in ("shoulder.L", "upper_arm.L", "forearm.L", "hand.L", "thigh.L"):
            pose = pose.with_rotation(self.skeleton.index(name), random_rotation(rng))
        right_arm = [self.skeleton.index(n) for n in ("shoulder.R", "upper_arm.R", "forearm.R", "hand.R")]
        original = self.positions(pose)
        copied = self.positions(self.skeleton.mirror_pose(pose, right_arm))
        for index, position in enumerate(copied):
            if index in right_arm:
                counterpart = self.skeleton.mirror_indices[index]
                self.assertTrue(position.is_close(mirror_vector(original[counterpart]), 1e-9))
            else:
                self.assertTrue(position.is_close(original[index], 1e-12))

    def test_mirror_names(self):
        for name, expected in (("hand.L", "hand.R"), ("hand.R", "hand.L"), ("thigh_L", "thigh_R"),
                               ("foot.r", "foot.l"), ("spine", "spine")):
            self.assertEqual(mirror_name(name), expected)
        self.assertEqual(self.skeleton.mirror_indices[self.skeleton.index("spine")],
                         self.skeleton.index("spine"))


if __name__ == "__main__":
    unittest.main()
