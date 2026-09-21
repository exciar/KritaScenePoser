"""Body shape: settings, the rest-pose deformation, and the reshaped figure."""

import math
import unittest

from krita_scene_poser.core.limits import limits_for
from krita_scene_poser.core.math3d import Quat, Vec3, Z_AXIS
from krita_scene_poser.core.shape import (
    LIMITS, BodyShape, base_name, deform, ground_offset, joint_factors, joint_matrices,
)
from krita_scene_poser.storage.figures import load_figure
from krita_scene_poser.storage.shaping import figure_key, shaped_figure


class SettingsTests(unittest.TestCase):
    def test_the_default_shape_changes_nothing(self):
        shape = BodyShape()
        self.assertTrue(shape.is_default())
        self.assertEqual(shape.height, 1.0)
        for name in LIMITS:
            with self.subTest(name=name):
                low, high = LIMITS[name]
                self.assertLessEqual(low, 1.0)
                self.assertGreaterEqual(high, 1.0)

    def test_values_are_clamped_and_rubbish_falls_back(self):
        wild = BodyShape(height=9.0, waist=-2.0, build=float("nan"), chest="wide",
                         arm_length=True).validated()
        self.assertEqual(wild.height, LIMITS["height"][1])
        self.assertEqual(wild.waist, LIMITS["waist"][0])
        self.assertEqual(wild.build, 1.0)
        self.assertEqual(wild.chest, 1.0)
        self.assertEqual(wild.arm_length, 1.0)  # A bool is not a number here.

    def test_json_round_trip_and_damaged_input(self):
        shape = BodyShape(height=1.2, waist=1.3, hand_size=0.8)
        self.assertEqual(BodyShape.from_json(shape.to_json()), shape)
        for text in ("", "{", "[]", None, '{"height": "tall"}'):
            with self.subTest(text=text):
                self.assertTrue(BodyShape.from_json(text).is_default())
        self.assertEqual(BodyShape.from_json('{"waist": 1.1, "tail": 3}').waist, 1.1)

    def test_controls_reach_the_right_joints(self):
        shape = BodyShape(arm_length=1.2, arm_thickness=1.3, head_size=1.1, build=1.1)
        length, girth = joint_factors(shape, "forearm.L")
        self.assertAlmostEqual(length, 1.2)
        self.assertAlmostEqual(girth, 1.3 * 1.1)
        length, girth = joint_factors(shape, "head")
        self.assertAlmostEqual(length, 1.1)
        self.assertAlmostEqual(girth, 1.1)
        self.assertEqual(joint_factors(shape, "hips")[0], 1.0)  # Height only.
        self.assertEqual(base_name("index.02.R"), "index")
        self.assertEqual(base_name("upper_arm.L"), "upper_arm")

    def test_height_scales_every_joint(self):
        for name in ("hips", "head", "forearm.R", "toe.L"):
            with self.subTest(name=name):
                length, girth = joint_factors(BodyShape(height=1.25), name)
                self.assertAlmostEqual(length, 1.25)
                self.assertAlmostEqual(girth, 1.25)


class DeformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig, cls.mesh = load_figure("body_kun")

    def shaped(self, **values):
        return shaped_figure(self.rig, self.mesh, BodyShape(**values))

    def height_of(self, mesh):
        return max(mesh.positions[1::3])

    def test_the_default_shape_returns_the_same_objects(self):
        rig, mesh = self.shaped()
        self.assertIs(rig, self.rig)
        self.assertIs(mesh, self.mesh)

    def test_height_scales_the_whole_figure(self):
        rig, mesh = self.shaped(height=1.2)
        self.assertAlmostEqual(self.height_of(mesh), self.height_of(self.mesh) * 1.2, places=3)
        self.assertEqual(mesh.vertex_count, self.mesh.vertex_count)

    def test_a_longer_arm_moves_the_hand_but_not_the_head(self):
        rig, _ = self.shaped(arm_length=1.25)
        skeleton, original = rig.skeleton, self.rig.skeleton

        def arm(bones):
            """Shoulder to wrist, which is upper arm plus forearm."""
            rest = bones.rest_transforms
            return (rest[bones.index("hand.L")].position
                    - rest[bones.index("upper_arm.L")].position).length()
        self.assertAlmostEqual(arm(skeleton) / arm(original), 1.25, places=2)
        head = skeleton.rest_transforms[skeleton.index("head")].position
        self.assertTrue(head.is_close(original.rest_transforms[original.index("head")].position,
                                      1e-6))

    def test_a_wider_waist_thickens_the_body_without_raising_it(self):
        """Measured on the skin bound to the waist; the arms hang at that height."""
        rig, mesh = self.shaped(waist=1.4)
        waist = self.rig.skeleton.index("waist")
        belly = [v for v in range(self.mesh.vertex_count)
                 if self.mesh.joints[v * 4] == waist and self.mesh.weights[v * 4] > 200]
        self.assertGreater(len(belly), 50)

        def width(source):
            xs = [source.positions[v * 3] for v in belly]
            return max(xs) - min(xs)
        self.assertGreater(width(mesh) / width(self.mesh), 1.2)
        self.assertAlmostEqual(self.height_of(mesh), self.height_of(self.mesh), places=2)

    def test_the_figure_always_stands_on_the_ground(self):
        for values in ({"leg_length": 1.3}, {"leg_length": 0.8}, {"height": 1.3},
                       {"foot_size": 1.4}, {"build": 1.3, "hips": 1.4}):
            with self.subTest(**values):
                _, mesh = self.shaped(**values)
                self.assertAlmostEqual(min(mesh.positions[1::3]), 0.0, places=6)

    def test_normals_stay_unit_length_and_turn_with_the_surface(self):
        _, mesh = self.shaped(waist=1.5, arm_thickness=1.4)
        for vertex in range(0, mesh.vertex_count, 617):
            x, y, z = mesh.normals[vertex * 3:vertex * 3 + 3]
            with self.subTest(vertex=vertex):
                self.assertAlmostEqual(math.sqrt(x * x + y * y + z * z), 1.0, places=5)

    def test_a_reshaped_figure_still_skins_and_poses(self):
        rig, mesh = self.shaped(height=1.1, leg_length=1.2, build=1.2)
        skeleton = rig.skeleton
        # Skinning at rest must be the identity, or the mesh would jump on load.
        for matrix in skeleton.skinning_matrices(skeleton.rest_pose()):
            for index, value in enumerate(matrix.m):
                expected = 1.0 if index in (0, 5, 10, 15) else 0.0
                self.assertAlmostEqual(value, expected, places=6)
        # Limits, IK, and mirroring still work on the new skeleton.
        self.assertIsNotNone(limits_for(skeleton)[skeleton.index("forearm.L")])
        pose = skeleton.rotate_world(skeleton.rest_pose(), skeleton.index("upper_arm.L"),
                                     Quat.from_axis_angle(Z_AXIS, 0.5))
        self.assertNotEqual(pose, skeleton.rest_pose())
        target = skeleton.rest_transforms[skeleton.index("hand.L")].position + Vec3(0.0, 0.1, 0.1)
        _, reached = skeleton.solve_ik(skeleton.rest_pose(), "hand.L", target)
        self.assertTrue(reached)

    def test_joint_and_mesh_data_stay_consistent(self):
        rig, mesh = self.shaped(height=1.15, hand_size=1.2)
        self.assertEqual(len(rig.joints), len(self.rig.joints))
        self.assertEqual(mesh.part_names, self.mesh.part_names)
        self.assertEqual(mesh.joints, self.mesh.joints)
        self.assertEqual(mesh.weights, self.mesh.weights)
        self.assertEqual(len(mesh.indices), len(self.mesh.indices))
        for joint, original in zip(rig.joints, self.rig.joints):
            with self.subTest(joint=joint.name):
                self.assertEqual(joint.parent, original.parent)
                self.assertGreater((joint.tail - joint.position).length(), 0.0)

    def test_ground_offset_and_cache_keys(self):
        self.assertAlmostEqual(ground_offset([0.0, 0.5, 0.0, 0.0, -0.25, 0.0]), 0.25)
        self.assertEqual(figure_key("body_kun", BodyShape()), "body_kun")
        self.assertNotEqual(figure_key("body_kun", BodyShape(height=1.1)), "body_kun")
        self.assertEqual(figure_key("body_kun", BodyShape(height=1.1)),
                         figure_key("body_kun", BodyShape(height=1.1)))

    def test_matrices_cover_every_joint(self):
        matrices, positions, normals = joint_matrices(self.rig.skeleton, BodyShape(height=1.1))
        count = len(self.rig.skeleton.joints)
        self.assertEqual((len(matrices), len(positions), len(normals)), (count, count, count))
        self.assertEqual(len(matrices[0]), 12)
        self.assertEqual(len(normals[0]), 9)

    def test_deform_with_no_weights_keeps_the_point(self):
        matrices, _, normals = joint_matrices(self.rig.skeleton, BodyShape(height=2.0))
        positions, _ = deform([1.0, 2.0, 3.0], [0.0, 1.0, 0.0], bytes(4), bytes(4),
                              matrices, normals)
        self.assertEqual(positions, [0.0, 0.0, 0.0])  # No influence, no transform.


if __name__ == "__main__":
    unittest.main()
