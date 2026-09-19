"""Two-bone IK: reachable, unreachable, nearly straight, and degenerate cases."""

import random
import unittest

from krita_scene_poser.core.ik import REACH_LIMIT, solve_two_bone
from krita_scene_poser.core.math3d import Vec3

ROOT = Vec3(0, 0, 0)


def perpendicular_part(v, direction):
    return v - direction * v.dot(direction)


class TwoBoneTests(unittest.TestCase):
    def assert_lengths_kept(self, solution, root, upper, lower):
        self.assertAlmostEqual((solution.middle - root).length(), upper, places=9)
        self.assertAlmostEqual((solution.end - solution.middle).length(), lower, places=9)

    def test_reachable_target_is_hit_and_bends_toward_pole(self):
        middle, end = Vec3(1, 0, 0), Vec3(2, 0, 0)
        target, pole = Vec3(1, 1, 0), Vec3(0, 5, -3)
        solution = solve_two_bone(ROOT, middle, end, target, pole)
        self.assertTrue(solution.reached)
        self.assertTrue(solution.end.is_close(target))
        self.assert_lengths_kept(solution, ROOT, 1, 1)
        direction = target.normalized()
        bend = perpendicular_part(solution.middle, direction)
        self.assertGreater(bend.dot(perpendicular_part(pole, direction)), 0)
        # The elbow lies in the plane through root, target, and pole.
        normal = target.cross(pole)
        self.assertAlmostEqual(solution.middle.dot(normal), 0, places=9)

    def test_random_reachable_targets_with_unequal_bones(self):
        rng = random.Random(21)
        middle, end = Vec3(0, -0.45, 0.05), Vec3(0, -0.85, 0.0)  # Thigh and shin.
        upper, lower = (middle - ROOT).length(), (end - middle).length()
        for _ in range(200):
            direction = Vec3(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
            if direction.length() < 0.1:
                continue
            reach = rng.uniform(abs(upper - lower) + 0.01, upper + lower - 0.01)
            target = direction.normalized() * reach
            pole = Vec3(rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(-2, 2))
            solution = solve_two_bone(ROOT, middle, end, target, pole)
            self.assertTrue(solution.reached)
            self.assertTrue(solution.end.is_close(target, 1e-9))
            self.assert_lengths_kept(solution, ROOT, upper, lower)

    def test_unreachable_target_straightens_limb_toward_it(self):
        middle, end = Vec3(1, 0, 0), Vec3(1, -1, 0)
        target = Vec3(0, 10, 0)
        solution = solve_two_bone(ROOT, middle, end, target, Vec3(0, 0, 1))
        self.assertFalse(solution.reached)
        self.assertTrue(solution.end.is_close(Vec3(0, 2 * REACH_LIMIT, 0)))
        self.assert_lengths_kept(solution, ROOT, 1, 1)
        self.assertLess(perpendicular_part(solution.middle, Vec3(0, 1, 0)).length(), 0.03)

    def test_target_inside_minimum_reach_is_clamped(self):
        middle, end = Vec3(2, 0, 0), Vec3(2, 1, 0)  # Upper 2, lower 1.
        solution = solve_two_bone(ROOT, middle, end, Vec3(0.2, 0, 0), Vec3(0, 1, 0))
        self.assertFalse(solution.reached)
        self.assert_lengths_kept(solution, ROOT, 2, 1)
        self.assertAlmostEqual(solution.end.length(), 1, places=3)

    def test_nearly_straight_limb_keeps_its_bend_side(self):
        middle, end = Vec3(1, 0.001, 0), Vec3(2, 0, 0)  # Barely bent toward +Y.
        solution = solve_two_bone(ROOT, middle, end, Vec3(1.5, 0, 0))
        self.assertGreater(solution.middle.y, 0)
        self.assert_lengths_kept(solution, ROOT, (middle - ROOT).length(), (end - middle).length())

    def test_pole_on_the_limb_axis_falls_back_to_current_bend(self):
        middle, end = Vec3(1, 0, -1), Vec3(2, 0, 0)  # Bent toward -Z.
        solution = solve_two_bone(ROOT, middle, end, Vec3(1.8, 0, 0), pole=Vec3(5, 0, 0))
        self.assertLess(solution.middle.z, 0)

    def test_target_on_root_and_fully_straight_limb_stay_finite(self):
        for middle, end, target in ((Vec3(1, 0, 0), Vec3(2, 0, 0), ROOT),
                                    (Vec3(0, 1, 0), Vec3(0, 2, 0), Vec3(0, 1.5, 0))):
            solution = solve_two_bone(ROOT, middle, end, target)
            for value in (*solution.middle, *solution.end):
                self.assertEqual(value, value)  # Not NaN.
            self.assert_lengths_kept(solution, ROOT, 1, 1)

    def test_zero_length_bone_is_rejected(self):
        with self.assertRaises(ValueError):
            solve_two_bone(ROOT, ROOT, Vec3(1, 0, 0), Vec3(0, 1, 0))


if __name__ == "__main__":
    unittest.main()
