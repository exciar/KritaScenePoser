"""Reading a pose off a posed .glb and putting it on a KSP figure."""

import json
import math
import random
import unittest

from glb_fixtures import GlbBuilder, posed_glb, simple_glb
from krita_scene_poser.core.limits import is_limited
from krita_scene_poser.core.math3d import Quat, Vec3, X_AXIS, Y_AXIS
from krita_scene_poser.storage.figures import load_figure
from krita_scene_poser.storage.import_figure import FigureImportError
from krita_scene_poser.storage.import_pose import read_pose, read_pose_map
from krita_scene_poser.storage.scene_io import read_pose as read_pose_file, write_pose

SHOULDER = "mixamorig:LeftArm"
ELBOW = "mixamorig:LeftForeArm"


def turned(bone, axis, angle, **options):
    return posed_glb(pose={bone: Quat.from_axis_angle(axis, angle)},
                     pose_shift={}, **options)


class PoseImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig, _ = load_figure("body_kun")
        cls.skeleton = cls.rig.skeleton
        cls.rest = cls.skeleton.transforms(cls.skeleton.rest_pose())

    def world(self, pose, joint):
        return self.skeleton.transforms(pose)[self.skeleton.index(joint)]

    def test_a_posed_file_puts_the_same_turn_on_the_figure(self):
        angle = 0.4
        result = read_pose(turned(SHOULDER, X_AXIS, angle), self.skeleton, "arm.glb")
        self.assertEqual(result.scheme, "Mixamo")
        self.assertIn("upper_arm.L", result.applied)
        index = self.skeleton.index("upper_arm.L")
        delta = (self.world(result.pose, "upper_arm.L").rotation
                 * self.rest[index].rotation.inverse()).normalized()
        expected = Quat.from_axis_angle(X_AXIS, angle)
        self.assertTrue(delta.is_close(expected, 1e-6) or delta.is_close(
            Quat(-expected.w, -expected.x, -expected.y, -expected.z), 1e-6))

    def test_children_follow_the_joints_above_them(self):
        result = read_pose(turned(SHOULDER, X_AXIS, 0.6), self.skeleton, "arm.glb")
        moved = self.world(result.pose, "hand.L").position
        self.assertGreater((moved - self.rest[self.skeleton.index("hand.L")].position).length(),
                           0.05)

    def test_joints_the_file_does_not_have_stay_at_rest(self):
        result = read_pose(posed_glb(), self.skeleton, "p.glb")
        self.assertIn("index.01.L", result.missing)  # The fixture has no fingers.
        for name in result.missing:
            index = self.skeleton.index(name)
            self.assertTrue(result.pose.rotations[index].is_close(
                self.skeleton.rest_pose().rotations[index], 1e-12))

    def test_the_hips_shift_scales_to_this_figure(self):
        result = read_pose(posed_glb(), self.skeleton, "p.glb")  # Hips 0.1 m lower.
        hips = self.rest[self.skeleton.index("hips")].position.y
        self.assertAlmostEqual(result.pose.root_translation.y, -0.1 * hips / 0.95, places=6)
        self.assertAlmostEqual(result.pose.root_translation.x, 0.0, places=9)

    def test_an_impossible_elbow_is_clamped_like_any_other_pose(self):
        # Bending an elbow the wrong way is exactly what joint limits refuse.
        result = read_pose(turned(ELBOW, X_AXIS, -2.4), self.skeleton, "bad.glb")
        for index, rotation in enumerate(result.pose.rotations):
            with self.subTest(joint=self.skeleton.joints[index].name):
                self.assertFalse(is_limited(self.skeleton.limits[index], rotation, 1e-3))
        index = self.skeleton.index("forearm.L")
        delta = (self.world(result.pose, "forearm.L").rotation
                 * self.rest[index].rotation.inverse()).normalized()
        self.assertFalse(delta.is_close(Quat.from_axis_angle(X_AXIS, -2.4), 1e-3),
                         "the backwards bend should have been held back")

    def test_a_file_that_faces_away_is_turned_around(self):
        angle = 0.5
        straight = read_pose(turned(SHOULDER, X_AXIS, angle), self.skeleton, "a.glb")
        flipped = read_pose(turned(SHOULDER, X_AXIS, angle, flip_facing=True),
                            self.skeleton, "b.glb")
        self.assertFalse(straight.facing_flipped)
        self.assertTrue(flipped.facing_flipped)
        for name in straight.applied:
            index = self.skeleton.index(name)
            with self.subTest(joint=name):
                self.assertTrue(flipped.pose.rotations[index].is_close(
                    straight.pose.rotations[index], 1e-6))

    def test_an_imported_pose_is_an_ordinary_pose_file(self):
        result = read_pose(posed_glb(), self.skeleton, "p.glb")
        text = write_pose(self.skeleton, result.pose, "body_kun")
        again = read_pose_file(text, self.skeleton)
        self.assertEqual(again.unknown, ())
        for before, after in zip(result.pose.rotations, again.pose.rotations):
            self.assertTrue(before.is_close(after, 1e-6))
        self.assertTrue(again.pose.root_translation.is_close(result.pose.root_translation, 1e-5))

    def test_a_pose_moves_between_figures(self):
        other = load_figure("body_chan")[0].skeleton
        result = read_pose(posed_glb(), other, "p.glb")
        self.assertEqual(len(result.applied), 22)

    def test_a_custom_map_names_unknown_bones(self):
        skeleton = (("Pelvis", None, (0.0, 0.95, 0.0)), ("Spine_01", "Pelvis", (0.0, 1.1, 0.0)))
        data = GlbBuilder(skeleton=skeleton,
                          pose={"Spine_01": Quat.from_axis_angle(Y_AXIS, 0.3)}).build(
            parts=[("blob", "Pelvis", (0.0, 1.0, 0.0), (0.2, 0.2, 0.2))])
        with self.assertRaises(FigureImportError) as caught:
            read_pose(data, self.skeleton, "x.glb")
        self.assertIn(".ksp-map.json", str(caught.exception))
        mapping = read_pose_map(json.dumps({"hips": "Pelvis", "waist": "Spine_01"}))
        result = read_pose(data, self.skeleton, "x.glb", custom_map=mapping)
        self.assertEqual(result.applied, ("hips", "waist"))
        self.assertIn("map", result.scheme)

    def test_damaged_and_unposable_files_are_refused(self):
        good = posed_glb()
        cases = {
            "not a glb": b"nothing like a model",
            "empty": b"",
            "truncated": good[:len(good) // 2],
            "no skin": simple_glb(extras={"skins": []}),
            "no bind matrices": simple_glb(extras={"skins": [{"joints": [0, 1]}]}),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(FigureImportError):
                    read_pose(data, self.skeleton, label)

    def test_random_damage_never_escapes_as_another_error(self):
        good = bytearray(posed_glb())
        generator = random.Random(23)
        for attempt in range(60):
            data = bytearray(good)
            if attempt % 2 == 0:
                data = data[:generator.randrange(1, len(data))]
            else:
                for _ in range(generator.randrange(1, 8)):
                    data[generator.randrange(len(data))] = generator.randrange(256)
            with self.subTest(attempt=attempt):
                try:
                    read_pose(bytes(data), self.skeleton, "fuzz.glb")
                except FigureImportError:
                    pass


if __name__ == "__main__":
    unittest.main()
