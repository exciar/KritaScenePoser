"""Joint limits: swing-twist clamping, hinge direction, and the shipped rigs."""

import math
import random
import unittest

from krita_scene_poser.core.limits import (
    DEFAULT_LIMITS, JointLimit, clamp_rotation, cone, default_limit, hinge, is_limited,
    limit_name, limits_for, swing_twist,
)
from krita_scene_poser.core.math3d import Quat, Vec3, X_AXIS, Y_AXIS, Z_AXIS
from krita_scene_poser.storage.figures import load_figure


def rotation(axis, degrees):
    return Quat.from_axis_angle(axis, math.radians(degrees))


class SwingTwistTests(unittest.TestCase):
    def test_decomposition_rebuilds_the_rotation(self):
        generator = random.Random(3)
        for _ in range(200):
            axis = Vec3(generator.uniform(-1, 1), generator.uniform(-1, 1),
                        generator.uniform(-1, 1))
            if axis.length() < 1e-6:
                continue
            q = Quat.from_axis_angle(axis.normalized(), generator.uniform(-3.0, 3.0))
            swing, twist = swing_twist(q)
            self.assertTrue((swing * twist).is_close(q, 1e-9))
            self.assertAlmostEqual(swing.y, 0.0, places=12)  # Swing never turns about +Y.
            self.assertAlmostEqual(twist.x, 0.0, places=12)
            self.assertAlmostEqual(twist.z, 0.0, places=12)

    def test_pure_twist_and_pure_swing_are_separated(self):
        swing, twist = swing_twist(rotation(Y_AXIS, 40.0))
        self.assertTrue(swing.is_close(Quat(1.0, 0.0, 0.0, 0.0), 1e-12))
        self.assertTrue(twist.is_close(rotation(Y_AXIS, 40.0), 1e-12))
        swing, twist = swing_twist(rotation(X_AXIS, 40.0))
        self.assertTrue(swing.is_close(rotation(X_AXIS, 40.0), 1e-12))
        self.assertTrue(twist.is_close(Quat(1.0, 0.0, 0.0, 0.0), 1e-12))


class ClampTests(unittest.TestCase):
    def test_rotations_inside_the_limit_are_untouched(self):
        limit = cone(45.0, 30.0)
        for axis, degrees in ((X_AXIS, 30.0), (Z_AXIS, -20.0), (Y_AXIS, 25.0), (X_AXIS, 0.0)):
            with self.subTest(axis=axis, degrees=degrees):
                q = rotation(axis, degrees)
                self.assertTrue(clamp_rotation(limit, q).is_close(q, 1e-9))
                self.assertFalse(is_limited(limit, q))

    def test_swing_and_twist_are_clamped_to_their_ranges(self):
        limit = JointLimit(swing_x=(-10.0, 45.0), swing_z=(-5.0, 5.0), twist=(-15.0, 15.0))
        self.assertTrue(clamp_rotation(limit, rotation(X_AXIS, 90.0)).is_close(
            rotation(X_AXIS, 45.0), 1e-9))
        self.assertTrue(clamp_rotation(limit, rotation(X_AXIS, -90.0)).is_close(
            rotation(X_AXIS, -10.0), 1e-9))
        self.assertTrue(clamp_rotation(limit, rotation(Z_AXIS, 40.0)).is_close(
            rotation(Z_AXIS, 5.0), 1e-9))
        self.assertTrue(clamp_rotation(limit, rotation(Y_AXIS, -60.0)).is_close(
            rotation(Y_AXIS, -15.0), 1e-9))
        self.assertTrue(is_limited(limit, rotation(X_AXIS, 90.0)))

    def test_clamping_is_stable_and_always_allowed(self):
        """A clamped rotation is inside the limit, so clamping it again changes nothing."""
        limit = hinge(150.0, lateral=5.0, twist=20.0)
        generator = random.Random(11)
        for _ in range(300):
            axis = Vec3(generator.uniform(-1, 1), generator.uniform(-1, 1),
                        generator.uniform(-1, 1))
            if axis.length() < 1e-6:
                continue
            q = Quat.from_axis_angle(axis.normalized(), generator.uniform(-3.0, 3.0))
            once = clamp_rotation(limit, q)
            self.assertTrue(clamp_rotation(limit, once).is_close(once, 1e-9))
            self.assertFalse(is_limited(limit, once, 1e-6))

    def test_a_hinge_never_bends_backwards(self):
        limit = hinge(150.0)
        for degrees in (-5.0, -45.0, -170.0):
            with self.subTest(degrees=degrees):
                clamped = clamp_rotation(limit, rotation(X_AXIS, degrees))
                swing, _ = swing_twist(clamped)
                self.assertGreaterEqual(swing.x, -1e-9)  # Folded the allowed way, or straight.

    def test_no_limit_allows_everything(self):
        q = rotation(X_AXIS, 179.0)
        self.assertIs(clamp_rotation(None, q), q)
        self.assertFalse(is_limited(None, q))


class NameTests(unittest.TestCase):
    def test_names_map_to_their_limits(self):
        self.assertEqual(limit_name("forearm.L"), "forearm")
        self.assertEqual(limit_name("index.02.R"), "index")
        self.assertEqual(limit_name("head"), "head")
        self.assertIsNone(default_limit("hips"))
        self.assertIsNone(default_limit("prop_bone"))  # Unknown joints stay free.
        self.assertEqual(default_limit("shin.R"), DEFAULT_LIMITS["shin"])


class ShippedFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rigs = {name: load_figure(name)[0] for name in ("body_chan", "body_kun")}

    def test_every_joint_gets_a_limit_except_the_root(self):
        for name, rig in self.rigs.items():
            with self.subTest(figure=name):
                limits = limits_for(rig.skeleton)
                self.assertEqual(len(limits), len(rig.skeleton.joints))
                self.assertIsNone(limits[rig.skeleton.index("hips")])
                self.assertTrue(all(limit is not None for index, limit in enumerate(limits)
                                    if index != rig.skeleton.index("hips")))

    def test_elbows_and_knees_fold_toward_the_body(self):
        """Flexion must shorten the limb; the opposite direction is clamped away."""
        for name, rig in self.rigs.items():
            skeleton = rig.skeleton
            limits = limits_for(skeleton)
            rest = skeleton.rest_pose()
            for joint, tip, base in (("forearm.L", "hand.L", "shoulder.L"),
                                     ("forearm.R", "hand.R", "shoulder.R"),
                                     ("shin.L", "foot.L", "hips"),
                                     ("shin.R", "foot.R", "hips")):
                with self.subTest(figure=name, joint=joint):
                    index = skeleton.index(joint)
                    limit = limits[index]
                    allowed = limit.swing_x[1] if limit.swing_x[1] > 0 else limit.swing_x[0]
                    folded = clamp_rotation(limit, rotation(X_AXIS, allowed))
                    straight = clamp_rotation(limit, rotation(X_AXIS, -allowed))

                    def span(local):
                        posed = skeleton.transforms(rest.with_rotation(index, local))
                        return (posed[skeleton.index(tip)].position
                                - posed[skeleton.index(base)].position).length()
                    extended = span(rest.rotations[index])
                    self.assertLess(span(folded), 0.5 * extended)  # Flexion folds the limb.
                    # The other way only undoes the small rest bend, so the limb
                    # stays extended instead of folding backwards.
                    self.assertGreater(span(straight), 0.95 * extended)
                    self.assertGreater(limit.swing_x[0] if limit.swing_x[1] > 0
                                       else -limit.swing_x[1], -35.0)

    def test_a_mirrored_pose_stays_inside_the_limits(self):
        rig = self.rigs["body_kun"]
        skeleton = rig.skeleton
        limits = limits_for(skeleton)
        pose = skeleton.rest_pose()
        for joint, axis, degrees in (("forearm.L", X_AXIS, 120.0), ("shin.L", X_AXIS, 90.0),
                                     ("upper_arm.L", Z_AXIS, 40.0), ("head", X_AXIS, 20.0)):
            index = skeleton.index(joint)
            pose = pose.with_rotation(index, clamp_rotation(limits[index],
                                                            rotation(axis, degrees)))
        mirrored = skeleton.mirror_pose(pose)
        for index, local in enumerate(mirrored.rotations):
            with self.subTest(joint=skeleton.joints[index].name):
                self.assertFalse(is_limited(limits[index], local, 1e-3))


if __name__ == "__main__":
    unittest.main()
