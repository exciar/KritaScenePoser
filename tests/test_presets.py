"""The bundled pose presets: every one loads and suits either figure."""

import unittest

from krita_scene_poser.core.limits import is_limited
from krita_scene_poser.storage.figures import load_figure
from krita_scene_poser.storage.presets import available_presets, load_preset, preset_text
from krita_scene_poser.storage.scene_io import read_pose

EXPECTED = {"t-pose", "relaxed", "sitting", "kneeling", "walking", "running",
            "hands-clasped", "reaching-up", "crouching"}


class PresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rigs = {name: load_figure(name)[0] for name in ("body_chan", "body_kun")}
        cls.presets, cls.problems = available_presets()

    def test_the_bundled_set_is_present_and_readable(self):
        self.assertEqual(self.problems, [])
        self.assertEqual({preset for preset, _ in self.presets}, EXPECTED)
        for preset, label in self.presets:
            with self.subTest(preset=preset):
                self.assertTrue(label and label[0].isupper())
        # Sorted by the name shown, so the docker list needs no sorting of its own.
        self.assertEqual([label for _, label in self.presets],
                         sorted(label for _, label in self.presets))

    def test_every_preset_applies_to_both_figures(self):
        for figure, rig in self.rigs.items():
            for preset, _ in self.presets:
                with self.subTest(figure=figure, preset=preset):
                    applied = load_preset(preset, rig.skeleton)
                    self.assertEqual(applied.unknown, ())
                    self.assertGreater(len(applied.applied), 3)
                    self.assertEqual(len(applied.pose.rotations), len(rig.skeleton.joints))

    def test_presets_stay_inside_the_joint_limits(self):
        skeleton = self.rigs["body_kun"].skeleton
        for preset, _ in self.presets:
            pose = load_preset(preset, skeleton).pose
            for index, rotation in enumerate(pose.rotations):
                with self.subTest(preset=preset, joint=skeleton.joints[index].name):
                    self.assertFalse(is_limited(skeleton.limits[index], rotation, 1e-3))

    def test_presets_move_the_figure(self):
        skeleton = self.rigs["body_kun"].skeleton
        rest = skeleton.transforms(skeleton.rest_pose())
        hand, foot = skeleton.index("hand.L"), skeleton.index("foot.R")
        for preset, _ in self.presets:
            with self.subTest(preset=preset):
                posed = skeleton.transforms(load_preset(preset, skeleton).pose)
                moved = max((posed[i].position - rest[i].position).length()
                            for i in (hand, foot))
                self.assertGreater(moved, 0.05)

    def test_a_preset_is_a_pose_file_like_any_other(self):
        skeleton = self.rigs["body_chan"].skeleton
        text = preset_text("t-pose")
        self.assertEqual(read_pose(text, skeleton).unknown, ())
        self.assertIn('"format": "ksp-pose"', text)


if __name__ == "__main__":
    unittest.main()
