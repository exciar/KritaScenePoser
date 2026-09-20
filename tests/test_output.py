"""Output size and quality settings: validation, plans, and limits."""

import unittest

from krita_scene_poser.core.output import (
    MAX_DIMENSION, MAX_PIXELS, OutputSettings, OutputSizeError,
)


class SettingsTests(unittest.TestCase):
    def test_defaults_follow_the_document_at_one_sample(self):
        settings = OutputSettings()
        self.assertEqual((settings.mode, settings.supersample, settings.anchor),
                         ("document", 1, "center"))
        plan = settings.resolve(800, 600)
        self.assertEqual((plan.width, plan.height), (800, 600))
        self.assertEqual((plan.render_width, plan.render_height), (800, 600))
        self.assertEqual(plan.origin, (0, 0))
        self.assertFalse(plan.scaled)
        self.assertAlmostEqual(plan.aspect, 800 / 600)

    def test_invalid_values_fall_back_instead_of_raising(self):
        settings = OutputSettings(mode="huge", width="x", height=-40, anchor="middle",
                                  supersample=3).validated()
        self.assertEqual(settings.mode, "document")
        self.assertEqual(settings.anchor, "center")
        self.assertEqual(settings.supersample, 1)
        self.assertEqual(settings.width, OutputSettings.width)
        self.assertEqual(settings.height, 1)  # Clamped to the smallest allowed.
        self.assertEqual(OutputSettings(width=99999).validated().width, MAX_DIMENSION)
        self.assertEqual(OutputSettings(width=True).validated().width, OutputSettings.width)

    def test_json_round_trip_and_damaged_input(self):
        settings = OutputSettings(mode="custom", width=700, height=500, anchor="topleft",
                                  supersample=2)
        self.assertEqual(OutputSettings.from_json(settings.to_json()), settings)
        for text in ("", "{", "[]", None, '{"mode": 4}'):
            with self.subTest(text=text):
                self.assertEqual(OutputSettings.from_json(text), OutputSettings())
        # Unknown keys from a newer KSP are ignored, not fatal.
        self.assertEqual(OutputSettings.from_json('{"width": 640, "margin": 3}').width, 640)


class PlanTests(unittest.TestCase):
    def test_a_custom_size_is_centered_or_anchored(self):
        settings = OutputSettings(mode="custom", width=400, height=300)
        self.assertEqual(settings.resolve(1000, 900).origin, (300, 300))
        self.assertEqual(settings.resolve(1000, 900).width, 400)
        anchored = OutputSettings(mode="custom", width=400, height=300, anchor="topleft")
        self.assertEqual(anchored.resolve(1000, 900).origin, (0, 0))

    def test_a_layer_larger_than_the_canvas_hangs_outside_it(self):
        plan = OutputSettings(mode="custom", width=1200, height=1000).resolve(800, 600)
        self.assertEqual(plan.origin, (-200, -200))

    def test_quality_multiplies_only_the_rendered_size(self):
        plan = OutputSettings(supersample=4).resolve(500, 400)
        self.assertEqual((plan.width, plan.height), (500, 400))
        self.assertEqual((plan.render_width, plan.render_height), (2000, 1600))
        self.assertTrue(plan.scaled)
        self.assertIn("4", plan.describe())

    def test_a_render_over_the_limit_is_refused_with_a_reason(self):
        for width, height, quality in ((3000, 2000, 2), (4096, 4096, 2), (2048, 2048, 4)):
            with self.subTest(size=(width, height), quality=quality):
                settings = OutputSettings(mode="custom", width=width, height=height,
                                          supersample=quality)
                with self.assertRaises(OutputSizeError) as caught:
                    settings.resolve(width, height)
                self.assertIn("quality", str(caught.exception))
        # The same size at 1x is fine.
        plan = OutputSettings(mode="custom", width=4096, height=4096).resolve(100, 100)
        self.assertEqual(plan.render_width * plan.render_height, MAX_PIXELS)

    def test_document_mode_ignores_the_stored_custom_size(self):
        settings = OutputSettings(width=123, height=456)
        plan = settings.resolve(640, 480)
        self.assertEqual((plan.width, plan.height), (640, 480))


if __name__ == "__main__":
    unittest.main()
