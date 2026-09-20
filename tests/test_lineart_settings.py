"""Line-art settings, uniforms, and depth range (the plan's test_lineart_settings)."""

import math
import unittest

from krita_scene_poser.core.camera import OrbitCamera
from krita_scene_poser.core.lineart import LIMITS, LineArtSettings, depth_range, line_uniforms
from krita_scene_poser.core.math3d import Vec3


class SettingsTests(unittest.TestCase):
    def test_defaults_are_black_lines_with_all_types_on(self):
        settings = LineArtSettings()
        self.assertEqual(settings.rgba(), (0.0, 0.0, 0.0, 1.0))
        self.assertTrue(all((settings.outline, settings.contours, settings.creases, settings.seams)))
        self.assertGreater(settings.outline_width, settings.inner_width)

    def test_opacity_becomes_the_line_alpha(self):
        self.assertEqual(LineArtSettings(color="#ff8000", opacity=0.5).rgba()[3], 0.5)
        self.assertEqual(LineArtSettings(opacity=4.0).validated().opacity, 1.0)
        self.assertEqual(LineArtSettings(opacity=-1.0).validated().opacity, 0.0)
        self.assertEqual(LineArtSettings(opacity="faint").validated().opacity, 1.0)
        # The uniforms carry it through to the edge shader unchanged.
        self.assertAlmostEqual(line_uniforms(LineArtSettings(opacity=0.25)).rgba[3], 0.25)

    def test_values_are_clamped_and_colors_validated(self):
        wild = LineArtSettings(color="red", outline_width=999, inner_width=-4,
                               crease_angle=float("nan"), depth_sensitivity=7, seams=0).validated()
        self.assertEqual(wild.color, "#000000")
        self.assertEqual(wild.outline_width, LIMITS["outline_width"][1])
        self.assertEqual(wild.inner_width, LIMITS["inner_width"][0])
        self.assertEqual(wild.crease_angle, LineArtSettings.crease_angle)
        self.assertEqual(wild.depth_sensitivity, 1.0)
        self.assertIs(wild.seams, False)
        self.assertEqual(LineArtSettings(color="#1A2B3C").validated().color, "#1a2b3c")

    def test_json_round_trip_and_damaged_input(self):
        custom = LineArtSettings(color="#336699", outline_width=5, creases=False)
        self.assertEqual(LineArtSettings.from_json(custom.to_json()), custom.validated())
        for damaged in ("", "{", "[1, 2]", "null", '{"outline_width": "wide", "bogus": 1}'):
            with self.subTest(damaged=damaged):
                loaded = LineArtSettings.from_json(damaged)
                self.assertEqual(loaded.outline_width, LineArtSettings.outline_width)
        partial = LineArtSettings.from_json('{"inner_width": 4, "seams": false}')
        self.assertEqual((partial.inner_width, partial.seams, partial.outline), (4.0, False, True))

    def test_depth_sensitivity_lowers_the_threshold(self):
        low = LineArtSettings(depth_sensitivity=0).depth_threshold()
        high = LineArtSettings(depth_sensitivity=1).depth_threshold()
        self.assertAlmostEqual(low, 0.2)
        self.assertAlmostEqual(high, 0.005)
        self.assertLess(LineArtSettings(depth_sensitivity=0.7).depth_threshold(),
                        LineArtSettings(depth_sensitivity=0.3).depth_threshold())


class UniformTests(unittest.TestCase):
    def test_radii_scale_with_render_size(self):
        settings = LineArtSettings(outline_width=4, inner_width=2)
        full = line_uniforms(settings)
        half = line_uniforms(settings, scale=0.5)
        self.assertEqual((full.outline_radius, full.inner_radius), (2.0, 1.0))
        self.assertEqual((half.outline_radius, half.inner_radius), (1.0, 0.5))
        self.assertEqual(line_uniforms(settings, scale=0.01).inner_radius, 0.5)  # Never vanishes.

    def test_crease_angle_and_toggles(self):
        uniforms = line_uniforms(LineArtSettings(crease_angle=60, contours=False, color="#ff0000"))
        self.assertAlmostEqual(uniforms.crease_cos, 0.5)
        self.assertEqual(uniforms.enabled, (1.0, 0.0, 1.0, 1.0))
        self.assertEqual(uniforms.rgba, (1.0, 0.0, 0.0, 1.0))


class DepthRangeTests(unittest.TestCase):
    def test_range_covers_the_points_in_both_projections(self):
        points = [Vec3(0, 0, 0), Vec3(0.3, 1.7, -0.2), Vec3(-0.4, 0.9, 0.3)]
        for orthographic in (False, True):
            camera = OrbitCamera(yaw=0.6, pitch=0.2, orthographic=orthographic)
            near, span = depth_range(points, camera)
            for p in points:
                depth = (p - camera.eye()).dot(camera.forward())
                normalized = (depth - near) / span
                self.assertTrue(0.0 < normalized < 1.0, normalized)
            if not orthographic:
                self.assertGreater(near, 0.0)

    def test_empty_points_still_give_a_usable_range(self):
        near, span = depth_range([], OrbitCamera())
        self.assertTrue(math.isfinite(near) and span > 0)


if __name__ == "__main__":
    unittest.main()
