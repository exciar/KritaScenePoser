"""Shared pointer interaction: gestures, camera drags, scale invariance, overlay."""

import math
import unittest

from krita_scene_poser.core.camera import OrbitCamera
from krita_scene_poser.core.editor import RINGS, PoseEditor
from krita_scene_poser.core.interaction import (
    PICK_PIXELS, RING_PIXELS, PoseInteraction, Screen,
)
from krita_scene_poser.core.math3d import project
from krita_scene_poser.core.picking import FigurePicker
from krita_scene_poser.storage.figures import load_figure

VIEWPORT = Screen(1000, 1000, 1.0)
DOCUMENT = Screen(2000, 2000, 2.0)  # Canvas at 50 %: one display pixel spans two document pixels.


class InteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rig, mesh = load_figure("body_chan")
        cls.skeleton, cls.picker = rig.skeleton, FigurePicker(rig, mesh)

    def make(self):
        camera = OrbitCamera(pitch=0.1)
        editor = PoseEditor(self.skeleton, self.picker)
        camera.frame([p for segment in editor.segments() for p in segment])
        return PoseInteraction(editor, camera)

    def screen_point(self, interaction, name, screen):
        start, end = interaction.editor.segments()[self.skeleton.index(name)]
        x, y, _ = project((start + end) * 0.5, interaction.view_projection(screen),
                          screen.width, screen.height)
        return x, y

    def test_press_on_the_figure_starts_the_right_gesture(self):
        interaction = self.make()
        x, y = self.screen_point(interaction, "forearm.L", VIEWPORT)
        self.assertEqual(interaction.press(x, y, VIEWPORT), "pose")
        self.assertEqual(interaction.editor.gesture.kind, "aim")
        self.assertEqual(interaction.editor.joint_name(interaction.editor.selected), "forearm.L")
        interaction.move(x + 40, y - 60, VIEWPORT)
        self.assertTrue(interaction.release())  # One undo entry.
        self.assertTrue(interaction.editor.history.can_undo)

    def test_empty_space_navigates_the_camera_only_when_asked(self):
        interaction = self.make()
        self.assertIsNone(interaction.press(5, 5, VIEWPORT))
        for modifiers, kind in (({}, "orbit"), ({"shift": True}, "pan"), ({"ctrl": True}, "dolly")):
            with self.subTest(kind):
                self.assertEqual(interaction.press(5, 5, VIEWPORT, navigate_on_empty=True, **modifiers), kind)
                interaction.release()
        before = interaction.camera.distance
        interaction.press(5, 500, VIEWPORT, ctrl=True, navigate_on_empty=True)
        interaction.move(5, 400, VIEWPORT)  # Drag up.
        self.assertLess(interaction.camera.distance, before)
        self.assertFalse(interaction.release())  # Camera moves are not pose undo steps.

    def test_same_gesture_gives_the_same_pose_on_viewport_and_canvas(self):
        results = []
        for screen in (VIEWPORT, DOCUMENT):
            interaction = self.make()
            x, y = self.screen_point(interaction, "shin.R", screen)
            interaction.press(x, y, screen)
            s = screen.pixel_scale
            interaction.move(x + 70 * s, y - 30 * s, screen)
            interaction.release()
            x, y = self.screen_point(interaction, "upper_arm.L", screen)
            interaction.press(x, y, screen, shift=True)  # Twist: speed in display pixels.
            interaction.move(x + 50 * s, y, screen)
            interaction.release()
            results.append(interaction.editor.pose)
        for a, b in zip(results[0].rotations, results[1].rotations):
            self.assertTrue(a.is_close(b, 1e-9))

    def test_pick_tolerance_and_rings_are_constant_on_the_display(self):
        interaction = self.make()
        editor = interaction.editor
        editor.select(self.skeleton.index("upper_arm.L"))
        _, _, viewport_radius = interaction.rings(VIEWPORT)
        _, _, canvas_radius = interaction.rings(DOCUMENT)
        self.assertAlmostEqual(viewport_radius, canvas_radius)
        pivot = editor.transforms()[editor.selected].position
        camera = interaction.camera
        self.assertAlmostEqual(camera.world_per_pixel(pivot, VIEWPORT.height) * RING_PIXELS,
                               viewport_radius)
        self.assertAlmostEqual(camera.world_per_pixel(pivot, VIEWPORT.height) * PICK_PIXELS,
                               camera.world_per_pixel(pivot, DOCUMENT.height) * PICK_PIXELS * 2)

    def test_rings_rotate_identically_at_any_scale(self):
        results = []
        for screen in (VIEWPORT, DOCUMENT):
            interaction = self.make()
            editor = interaction.editor
            editor.mode = RINGS
            x, y = self.screen_point(interaction, "forearm.L", screen)
            self.assertEqual(interaction.press(x, y, screen), "select")
            ring = next(p for p in interaction.overlay(screen) if p.kind == "segment" and p.front)
            gx, gy = ring.a
            self.assertEqual(interaction.press(gx, gy, screen), "ring")
            s = screen.pixel_scale
            interaction.move(gx + 25 * s, gy + 10 * s, screen)
            self.assertTrue(interaction.release())
            results.append(editor.pose)
        for a, b in zip(results[0].rotations, results[1].rotations):
            self.assertTrue(a.is_close(b, 1e-6))

    def test_cancel_restores_the_pose(self):
        interaction = self.make()
        start = interaction.editor.pose
        x, y = self.screen_point(interaction, "thigh.L", VIEWPORT)
        interaction.press(x, y, VIEWPORT)
        interaction.move(x + 80, y, VIEWPORT)
        self.assertTrue(interaction.cancel())
        self.assertIs(interaction.editor.pose, start)
        self.assertIsNone(interaction.drag)

    def test_overlay_primitives(self):
        interaction = self.make()
        editor = interaction.editor
        self.assertEqual(interaction.overlay(VIEWPORT), [])
        editor.select(self.skeleton.index("hand.R"))
        kinds = [p.kind for p in interaction.overlay(VIEWPORT)]
        self.assertEqual(kinds, ["line", "circle", "circle"])  # Bone, pivot, IK marker.
        editor.mode = RINGS
        segments = [p for p in interaction.overlay(VIEWPORT) if p.kind == "segment"]
        self.assertEqual({p.axis for p in segments}, {0, 1, 2})
        self.assertTrue(any(p.front for p in segments) and any(not p.front for p in segments))


if __name__ == "__main__":
    unittest.main()
