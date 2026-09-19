"""Orbit camera: placement, framing, panning, zoom, and picking rays."""

import math
import unittest

from krita_scene_poser.core.camera import MAX_DISTANCE, MIN_DISTANCE, PITCH_LIMIT, OrbitCamera
from krita_scene_poser.core.math3d import Vec3, project

WIDTH, HEIGHT = 800, 600


class CameraTests(unittest.TestCase):
    def test_default_view_faces_the_figure_front(self):
        camera = OrbitCamera(pitch=0.0)
        self.assertTrue(camera.eye().is_close(Vec3(0, 0.9, 3.0)))
        self.assertTrue(camera.view().transform_point(camera.target).is_close(Vec3(0, 0, -3.0)))
        self.assertTrue(camera.forward().is_close(Vec3(0, 0, -1)))
        self.assertTrue(camera.right().is_close(Vec3(1, 0, 0)))
        camera.yaw = math.pi / 2
        self.assertTrue(camera.eye().is_close(Vec3(3.0, 0.9, 0)))

    def test_target_projects_to_screen_center_in_both_projections(self):
        for orthographic in (False, True):
            camera = OrbitCamera(yaw=0.7, pitch=0.4, orthographic=orthographic)
            x, y, _ = project(camera.target, camera.view_projection(WIDTH / HEIGHT), WIDTH, HEIGHT)
            self.assertAlmostEqual(x, WIDTH / 2, places=6)
            self.assertAlmostEqual(y, HEIGHT / 2, places=6)

    def test_ray_through_center_hits_the_target(self):
        for orthographic in (False, True):
            camera = OrbitCamera(yaw=-0.4, pitch=0.3, orthographic=orthographic)
            ray = camera.ray(WIDTH / 2, HEIGHT / 2, WIDTH, HEIGHT)
            self.assertTrue(ray.direction.is_close(camera.forward(), 1e-9))
            offset = camera.target - ray.origin
            self.assertLess((offset - ray.direction * offset.dot(ray.direction)).length(), 1e-9)

    def test_frame_fits_points_inside_the_view(self):
        camera = OrbitCamera(yaw=0.3, pitch=0.2)
        points = [Vec3(-0.9, 0, -0.3), Vec3(0.9, 1.75, 0.3), Vec3(0.1, 0.9, 0.2)]
        camera.frame(points)
        matrix = camera.view_projection(1.0)
        for point in points:
            x, y, _ = project(point, matrix, 100, 100)
            self.assertTrue(0 <= x <= 100 and 0 <= y <= 100, (x, y))
        self.assertTrue(camera.target.is_close(Vec3(0, 0.875, 0)))

    def test_pan_keeps_the_target_plane_under_the_cursor(self):
        for orthographic in (False, True):
            camera = OrbitCamera(yaw=0.5, pitch=0.25, orthographic=orthographic)
            anchor = camera.target + camera.right() * 0.2
            before = project(anchor, camera.view_projection(WIDTH / HEIGHT), WIDTH, HEIGHT)
            camera.pan(30, -20, HEIGHT)
            after = project(anchor, camera.view_projection(WIDTH / HEIGHT), WIDTH, HEIGHT)
            self.assertAlmostEqual(after[0] - before[0], 30, delta=0.5)
            self.assertAlmostEqual(after[1] - before[1], -20, delta=0.5)

    def test_world_per_pixel_matches_projection(self):
        camera = OrbitCamera(pitch=0.0)
        point = Vec3(0, 0.9, -1.0)
        step = camera.world_per_pixel(point, HEIGHT)
        matrix = camera.view_projection(WIDTH / HEIGHT)
        a = project(point, matrix, WIDTH, HEIGHT)
        b = project(point + Vec3(0, step * 10, 0), matrix, WIDTH, HEIGHT)
        self.assertAlmostEqual(a[1] - b[1], 10, delta=1e-6)

    def test_orbit_clamps_pitch_and_zoom_clamps_distance(self):
        camera = OrbitCamera()
        camera.orbit(0, 10000)
        self.assertAlmostEqual(camera.pitch, PITCH_LIMIT)
        camera.orbit(0, -20000)
        self.assertAlmostEqual(camera.pitch, -PITCH_LIMIT)
        camera.orbit(100, 0)
        self.assertLess(camera.yaw, 0)
        camera.zoom(1000)
        self.assertEqual(camera.distance, MIN_DISTANCE)
        camera.zoom(-1000)
        self.assertEqual(camera.distance, MAX_DISTANCE)

    def test_copy_is_independent(self):
        camera = OrbitCamera()
        copy = camera.copy()
        copy.orbit(50, 50)
        self.assertNotEqual(camera.yaw, copy.yaw)


if __name__ == "__main__":
    unittest.main()
