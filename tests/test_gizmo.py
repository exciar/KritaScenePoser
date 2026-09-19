"""Rotation-ring geometry, picking by screen distance, and angles."""

import math
import unittest

from krita_scene_poser.core.gizmo import (
    distance_to_ring, is_edge_on, joint_axes, perpendicular_basis, pick_ring, ring_points,
    screen_tangent, signed_angle,
)
from krita_scene_poser.core.math3d import IDENTITY, Quat, Vec3, X_AXIS, Y_AXIS, Z_AXIS


def front(point):
    """Orthographic front view: 100 px per meter, screen y down."""
    return (point.x * 100.0, -point.y * 100.0)


class GizmoTests(unittest.TestCase):
    def test_ring_points_circle_the_axis_counter_clockwise(self):
        pivot = Vec3(1, 2, 3)
        for axis in (X_AXIS, Y_AXIS, Vec3(0.3, 0.8, -0.5).normalized()):
            points = ring_points(pivot, axis, 0.5, segments=8)
            for p in points:
                self.assertAlmostEqual((p - pivot).length(), 0.5)
                self.assertAlmostEqual((p - pivot).dot(axis), 0.0)
            first, quarter = points[0] - pivot, points[2] - pivot
            self.assertAlmostEqual(signed_angle(first, quarter, axis), math.pi / 2)

    def test_joint_axes_follow_the_rotation(self):
        axes = joint_axes(Quat.from_axis_angle(Z_AXIS, math.pi / 2))
        self.assertTrue(axes[0].is_close(Y_AXIS))
        self.assertTrue(axes[1].is_close(-X_AXIS))
        self.assertTrue(joint_axes(IDENTITY)[2].is_close(Z_AXIS))

    def test_ring_picking_by_screen_distance(self):
        rings = [[front(p) for p in ring_points(Vec3(0, 0, 0), Z_AXIS, r)] for r in (0.5, 1.0)]
        self.assertEqual(pick_ring((100.0, 3.0), rings, 8)[0], 1)
        self.assertEqual(pick_ring((0.0, -52.0), rings, 8)[0], 0)
        self.assertIsNone(pick_ring((75.0, 0.0), rings, 8))
        u, v = perpendicular_basis(Z_AXIS)
        quarter = front(v)  # Parameter 0.25 is the basis' v direction.
        distance, where = distance_to_ring(quarter, rings[1])
        self.assertAlmostEqual(distance, 0.0, places=6)
        self.assertAlmostEqual(where, 0.25, places=6)
        self.assertEqual(distance_to_ring((0, 0), [None] * 4)[0], float("inf"))

    def test_screen_tangent_points_toward_increasing_angle(self):
        u, v = perpendicular_basis(Z_AXIS)
        tangent = screen_tangent(front, Vec3(0, 0, 0), Z_AXIS, 1.0, 0.0)
        expected = front(v)  # At angle 0 the ring moves toward v.
        length = math.hypot(*expected)
        self.assertAlmostEqual(tangent[0], expected[0] / length, places=4)
        self.assertAlmostEqual(tangent[1], expected[1] / length, places=4)
        self.assertIsNone(screen_tangent(lambda p: None, Vec3(0, 0, 0), Z_AXIS, 1.0, 0.0))

    def test_signed_angle_and_edge_on(self):
        self.assertAlmostEqual(signed_angle(X_AXIS, Y_AXIS, Z_AXIS), math.pi / 2)
        self.assertAlmostEqual(signed_angle(Y_AXIS, X_AXIS, Z_AXIS), -math.pi / 2)
        self.assertTrue(is_edge_on(X_AXIS, Vec3(0, 0, -1)))
        self.assertFalse(is_edge_on(Z_AXIS, Vec3(0, 0, -1)))


if __name__ == "__main__":
    unittest.main()
