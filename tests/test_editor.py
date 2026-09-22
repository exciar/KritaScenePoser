"""Pose editor gestures, commands, and undo history on the real figures."""

import math
import unittest

from krita_scene_poser.core.commands import PoseHistory
from krita_scene_poser.core.editor import DRAG, RINGS, PoseEditor, display_name
from krita_scene_poser.core.gizmo import joint_axes
from krita_scene_poser.core.limits import clamp_rotation
from krita_scene_poser.core.math3d import IDENTITY, Quat, Ray, Vec3, Y_AXIS, Z_AXIS
from krita_scene_poser.core.picking import FigurePicker
from krita_scene_poser.storage.figures import load_figure

VIEW = Vec3(0.0, 0.0, -1.0)  # Looking at the figure's front.


def ray_to(point):
    return Ray(point - VIEW * 5.0, VIEW)


class HistoryTests(unittest.TestCase):
    def test_record_undo_redo_and_limit(self):
        history = PoseHistory(limit=2)
        self.assertFalse(history.record("a", "a"))
        for before, after in (("a", "b"), ("b", "c"), ("c", "d")):
            self.assertTrue(history.record(before, after))
        self.assertEqual(history.undo(), "c")
        self.assertEqual(history.undo(), "b")
        self.assertIsNone(history.undo())  # Oldest entry was dropped by the limit.
        self.assertEqual(history.redo(), "c")
        history.record("c", "x")
        self.assertFalse(history.can_redo)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.figures = {}
        for name in ("body_chan", "body_kun"):
            rig, mesh = load_figure(name)
            cls.figures[name] = (rig.skeleton, FigurePicker(rig, mesh))

    def setUp(self):
        self.skeleton, picker = self.figures["body_chan"]
        # Gesture geometry is checked here with rotations no body could hold;
        # LimitedEditorTests below covers what joint limits do to a drag.
        self.skeleton.limits_enabled = False
        self.editor = PoseEditor(self.skeleton, picker)

    def joint(self, name):
        return self.skeleton.index(name)

    def position(self, name):
        return self.editor.transforms()[self.joint(name)].position

    def segment(self, name):
        return self.editor.segments()[self.joint(name)]

    def test_aim_points_the_grabbed_part_at_the_cursor(self):
        start, end = self.segment("forearm.L")
        grab = (start + end) * 0.5
        self.assertEqual(self.editor.begin_drag(self.joint("forearm.L"), grab, VIEW, 0, 0), "aim")
        target = start + Vec3(0.1, 0.2, 0.0)
        self.assertTrue(self.editor.drag(ray_to(target), 10, -20))
        new_start, new_end = self.segment("forearm.L")
        self.assertTrue(new_start.is_close(start, 1e-9))  # The pivot stays put.
        # The grabbed point follows the cursor on the view plane through the grab.
        cursor = Vec3(target.x, target.y, grab.z)
        self.assertTrue((new_end - new_start).normalized().is_close((cursor - start).normalized(), 1e-9))
        self.assertTrue(((new_start + new_end) * 0.5 - start).normalized().is_close(
            (cursor - start).normalized(), 1e-9))

    def test_twist_keeps_the_bone_direction(self):
        start, end = self.segment("upper_arm.R")
        joint = self.joint("upper_arm.R")
        before = self.editor.transforms()[joint].rotation
        self.assertEqual(self.editor.begin_drag(joint, end, VIEW, 0, 0, shift=True), "twist")
        self.editor.drag(ray_to(end), 60, 0)
        new_start, new_end = self.segment("upper_arm.R")
        self.assertTrue((new_end - new_start).normalized().is_close((end - start).normalized(), 1e-9))
        self.assertAlmostEqual((self.editor.transforms()[joint].rotation * before.inverse()).angle(), 0.6)

    def test_ik_drag_places_the_hand_and_keeps_its_orientation(self):
        hand = self.joint("hand.L")
        grab = self.position("hand.L")
        before = self.editor.transforms()[hand].rotation
        self.assertEqual(self.editor.begin_drag(hand, grab, VIEW, 0, 0), "ik")
        target = grab + Vec3(-0.1, 0.25, 0.0)
        self.editor.drag(ray_to(target), 0, 0)
        self.assertTrue(self.position("hand.L").is_close(target, 1e-9))
        self.assertTrue(self.editor.transforms()[hand].rotation.is_close(before, 1e-9))
        self.assertEqual(self.editor.drag_kind(hand, ctrl=True), "aim")

    def test_hips_move_and_turn_the_whole_figure(self):
        hips = self.joint("hips")
        grab = self.position("hips")
        feet = self.position("foot.L")
        self.assertEqual(self.editor.begin_drag(hips, grab, VIEW, 0, 0), "move")
        self.editor.drag(ray_to(grab + Vec3(0.3, 0.1, 0.0)), 0, 0)
        self.assertTrue(self.position("foot.L").is_close(feet + Vec3(0.3, 0.1, 0.0), 1e-9))
        self.editor.end_drag()
        self.assertEqual(self.editor.begin_drag(hips, grab, VIEW, 0, 0, shift=True), "turn")
        self.editor.drag(ray_to(grab), 100, 0)
        self.assertTrue(self.editor.pose.root_rotation.is_close(Quat.from_axis_angle(Y_AXIS, 1.0), 1e-9))

    def test_cancel_restores_the_exact_pose_and_adds_no_undo(self):
        start = self.editor.pose
        joint = self.joint("thigh.L")
        begin, end = self.segment("thigh.L")
        self.editor.begin_drag(joint, end, VIEW, 0, 0)
        for step in range(5):
            self.editor.drag(ray_to(end + Vec3(0.05 * step, 0.1, 0.0)), step, step)
        self.assertNotEqual(self.editor.pose, start)
        self.assertTrue(self.editor.cancel_drag())
        self.assertIs(self.editor.pose, start)
        self.assertFalse(self.editor.history.can_undo)

    def test_one_undo_entry_per_gesture_then_undo_and_redo(self):
        rest = self.editor.pose
        begin, end = self.segment("forearm.R")
        self.editor.begin_drag(self.joint("forearm.R"), end, VIEW, 0, 0)
        for step in range(1, 8):
            self.editor.drag(ray_to(end + Vec3(0.0, 0.03 * step, 0.0)), 0, step)
        self.assertTrue(self.editor.end_drag())
        posed = self.editor.pose
        self.assertTrue(self.editor.undo())
        self.assertIs(self.editor.pose, rest)
        self.assertFalse(self.editor.history.can_undo)
        self.assertTrue(self.editor.redo())
        self.assertIs(self.editor.pose, posed)
        # A click without movement is not an undo step.
        self.editor.begin_drag(self.joint("head"), self.position("head"), VIEW, 0, 0)
        self.assertFalse(self.editor.end_drag())

    def test_ring_drag_rotates_only_about_its_axis(self):
        joint = self.joint("upper_arm.L")
        x_axis = joint_axes(self.editor.transforms()[joint].rotation)[0]
        before = self.editor.transforms()[joint].rotation
        pivot = self.position("upper_arm.L")
        view = x_axis * -1.0  # Face-on: plane method.
        start_point = pivot + joint_axes(before)[1] * 0.1
        self.editor.mode = RINGS
        self.editor.begin_ring_drag(joint, x_axis, Ray(start_point - view, view), view, 0, 0, None, 60)
        target = pivot + Quat.from_axis_angle(x_axis, math.radians(30)).rotate(start_point - pivot)
        self.editor.drag(Ray(target - view, view), 0, 0)
        after = self.editor.transforms()[joint].rotation
        self.assertTrue((after * before.inverse()).is_close(Quat.from_axis_angle(x_axis, math.radians(30)), 1e-9))
        self.assertTrue(joint_axes(after)[0].is_close(x_axis, 1e-9))

    def test_edge_on_ring_uses_the_screen_tangent_without_jumps(self):
        joint = self.joint("forearm.L")
        before = self.editor.transforms()[joint].rotation
        axis = Vec3(1.0, 0.0, 0.0)  # Perpendicular to the view: edge-on.
        self.editor.begin_ring_drag(joint, axis, ray_to(self.position("forearm.L")), VIEW, 100, 100,
                                    (0.0, -1.0), 60)
        angles = []
        for step in range(0, 61, 10):
            self.editor.drag(ray_to(self.position("forearm.L")), 100, 100 - step)
            angles.append((self.editor.transforms()[joint].rotation * before.inverse()).angle())
        self.assertAlmostEqual(angles[-1], 1.0, places=9)  # 60 px along a 60 px radius.
        self.assertTrue(all(b - a < 0.2 for a, b in zip(angles, angles[1:])))

    def test_reset_mirror_and_limb_commands(self):
        editor, forearm = self.editor, self.joint("forearm.L")
        editor.select(forearm)
        editor.pose = editor.pose.with_rotation(forearm, Quat.from_axis_angle(Z_AXIS, 0.8))
        self.assertTrue(editor.mirror_limb())
        right = self.skeleton.mirror_indices[forearm]
        left_hand, right_hand = self.position("hand.L"), self.position("hand.R")
        self.assertTrue(right_hand.is_close(Vec3(-left_hand.x, left_hand.y, left_hand.z), 2e-3))
        editor.select(self.joint("chest"))
        self.assertFalse(editor.mirror_limb())  # Center joints have no other side.
        self.assertTrue(editor.mirror_pose())
        editor.select(right)
        self.assertTrue(editor.reset_joint())
        self.assertEqual(editor.pose.rotations[right], IDENTITY)
        self.assertTrue(editor.reset_pose())
        self.assertEqual(editor.pose, self.skeleton.rest_pose())
        entries = 0
        while editor.undo():
            entries += 1
        self.assertEqual(entries, 4)

    def test_switching_figures_keeps_the_pose(self):
        editor, forearm = self.editor, self.joint("forearm.L")
        editor.select(forearm)
        editor.pose = editor.pose.with_rotation(forearm, Quat.from_axis_angle(Z_AXIS, 0.5))
        skeleton, picker = self.figures["body_kun"]
        editor.set_figure(skeleton, picker)
        self.assertEqual(editor.pose.rotations[forearm], Quat.from_axis_angle(Z_AXIS, 0.5).normalized())
        self.assertEqual(editor.selected, forearm)
        self.assertEqual(len(editor.transforms()), 52)

    def test_subtree_hints_and_names(self):
        members = {self.skeleton.joints[i].name for i in self.editor.subtree(self.joint("upper_arm.L"))}
        self.assertIn("pinky.03.L", members)
        self.assertNotIn("shoulder.L", members)
        self.assertEqual(self.editor.mode, DRAG)
        self.assertIn("IK", self.editor.hint(self.joint("foot.R")))
        self.assertEqual(display_name("index.02.R"), "Right index finger 2")
        self.assertEqual(display_name("hips"), "Hips")


class LimitedEditorTests(EditorTests):
    """The same editor with joint limits on, as the docker ships it."""

    def setUp(self):
        super().setUp()
        self.skeleton.limits_enabled = True
        self.editor = PoseEditor(self.skeleton, self.figures["body_chan"][1])

    # Gesture-geometry tests from the base class assume unclamped rotation.
    test_aim_points_the_grabbed_part_at_the_cursor = None
    test_edge_on_ring_uses_the_screen_tangent_without_jumps = None
    test_ik_drag_places_the_hand_and_keeps_its_orientation = None
    test_reset_mirror_and_limb_commands = None

    def elbow_bend(self):
        """Degrees the elbow is folded; 0 is straight, and the rest pose is ~15."""
        transforms = self.editor.transforms()
        upper, elbow, wrist = (transforms[self.joint(name)].position
                               for name in ("upper_arm.L", "forearm.L", "hand.L"))
        first, second = (elbow - upper).normalized(), (wrist - elbow).normalized()
        return math.degrees(math.acos(max(-1.0, min(1.0, first.dot(second)))))

    def inverted_target(self):
        """Where the wrist would go if the elbow folded the wrong way."""
        transforms = self.editor.transforms()
        forearm = transforms[self.joint("forearm.L")]
        wrist = transforms[self.joint("hand.L")].position
        hinge = forearm.rotation.rotate(Vec3(1.0, 0.0, 0.0))
        return forearm.position + Quat.from_axis_angle(hinge, -1.5).rotate(
            wrist - forearm.position)

    def drag_forearm_to(self, target):
        start, end = self.segment("forearm.L")
        self.editor.begin_drag(self.joint("forearm.L"), (start + end) * 0.5, VIEW, 0, 0)
        self.editor.drag(ray_to(target), 0, 0)
        self.editor.end_drag()

    def test_an_elbow_cannot_be_dragged_backwards(self):
        self.drag_forearm_to(self.inverted_target())
        self.assertLess(self.elbow_bend(), 25.0)  # Refused: the arm stays extended.
        self.assertTrue(self.skeleton.at_limit(self.editor.pose, self.joint("forearm.L")))

    def test_turning_limits_off_allows_the_same_drag(self):
        target = self.inverted_target()
        self.skeleton.limits_enabled = False
        self.drag_forearm_to(target)
        self.assertGreater(self.elbow_bend(), 60.0)  # Bent the wrong way, as asked.

    def test_an_elbow_still_folds_the_natural_way(self):
        transforms = self.editor.transforms()
        forearm = transforms[self.joint("forearm.L")]
        wrist = transforms[self.joint("hand.L")].position
        hinge = forearm.rotation.rotate(Vec3(1.0, 0.0, 0.0))
        self.drag_forearm_to(forearm.position + Quat.from_axis_angle(hinge, 1.5).rotate(
            wrist - forearm.position))
        self.assertGreater(self.elbow_bend(), 60.0)

    def test_a_pose_never_holds_a_forbidden_rotation(self):
        for name in ("forearm.L", "shin.R", "head", "hand.L"):
            with self.subTest(joint=name):
                index = self.joint(name)
                self.editor.pose = self.skeleton.set_rotation(
                    self.skeleton.rest_pose(), index, Quat.from_axis_angle(Z_AXIS, 2.6))
                stored = self.editor.pose.rotations[index]
                self.assertTrue(clamp_rotation(self.skeleton.limits[index], stored)
                                .is_close(stored, 1e-9))


if __name__ == "__main__":
    unittest.main()
