"""Capsule picking: geometry, nearest hit, and the real figures."""

import math
import time
import unittest

from krita_scene_poser.core.camera import OrbitCamera
from krita_scene_poser.core.math3d import Quat, Ray, Vec3, Z_AXIS, project
from krita_scene_poser.core.picking import (
    FigurePicker, JointCapsules, ray_capsule, ray_triangle, segment_distance, skin_point,
)
from krita_scene_poser.core.skeleton import Joint, Skeleton
from krita_scene_poser.storage.figures import load_figure

A, B = Vec3(0, 0, 0), Vec3(0, 1, 0)


class CapsuleGeometryTests(unittest.TestCase):
    def test_body_cap_and_axis_aligned_hits(self):
        self.assertAlmostEqual(ray_capsule(Vec3(0, 0.5, 5), Vec3(0, 0, -1), A, B, 0.1), 4.9)
        cap = math.sqrt(0.01 - 0.05 ** 2)
        self.assertAlmostEqual(ray_capsule(Vec3(0, 1.05, 5), Vec3(0, 0, -1), A, B, 0.1), 5 - cap)
        self.assertAlmostEqual(ray_capsule(Vec3(0, 5, 0), Vec3(0, -1, 0), A, B, 0.1), 3.9)
        self.assertAlmostEqual(ray_capsule(Vec3(0, -5, 0), Vec3(0, 1, 0), A, B, 0.1), 4.9)

    def test_misses_and_hits_behind_the_origin(self):
        self.assertIsNone(ray_capsule(Vec3(0.2, 0.5, 5), Vec3(0, 0, -1), A, B, 0.1))
        self.assertIsNone(ray_capsule(Vec3(0, 0.5, -5), Vec3(0, 0, -1), A, B, 0.1))
        self.assertIsNone(ray_capsule(Vec3(0, 1.2, 5), Vec3(0, 0, -1), A, B, 0.1))

    def test_ray_triangle_front_back_and_outside(self):
        a, b, c = (0, 0, 0), (1, 0, 0), (0, 1, 0)
        self.assertAlmostEqual(ray_triangle((0.2, 0.2, 5), (0, 0, -1), a, b, c), 5)
        self.assertAlmostEqual(ray_triangle((0.2, 0.2, -5), (0, 0, 1), a, b, c), 5)  # Back side.
        self.assertIsNone(ray_triangle((0.8, 0.8, 5), (0, 0, -1), a, b, c))
        self.assertIsNone(ray_triangle((0.2, 0.2, 5), (1, 0, 0), a, b, c))  # Parallel.
        self.assertIsNone(ray_triangle((0.2, 0.2, -5), (0, 0, -1), a, b, c))  # Behind.

    def test_segment_distance(self):
        self.assertAlmostEqual(segment_distance(Vec3(1, 0.5, 0), A, B), 1)
        self.assertAlmostEqual(segment_distance(Vec3(0, 3, 0), A, B), 2)
        self.assertAlmostEqual(segment_distance(Vec3(0, -1, 0), A, A), 1)

    def test_nearest_capsule_and_minimum_radius(self):
        skeleton = Skeleton([Joint("far", -1, Vec3(0, 0, -1)), Joint("near", 0, Vec3(0, 0, 1))])
        capsules = JointCapsules([Vec3(0, 1, 0), Vec3(0, 1, 0)], [0.1, 0.1])
        ray = Ray(Vec3(0, 0.5, 5), Vec3(0, 0, -1))
        self.assertEqual(capsules.pick(ray, skeleton, skeleton.rest_pose()).joint, 1)
        wide = Ray(Vec3(0.3, 0.5, 5), Vec3(0, 0, -1))
        self.assertIsNone(capsules.pick(wide, skeleton, skeleton.rest_pose()))
        hit = capsules.pick(wide, skeleton, skeleton.rest_pose(), minimum_radius=lambda p: 0.5)
        self.assertEqual(hit.joint, 1)
        self.assertTrue(hit.point.is_close(wide.point_at(hit.distance)))


class FigurePickingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig, cls.mesh = load_figure("body_chan")
        cls.picker = FigurePicker(cls.rig, cls.mesh)
        cls.capsules = cls.picker.capsules
        cls.skeleton = cls.rig.skeleton
        cls.camera = OrbitCamera(pitch=0.0)
        cls.camera.frame([j.position for j in cls.rig.joints])

    def ray_through(self, point):
        x, y, _ = project(point, self.camera.view_projection(1.0), 600, 600)
        return self.camera.ray(x, y, 600, 600)

    def midpoint(self, pose, name):
        start, end = self.capsules.segments(self.skeleton, pose)[self.skeleton.index(name)]
        return (start + end) * 0.5

    def test_radii_follow_body_proportions(self):
        radius = {j.name: r for j, r in zip(self.rig.joints, self.capsules.radii)}
        self.assertGreater(radius["torso"], radius["forearm.L"])
        self.assertGreater(radius["forearm.L"], radius["index.02.L"])
        self.assertTrue(all(r >= 0.008 for r in self.capsules.radii))

    def test_front_view_picks_the_part_under_the_cursor(self):
        rest = self.skeleton.rest_pose()
        for name in ("head", "forearm.L", "upper_arm.R", "thigh.L", "shin.R", "torso"):
            with self.subTest(name):
                hit = self.picker.pick(self.ray_through(self.midpoint(rest, name)), self.skeleton, rest)
                self.assertEqual(self.rig.joints[hit.joint].name, name)

    def test_picking_follows_the_posed_figure(self):
        upper = self.skeleton.index("upper_arm.L")
        pose = self.skeleton.rotate_world(self.skeleton.rest_pose(), upper,
                                          Quat.from_axis_angle(Z_AXIS, 1.2))
        hit = self.picker.pick(self.ray_through(self.midpoint(pose, "forearm.L")), self.skeleton, pose)
        self.assertEqual(self.rig.joints[hit.joint].name, "forearm.L")
        old_place = self.ray_through(self.midpoint(self.skeleton.rest_pose(), "forearm.L"))
        moved = self.picker.pick(old_place, self.skeleton, pose)
        self.assertTrue(moved is None or self.rig.joints[moved.joint].name != "forearm.L")

    def brute_force(self, ray, pose):
        """Every triangle of the fully skinned mesh; the reference answer."""
        mesh, matrices = self.mesh, self.skeleton.skinning_matrices(pose)
        points = [skin_point(matrices, mesh.joints, mesh.weights, mesh.positions, v)
                  for v in range(mesh.vertex_count)]
        best = None
        for start in range(0, len(mesh.indices), 3):
            corners = [points[v] for v in mesh.indices[start:start + 3]]
            t = ray_triangle(tuple(ray.origin), tuple(ray.direction), *corners)
            if t is not None and (best is None or t < best[0]):
                best = (t, mesh.joints[4 * mesh.indices[start]])
        return best

    def test_surface_pick_matches_brute_force_and_is_fast(self):
        upper = self.skeleton.index("upper_arm.R")
        posed = self.skeleton.rotate_world(self.skeleton.rest_pose(), upper,
                                           Quat.from_axis_angle(Z_AXIS, -1.0))
        slowest = 0.0
        for pose in (self.skeleton.rest_pose(), posed):
            for x in range(170, 440, 45):
                for y in range(40, 580, 60):
                    ray = self.camera.ray(x, y, 600, 600)
                    expected = self.brute_force(ray, pose)
                    start = time.perf_counter()
                    hit = self.picker.pick(ray, self.skeleton, pose)
                    slowest = max(slowest, time.perf_counter() - start)
                    with self.subTest(x=x, y=y, posed=pose is posed):
                        if expected is None:
                            self.assertTrue(hit is None or hit.distance > 0)  # Capsule fallback only.
                        else:
                            self.assertEqual(hit.joint, expected[1])
                            self.assertAlmostEqual(hit.distance, expected[0], places=9)
        self.assertLess(slowest, 0.25, "a single pick should feel instant")


if __name__ == "__main__":
    unittest.main()
