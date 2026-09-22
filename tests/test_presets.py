"""The bundled pose presets: whatever is in the folder must load and suit either figure."""

import unittest

from krita_scene_poser.core.limits import is_limited
from krita_scene_poser.storage.figures import load_figure
from krita_scene_poser.storage.presets import available_presets, load_preset, preset_text
from krita_scene_poser.storage.scene_io import read_pose


class PresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rigs = {name: load_figure(name)[0] for name in ("body_chan", "body_kun")}
        cls.presets, cls.problems = available_presets()

    def test_the_bundled_set_is_present_and_readable(self):
        self.assertEqual(self.problems, [])
        self.assertTrue(self.presets, "No poses in the bundled folder")
        labels = [label for _, label in self.presets]
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(label, label.strip())
                self.assertTrue(label)
        self.assertEqual(len(set(labels)), len(labels), "Two poses show the same name")
        # Sorted by the name shown, so the docker list needs no sorting of its own.
        self.assertEqual(labels, sorted(labels))

    def test_every_preset_applies_to_both_figures(self):
        for figure, rig in self.rigs.items():
            for preset, _ in self.presets:
                with self.subTest(figure=figure, preset=preset):
                    applied = load_preset(preset, rig.skeleton)
                    self.assertEqual(applied.unknown, ())
                    self.assertEqual(len(applied.pose.rotations), len(rig.skeleton.joints))

    def test_presets_stay_inside_the_joint_limits(self):
        skeleton = self.rigs["body_kun"].skeleton
        for preset, _ in self.presets:
            pose = load_preset(preset, skeleton).pose
            for index, rotation in enumerate(pose.rotations):
                with self.subTest(preset=preset, joint=skeleton.joints[index].name):
                    self.assertFalse(
                        is_limited(skeleton.limits[index], rotation, 1e-3),
                        "Save the pose with Joint limits on, or this joint is clamped on load")

    def test_a_preset_is_a_pose_file_like_any_other(self):
        skeleton = self.rigs["body_chan"].skeleton
        text = preset_text(self.presets[0][0])
        self.assertEqual(read_pose(text, skeleton).unknown, ())
        self.assertIn('"format": "ksp-pose"', text)


if __name__ == "__main__":
    unittest.main()
