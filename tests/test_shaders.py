"""Figure shader sources and snapshot packing (no GPU needed)."""

import math
import re
import unittest

from krita_scene_poser.core.camera import OrbitCamera
from krita_scene_poser.core.math3d import Quat, Vec3, Z_AXIS
from krita_scene_poser.core.picking import skin_point
from krita_scene_poser.render.shaders import (
    BONE_ROWS, EDGE_FRAGMENT, FIGURE_FRAGMENT, FIGURE_VERTEX, GBUFFER_DEPTH_FRAGMENT,
    GBUFFER_NORMAL_FRAGMENT, GBUFFER_VERTEX, GRID_FRAGMENT, GRID_VERTEX, QUAD_VERTEX,
    REQUIRED_UNIFORM_VECTORS, bone_rows, grid_lines, snapshot, translate,
)
from krita_scene_poser.storage.figures import load_figure

PROGRAMS = {
    "figure": (FIGURE_VERTEX, FIGURE_FRAGMENT),
    "normals": (GBUFFER_VERTEX, GBUFFER_NORMAL_FRAGMENT),
    "depth": (GBUFFER_VERTEX, GBUFFER_DEPTH_FRAGMENT),
    "edges": (QUAD_VERTEX, EDGE_FRAGMENT),
    "grid": (GRID_VERTEX, GRID_FRAGMENT),
}
# Reserved for future use in GLSL ES 1.00 or GLSL 1.50; drivers reject them as names.
RESERVED = set("""
    asm class union enum typedef template this packed goto switch default inline noinline
    volatile public static extern external interface flat long short double half fixed
    unsigned superp input output hvec2 hvec3 hvec4 dvec2 dvec3 dvec4 fvec2 fvec3 fvec4
    sampler1D sampler3D sampler1DShadow sampler2DShadow sampler2DRect sampler3DRect
    sampler2DRectShadow sizeof cast namespace using common partition active filter
    smooth noperspective centroid layout patch sample subroutine row_major texture
    in out inout
""".split())
DECLARATION = re.compile(r"^(uniform|attribute|varying)\s+(\w+)\s+(\w+)(?:\[(\d+)\])?;", re.M)
SLOTS = {"float": 1, "int": 1, "vec2": 1, "vec3": 1, "vec4": 1, "mat4": 4, "sampler2D": 0}


def declarations(source, kind):
    return {name: (type_, int(count or 1))
            for found, type_, name, count in DECLARATION.findall(source) if found == kind}


def declared_names(source):
    """Identifiers the source declares: variables, parameters, and functions."""
    body = re.sub(r"^#.*$", "", source, flags=re.M)
    return set(re.findall(r"\b(?:float|int|bool|vec[234]|mat4|sampler2D|void)\s+(\w+)", body))


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

    def test_every_program_translates_cleanly(self):
        for name, (vertex, fragment) in PROGRAMS.items():
            for dialect in ("GLSL 1.20", "GLSL 1.50 core"):
                with self.subTest(program=name, dialect=dialect):
                    for source, stage in ((vertex, "vertex"), (fragment, "fragment")):
                        text = translate(source, dialect, stage)
                        self.assertEqual(text.count("#version"), 1)
                        self.assertNotIn("precision", text)
                        self.assertNotIn("GL_FRAGMENT_PRECISION_HIGH", text)
                    if dialect == "GLSL 1.50 core":
                        core = translate(fragment, dialect, "fragment")
                        self.assertNotIn("texture2D", core)
                        self.assertNotIn("gl_FragColor", core)

    def test_depth_and_edge_passes_ask_for_high_precision_on_es(self):
        for source in (GBUFFER_DEPTH_FRAGMENT, EDGE_FRAGMENT):
            self.assertIn("#ifdef GL_FRAGMENT_PRECISION_HIGH", source)
            self.assertLess(source.index("precision highp float;"), source.index("void main"))

    def test_no_reserved_words_are_used_as_names(self):
        for name, pair in PROGRAMS.items():
            for source in pair:
                with self.subTest(program=name):
                    self.assertEqual(declared_names(source) & RESERVED, set())

    def test_fragment_varyings_come_from_the_vertex_shader(self):
        for name, (vertex, fragment) in PROGRAMS.items():
            with self.subTest(program=name):
                produced = declarations(vertex, "varying")
                for varying, kind in declarations(fragment, "varying").items():
                    self.assertEqual(produced.get(varying), kind, varying)

    def test_vertex_uniforms_fit_the_required_budget(self):
        for name, (vertex, _) in PROGRAMS.items():
            with self.subTest(program=name):
                used = sum(SLOTS[type_] * count
                           for type_, count in declarations(vertex, "uniform").values())
                self.assertLessEqual(used, REQUIRED_UNIFORM_VECTORS)

    def test_depth_packing_survives_eight_bit_channels(self):
        """Mirror the depth pass and the edge shader's decode on the CPU."""
        def fract(x):
            return x - math.floor(x)
        for t in (0.0, 1e-4, 0.123456, 0.5, 0.77777, 0.999999):
            e = [fract(t * k) for k in (1.0, 255.0, 65025.0)]
            e[0] -= e[1] / 255.0
            e[1] -= e[2] / 255.0
            stored = [round(max(0.0, min(1.0, c)) * 255.0) / 255.0 for c in e]
            decoded = stored[0] + stored[1] / 255.0 + stored[2] / 65025.0
            self.assertAlmostEqual(decoded, t, delta=1e-6)

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

    def test_snapshot_carries_camera_and_depth_range(self):
        rig, mesh = load_figure("body_chan")
        camera = OrbitCamera()
        pose = rig.skeleton.rest_pose()
        shot = snapshot(rig.skeleton, pose, camera, 1.5, selected=None)
        self.assertEqual((shot.selected, shot.grid, len(shot.view_projection), len(shot.view)),
                         (-1, True, 16, 16))
        self.assertEqual(shot.view_projection, camera.view_projection(1.5).m)
        self.assertEqual(shot.view, camera.view().m)
        # Every mesh vertex, not just the joints, must land inside the packed range.
        eye, forward = camera.eye(), camera.forward()
        for vertex in range(0, mesh.vertex_count, 211):
            p = Vec3(*mesh.positions[3 * vertex:3 * vertex + 3])
            depth = (p - eye).dot(forward)
            self.assertGreater(depth, shot.depth_near)
            self.assertLess(depth, shot.depth_near + shot.depth_range)
        self.assertEqual(snapshot(rig.skeleton, pose, camera, 1.0, 3, grid=False).selected, 3)

    def test_grid(self):
        points = grid_lines(extent=1.0, step=0.5)
        self.assertEqual(len(points), 5 * 12)
        self.assertTrue(all(points[i] == 0.0 for i in range(1, len(points), 3)))  # On y = 0.


if __name__ == "__main__":
    unittest.main()
