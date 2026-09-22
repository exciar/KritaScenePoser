"""The development tool that checks and grounds bundled poses."""

import json
import os
import tempfile
import unittest

from krita_scene_poser.core.math3d import Vec3
from krita_scene_poser.storage.figures import load_figure
from krita_scene_poser.storage.presets import SUFFIX, available_presets, load_preset, write_preset
from krita_scene_poser.storage.scene_io import write_pose
from tools.preview_poses import floor_offset, ground, ground_preset


class PreviewPoseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rig, cls.mesh = load_figure("body_kun")
        cls.skeleton = rig.skeleton

    def floating_pose(self, height):
        """A bundled pose lifted off the floor, so grounding has work to do."""
        preset = available_presets()[0][0][0]
        pose = load_preset(preset, self.skeleton).pose
        translation = pose.root_translation
        return type(pose)(pose.rotations,
                          Vec3(translation.x, translation.y + height, translation.z),
                          pose.root_rotation, pose.root_scale)

    def test_grounding_puts_the_lowest_point_on_the_floor(self):
        for height in (0.5, -0.3):
            with self.subTest(height=height):
                pose = self.floating_pose(height)
                self.assertAlmostEqual(floor_offset(self.skeleton, self.mesh, pose), height,
                                       places=6)
                grounded = ground(self.skeleton, self.mesh, pose)
                self.assertAlmostEqual(floor_offset(self.skeleton, self.mesh, grounded), 0.0,
                                       places=6)

    def test_grounding_a_file_changes_only_its_root_height(self):
        pose = self.floating_pose(0.5)
        with tempfile.TemporaryDirectory() as folder:
            write_preset(folder, "floating", write_pose(self.skeleton, pose, "body_kun"),
                         "Floating")
            with open(os.path.join(folder, "floating" + SUFFIX), encoding="utf-8") as handle:
                before = json.load(handle)

            self.assertAlmostEqual(ground_preset("floating", folder), 0.5, places=6)

            with open(os.path.join(folder, "floating" + SUFFIX), encoding="utf-8") as handle:
                after = json.load(handle)
            self.assertEqual(after["rotations"], before["rotations"])
            self.assertEqual(after["name"], "Floating")
            self.assertEqual(after["figure"], "body_kun")
            self.assertEqual(after["root"]["rotation"], before["root"]["rotation"])
            self.assertAlmostEqual(after["root"]["translation"][1],
                                   before["root"]["translation"][1] - 0.5, places=5)
            grounded = load_preset("floating", self.skeleton, folder).pose
            self.assertAlmostEqual(floor_offset(self.skeleton, self.mesh, grounded), 0.0,
                                   places=5)


if __name__ == "__main__":
    unittest.main()
