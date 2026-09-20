"""Pose and scene files: round trips, portability between figures, and rejection."""

import json
import unittest

from krita_scene_poser.core.camera import OrbitCamera
from krita_scene_poser.core.lineart import LineArtSettings
from krita_scene_poser.core.math3d import Quat, Vec3, X_AXIS, Y_AXIS, Z_AXIS
from krita_scene_poser.core.output import OutputSettings
from krita_scene_poser.storage.figures import load_figure
from krita_scene_poser.storage.scene_io import (
    SceneFormatError, read_pose, read_scene, write_pose, write_scene,
)


def posed(skeleton):
    """A pose using the root and several joints, all inside the limits."""
    pose = skeleton.rest_pose()
    for name, axis, angle in (("upper_arm.L", Z_AXIS, 0.7), ("forearm.L", X_AXIS, 1.1),
                              ("head", Y_AXIS, 0.3), ("thigh.R", X_AXIS, -0.4)):
        pose = skeleton.set_rotation(pose, skeleton.index(name),
                                     Quat.from_axis_angle(axis, angle))
    return type(pose)(pose.rotations, Vec3(0.1, 0.0, -0.2),
                      Quat.from_axis_angle(Y_AXIS, 0.5), pose.root_scale)


class PoseFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rigs = {name: load_figure(name)[0] for name in ("body_chan", "body_kun")}

    def setUp(self):
        self.skeleton = self.rigs["body_chan"].skeleton

    def test_round_trip_restores_every_joint_and_the_root(self):
        pose = posed(self.skeleton)
        loaded = read_pose(write_pose(self.skeleton, pose, "body_chan"), self.skeleton)
        self.assertEqual(loaded.unknown, ())
        for index, joint in enumerate(self.skeleton.joints):
            with self.subTest(joint=joint.name):
                self.assertTrue(loaded.pose.rotations[index].is_close(
                    pose.rotations[index], 1e-7))
        self.assertTrue(loaded.pose.root_translation.is_close(pose.root_translation, 1e-6))
        self.assertTrue(loaded.pose.root_rotation.is_close(pose.root_rotation, 1e-7))

    def test_only_moved_joints_are_stored(self):
        pose = self.skeleton.set_rotation(self.skeleton.rest_pose(),
                                          self.skeleton.index("head"),
                                          Quat.from_axis_angle(Y_AXIS, 0.2))
        data = json.loads(write_pose(self.skeleton, pose, "body_chan"))
        self.assertEqual(list(data["rotations"]), ["head"])
        self.assertEqual(data["format"], "ksp-pose")
        self.assertEqual(data["version"], 1)

    def test_a_pose_moves_between_figures(self):
        pose = posed(self.skeleton)
        text = write_pose(self.skeleton, pose, "body_chan")
        other = self.rigs["body_kun"].skeleton
        loaded = read_pose(text, other)
        self.assertEqual(loaded.unknown, ())
        self.assertGreater(len(loaded.applied), 3)
        index = other.index("forearm.L")
        self.assertTrue(loaded.pose.rotations[index].is_close(
            pose.rotations[self.skeleton.index("forearm.L")], 1e-7))

    def test_joints_the_figure_lacks_are_reported_not_fatal(self):
        pose = posed(self.skeleton)
        data = json.loads(write_pose(self.skeleton, pose, "body_chan"))
        data["rotations"]["tail.01"] = [1.0, 0.0, 0.0, 0.0]
        loaded = read_pose(json.dumps(data), self.skeleton)
        self.assertEqual(loaded.unknown, ("tail.01",))
        self.assertIn("tail.01", loaded.describe("Body-chan"))

    def test_a_loaded_pose_obeys_joint_limits(self):
        """A file cannot smuggle in a rotation a drag could never make."""
        data = json.loads(write_pose(self.skeleton, self.skeleton.rest_pose(), "body_chan"))
        backwards = Quat.from_axis_angle(X_AXIS, -2.0)  # An inverted elbow.
        data["rotations"]["forearm.L"] = list(backwards)
        loaded = read_pose(json.dumps(data), self.skeleton)
        index = self.skeleton.index("forearm.L")
        self.assertFalse(loaded.pose.rotations[index].is_close(backwards, 1e-3))
        self.assertFalse(self.skeleton.limits[index] is None)

    def test_damaged_files_are_rejected_with_a_reason(self):
        good = json.loads(write_pose(self.skeleton, posed(self.skeleton), "body_chan"))

        def variant(change):
            data = json.loads(json.dumps(good))
            change(data)
            return json.dumps(data)
        cases = {
            "not json": "{ not json",
            "wrong format": variant(lambda d: d.update(format="ksp-scene")),
            "newer version": variant(lambda d: d.update(version=2)),
            "no rotations": variant(lambda d: d.pop("rotations")),
            "rotations not an object": variant(lambda d: d.update(rotations=[])),
            "short quaternion": variant(lambda d: d["rotations"].update({"head": [1, 0, 0]})),
            "text in a quaternion": variant(
                lambda d: d["rotations"].update({"head": ["a", 0, 0, 1]})),
            "not a unit quaternion": variant(
                lambda d: d["rotations"].update({"head": [3, 0, 0, 1]})),
            "bad root": variant(lambda d: d["root"].update(translation=[1, 2])),
        }
        for label, text in cases.items():
            with self.subTest(label):
                with self.assertRaises(SceneFormatError):
                    read_pose(text, self.skeleton)

    def test_unknown_keys_are_ignored_within_a_version(self):
        data = json.loads(write_pose(self.skeleton, posed(self.skeleton), "body_chan"))
        data["future"] = {"anything": 1}
        data["root"]["scale"] = 2.0
        self.assertTrue(read_pose(json.dumps(data), self.skeleton).applied)


class SceneFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skeleton = load_figure("body_kun")[0].skeleton

    def test_round_trip_restores_the_whole_workspace(self):
        camera = OrbitCamera(target=Vec3(0.0, 1.1, 0.0), yaw=0.8, pitch=-0.2, distance=2.4,
                             orthographic=True)
        lines = LineArtSettings(color="#204080", outline_width=4.0, seams=False, opacity=0.6)
        output = OutputSettings(mode="custom", width=800, height=1200, anchor="topleft",
                                supersample=2)
        text = write_scene(self.skeleton, posed(self.skeleton), figure="body_kun",
                           camera=camera, mode="both", lines=lines, opacity=0.5,
                           layer_opacity=0.75, output=output)
        scene = read_scene(text, self.skeleton)
        self.assertEqual(scene.figure, "body_kun")
        self.assertEqual(scene.mode, "both")
        self.assertEqual(scene.lines, lines)
        self.assertEqual(scene.output, output)
        self.assertEqual((scene.opacity, scene.layer_opacity), (0.5, 0.75))
        self.assertTrue(scene.camera.target.is_close(camera.target, 1e-9))
        self.assertAlmostEqual(scene.camera.yaw, camera.yaw)
        self.assertAlmostEqual(scene.camera.distance, camera.distance)
        self.assertTrue(scene.camera.orthographic)
        self.assertTrue(scene.pose.rotations[self.skeleton.index("head")].is_close(
            posed(self.skeleton).rotations[self.skeleton.index("head")], 1e-7))

    def test_defaults_fill_in_for_a_sparse_or_damaged_scene(self):
        text = write_scene(self.skeleton, self.skeleton.rest_pose())
        data = json.loads(text)
        for key in ("camera", "lines", "output", "display", "opacity", "layer_opacity"):
            data.pop(key)
        scene = read_scene(json.dumps(data), self.skeleton)
        self.assertEqual(scene.mode, "shaded")
        self.assertEqual(scene.lines, LineArtSettings())
        self.assertEqual(scene.output, OutputSettings())
        self.assertEqual((scene.opacity, scene.layer_opacity), (1.0, 1.0))
        self.assertAlmostEqual(scene.camera.distance, OrbitCamera().distance)

        damaged = json.loads(text)
        damaged.update(camera="tilted", lines=7, output=[], display="sideways",
                       opacity="half", layer_opacity=None)
        scene = read_scene(json.dumps(damaged), self.skeleton)
        self.assertEqual(scene.mode, "shaded")
        self.assertEqual(scene.lines, LineArtSettings())
        self.assertEqual((scene.opacity, scene.layer_opacity), (1.0, 1.0))

    def test_a_pose_file_is_not_a_scene_file(self):
        pose_text = write_pose(self.skeleton, self.skeleton.rest_pose())
        with self.assertRaises(SceneFormatError):
            read_scene(pose_text, self.skeleton)
        with self.assertRaises(SceneFormatError):
            read_pose(write_scene(self.skeleton, self.skeleton.rest_pose()), self.skeleton)

    def test_files_are_written_the_same_way_every_time(self):
        pose = posed(self.skeleton)
        first = write_scene(self.skeleton, pose, figure="body_kun")
        second = write_scene(self.skeleton, pose, figure="body_kun")
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
