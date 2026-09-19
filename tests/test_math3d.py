"""Deterministic fixtures and round trips for core.math3d."""

import math
import random
import unittest

from krita_scene_poser.core.math3d import (
    IDENTITY, X_AXIS, Y_AXIS, Z_AXIS, ZERO, Mat4, Quat, Ray, Vec3,
    project, ray_from_screen, unproject,
)


def random_rotation(rng):
    axis = Vec3(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1) + 2.0)
    return Quat.from_axis_angle(axis, rng.uniform(-math.pi, math.pi))


class VectorTests(unittest.TestCase):
    def test_arithmetic_and_right_handed_cross(self):
        a, b = Vec3(1, 2, 3), Vec3(4, -5, 6)
        self.assertEqual(a + b, Vec3(5, -3, 9))
        self.assertEqual(a - b, Vec3(-3, 7, -3))
        self.assertEqual(2 * a, Vec3(2, 4, 6))
        self.assertEqual(a.dot(b), 12)
        self.assertEqual(X_AXIS.cross(Y_AXIS), Z_AXIS)
        self.assertAlmostEqual(Vec3(3, 4, 0).length(), 5)

    def test_zero_vector_cannot_be_normalized(self):
        with self.assertRaises(ValueError):
            ZERO.normalized()


class QuaternionTests(unittest.TestCase):
    def test_quarter_turns_follow_the_right_hand_rule(self):
        quarter = math.pi / 2
        self.assertTrue(Quat.from_axis_angle(Z_AXIS, quarter).rotate(X_AXIS).is_close(Y_AXIS))
        self.assertTrue(Quat.from_axis_angle(X_AXIS, quarter).rotate(Y_AXIS).is_close(Z_AXIS))
        self.assertTrue(Quat.from_axis_angle(Y_AXIS, quarter).rotate(Z_AXIS).is_close(X_AXIS))

    def test_composition_order_and_inverse(self):
        rng = random.Random(7)
        for _ in range(20):
            a, b = random_rotation(rng), random_rotation(rng)
            v = Vec3(rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(-2, 2))
            self.assertTrue((a * b).rotate(v).is_close(a.rotate(b.rotate(v))))
            self.assertTrue((a * a.inverse()).is_close(IDENTITY))
            self.assertAlmostEqual(a.rotate(v).length(), v.length())

    def test_matrix_round_trip_including_scale_and_sign(self):
        rng = random.Random(11)
        for _ in range(50):
            q = random_rotation(rng)
            matrix = Mat4.from_trs(Vec3(1, 2, 3), q, Vec3(2, 0.5, 3))
            self.assertTrue(Quat.from_matrix(matrix).is_close(q))
        half_turns = [Quat.from_axis_angle(axis, math.pi) for axis in (X_AXIS, Y_AXIS, Z_AXIS)]
        for q in half_turns:
            self.assertTrue(Quat.from_matrix(Mat4.from_trs(rotation=q)).is_close(q))

    def test_between_directions_including_opposites(self):
        for source, target in ((X_AXIS, Y_AXIS), (Vec3(1, 1, 0), Vec3(0, 0, 3)),
                               (X_AXIS, X_AXIS), (X_AXIS, -X_AXIS), (Y_AXIS, -Y_AXIS)):
            with self.subTest(source=source, target=target):
                rotated = Quat.between(source, target).rotate(source.normalized())
                self.assertTrue(rotated.is_close(target.normalized()))

    def test_slerp_endpoints_midpoint_and_short_path(self):
        a = IDENTITY
        b = Quat.from_axis_angle(Z_AXIS, math.pi / 2)
        self.assertTrue(a.slerp(b, 0).is_close(a))
        self.assertTrue(a.slerp(b, 1).is_close(b))
        self.assertTrue(a.slerp(b, 0.5).is_close(Quat.from_axis_angle(Z_AXIS, math.pi / 4)))
        # -b is the same rotation; slerp must still take the 45-degree path.
        self.assertTrue(a.slerp(-b, 0.5).is_close(Quat.from_axis_angle(Z_AXIS, math.pi / 4)))
        self.assertAlmostEqual(b.angle(), math.pi / 2)


class MatrixTests(unittest.TestCase):
    def test_trs_matches_component_application(self):
        rng = random.Random(3)
        for _ in range(20):
            t = Vec3(rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-5, 5))
            q, s = random_rotation(rng), rng.uniform(0.5, 2)
            p = Vec3(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
            expected = t + q.rotate(p * s)
            self.assertTrue(Mat4.from_trs(t, q, s).transform_point(p).is_close(expected))
            self.assertTrue(Mat4.from_trs(t, q, s).transform_vector(p).is_close(q.rotate(p * s)))

    def test_product_is_composition_and_column_major(self):
        a = Mat4.translation(Vec3(1, 0, 0))
        b = Mat4.from_trs(rotation=Quat.from_axis_angle(Z_AXIS, math.pi / 2))
        # Rotate first, then translate: X -> Y -> (1, 1, 0).
        self.assertTrue((a @ b).transform_point(X_AXIS).is_close(Vec3(1, 1, 0)))
        self.assertEqual(a.m[12:15], (1.0, 0.0, 0.0))
        self.assertEqual(a.at(0, 3), 1.0)
        self.assertEqual((Mat4.identity() @ b), b)

    def test_inverse_of_affine_and_projection_matrices(self):
        rng = random.Random(5)
        matrices = [Mat4.from_trs(Vec3(1, -2, 3), random_rotation(rng), Vec3(2, 3, 0.5)),
                    Mat4.perspective(math.radians(50), 1.5, 0.1, 100),
                    Mat4.orthographic(-2, 3, -1, 4, 0.5, 20)]
        for matrix in matrices:
            self.assertTrue((matrix @ matrix.inverse()).is_close(Mat4.identity(), 1e-9))
        with self.assertRaises(ValueError):
            Mat4.from_trs(scale=Vec3(1, 0, 1)).inverse()
        self.assertEqual(matrices[0].transposed().transposed(), matrices[0])

    def test_perspective_maps_near_and_far_planes_to_ndc(self):
        projection = Mat4.perspective(math.radians(60), 2.0, 1.0, 10.0)
        for depth, expected in ((1.0, -1.0), (10.0, 1.0)):
            x, y, z, w = projection.transform4(0, 0, -depth, 1)
            self.assertAlmostEqual(z / w, expected)
        # Top edge of the view at the near plane is y = tan(30 degrees).
        x, y, z, w = projection.transform4(0, math.tan(math.radians(30)), -1, 1)
        self.assertAlmostEqual(y / w, 1.0)
        with self.assertRaises(ValueError):
            Mat4.perspective(0, 1, 1, 10)

    def test_orthographic_maps_box_corners(self):
        projection = Mat4.orthographic(-2, 4, -1, 3, 1, 5)
        self.assertTrue(projection.transform_point(Vec3(-2, -1, -1)).is_close(Vec3(-1, -1, -1)))
        self.assertTrue(projection.transform_point(Vec3(4, 3, -5)).is_close(Vec3(1, 1, 1)))

    def test_look_at_puts_eye_at_origin_and_target_down_negative_z(self):
        eye, target = Vec3(3, 2, 5), Vec3(0, 1, 0)
        view = Mat4.look_at(eye, target)
        self.assertTrue(view.transform_point(eye).is_close(ZERO))
        seen = view.transform_point(target)
        self.assertTrue(Vec3(0, 0, -(target - eye).length()).is_close(seen))
        with self.assertRaises(ValueError):
            Mat4.look_at(ZERO, Vec3(0, 5, 0))


class ProjectionAndRayTests(unittest.TestCase):
    def setUp(self):
        self.view = Mat4.look_at(Vec3(0, 1, 5), Vec3(0, 1, 0))
        self.view_projection = Mat4.perspective(math.radians(45), 1.6, 0.1, 50) @ self.view
        self.inverse = self.view_projection.inverse()

    def test_project_unproject_round_trip_and_screen_orientation(self):
        for point in (Vec3(0, 1, 0), Vec3(0.7, 1.8, -1), Vec3(-1, 0.2, 2)):
            x, y, depth = project(point, self.view_projection, 800, 500)
            self.assertTrue(unproject(x, y, depth, self.inverse, 800, 500).is_close(point, 1e-6))
        cx, cy, _ = project(Vec3(0, 1, 0), self.view_projection, 800, 500)
        self.assertAlmostEqual(cx, 400)
        self.assertAlmostEqual(cy, 250)
        higher = project(Vec3(0, 1.5, 0), self.view_projection, 800, 500)
        self.assertLess(higher[1], cy)  # Screen y grows downward.
        self.assertIsNone(project(Vec3(0, 1, 10), self.view_projection, 800, 500))

    def test_screen_ray_through_center_follows_the_view_direction(self):
        ray = ray_from_screen(400, 250, self.inverse, 800, 500)
        self.assertTrue(ray.direction.is_close(Vec3(0, 0, -1), 1e-6))
        t = ray.intersect_plane(Vec3(0, 0, 0), Z_AXIS)
        self.assertTrue(ray.point_at(t).is_close(Vec3(0, 1, 0), 1e-6))

    def test_plane_and_sphere_hits_misses_and_parallel_rays(self):
        ray = Ray(Vec3(0, 0, 5), Vec3(0, 0, -1))
        self.assertAlmostEqual(ray.intersect_plane(ZERO, Z_AXIS), 5)
        self.assertIsNone(ray.intersect_plane(ZERO, X_AXIS))  # Parallel.
        self.assertIsNone(Ray(Vec3(0, 0, 5), Z_AXIS).intersect_plane(ZERO, Z_AXIS))  # Behind.
        self.assertAlmostEqual(ray.intersect_sphere(ZERO, 1), 4)
        self.assertAlmostEqual(Ray(ZERO, Z_AXIS).intersect_sphere(ZERO, 2), 2)  # Inside.
        self.assertIsNone(ray.intersect_sphere(Vec3(3, 0, 0), 1))


if __name__ == "__main__":
    unittest.main()
