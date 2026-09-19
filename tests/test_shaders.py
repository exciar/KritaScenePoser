"""Figure shader sources and snapshot packing (no GPU needed)."""

import unittest

from krita_scene_poser.core.math3d import Quat, Vec3, Z_AXIS
from krita_scene_poser.core.picking import skin_point
from krita_scene_poser.render.shaders import (
    BONE_ROWS, FIGURE_FRAGMENT, FIGURE_VERTEX, GRID_FRAGMENT, bone_rows, grid_lines, snapshot, translate,
)
from krita_scene_poser.storage.figures import load_figure


class ShaderTests(unittest.TestCase):
    def test_dialects_use_their_own_keywords(self):
        self.assertTrue(translate(FIGURE_VERTEX, "GLSL ES 1.00", "vertex").startswith("#version 100\n"))
        desktop = translate(FIGURE_FRAGMENT, "GLSL 1.20", "fragment")
        self.assertTrue(desktop.startswith("#version 120\n"))
        self.assertNotIn("precision", desktop)
        core_vertex = translate(FIGURE_VERTEX, "GLSL 1.50 core", "vertex")
        core_fragment = translate(GRID_FRAGMENT, "GLSL 1.50 core", "fragment")
        self.assertTrue(core_vertex.startswith("#version 150\n"))
        self.assertNotIn("attribute ", core_vertex)
        self.assertNotIn("varying ", core_vertex)
        self.assertIn("in vec3 a_position;", core_vertex)
        self.assertIn("out vec4 outputColor;", core_fragment)
        self.assertNotIn("gl_FragColor", core_fragment)
        self.assertIn("uniform vec4 u_bones[{}];".format(BONE_ROWS), FIGURE_VERTEX)

    def test_bone_rows_skin_exactly_like_the_cpu(self):
        rig, mesh = load_figure("body_kun")
        skeleton = rig.skeleton
        pose = skeleton.rotate_world(skeleton.rest_pose(), skeleton.index("forearm.L"),
                                     Quat.from_axis_angle(Z_AXIS, 0.9))
        matrices = skeleton.skinning_matrices(pose)
        rows = bone_rows(matrices)
        self.assertEqual(len(rows), 12 * len(matrices))
        for vertex in range(0, mesh.vertex_count, 997):
            p = (mesh.positions[3 * vertex], mesh.positions[3 * vertex + 1], mesh.positions[3 * vertex + 2], 1.0)
            gpu = [0.0, 0.0, 0.0]
            for slot in range(4):
                joint, weight = mesh.joints[4 * vertex + slot], mesh.weights[4 * vertex + slot] / 255.0
                for axis in range(3):
                    row = rows[12 * joint + 4 * axis:12 * joint + 4 * axis + 4]
                    gpu[axis] += weight * sum(a * b for a, b in zip(row, p))
            cpu = skin_point(matrices, mesh.joints, mesh.weights, mesh.positions, vertex)
            self.assertTrue(Vec3(*gpu).is_close(Vec3(*cpu), 1e-9))

    def test_snapshot_and_grid(self):
        rig, _ = load_figure("body_chan")
        from krita_scene_poser.core.camera import OrbitCamera
        shot = snapshot(rig.skeleton, rig.skeleton.rest_pose(), OrbitCamera().view_projection(1.5),
                        selected=None)
        self.assertEqual((shot.selected, shot.grid, len(shot.view_projection)), (-1, True, 16))
        points = grid_lines(extent=1.0, step=0.5)
        self.assertEqual(len(points), 5 * 12)
        self.assertTrue(all(points[i] == 0.0 for i in range(1, len(points), 3)))  # On y = 0.


if __name__ == "__main__":
    unittest.main()
