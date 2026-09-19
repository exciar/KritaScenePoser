"""Figure shader sources and render snapshots. Pure Python (no Qt).

Kept separate from ``figure_renderer`` so the shaders' dialect translation and
the skinning-row packing can be tested outside Krita.
"""

from dataclasses import dataclass

MAX_JOINTS = 64
BONE_ROWS = 3 * MAX_JOINTS
REQUIRED_UNIFORM_VECTORS = BONE_ROWS + 8

BACKGROUND = (0.165, 0.175, 0.195)
BASE_COLOR = (0.80, 0.76, 0.70, 1.0)
HIGHLIGHT = (1.0, 0.58, 0.18, 1.0)
GRID_COLOR = (0.62, 0.65, 0.70, 0.30)
LIGHT = (-0.35, 0.75, 0.56)  # Normalized when uploaded.

FIGURE_VERTEX = """#version 100
uniform mat4 u_view_projection;
uniform vec4 u_bones[%d];
uniform float u_selected;
attribute vec3 a_position;
attribute vec3 a_normal;
attribute vec4 a_joints;
attribute vec4 a_weights;
varying vec3 v_normal;
varying float v_selected;
vec3 skin(vec4 p, float joint) {
    int i = int(joint) * 3;
    return vec3(dot(u_bones[i], p), dot(u_bones[i + 1], p), dot(u_bones[i + 2], p));
}
void main() {
    vec4 joints = floor(a_joints * 255.0 + 0.5);
    vec4 p = vec4(a_position, 1.0);
    vec4 n = vec4(a_normal, 0.0);
    vec3 position = skin(p, joints.x) * a_weights.x + skin(p, joints.y) * a_weights.y
                  + skin(p, joints.z) * a_weights.z + skin(p, joints.w) * a_weights.w;
    v_normal = skin(n, joints.x) * a_weights.x + skin(n, joints.y) * a_weights.y
             + skin(n, joints.z) * a_weights.z + skin(n, joints.w) * a_weights.w;
    v_selected = dot(a_weights, vec4(equal(joints, vec4(u_selected))));
    gl_Position = u_view_projection * vec4(position, 1.0);
}
""" % BONE_ROWS

FIGURE_FRAGMENT = """#version 100
precision mediump float;
uniform vec3 u_light;
uniform vec4 u_base;
uniform vec4 u_highlight;
varying vec3 v_normal;
varying float v_selected;
void main() {
    vec3 n = normalize(v_normal);
    if (!gl_FrontFacing) n = -n;
    float diffuse = max(dot(n, u_light), 0.0);
    float sky = 0.5 + 0.5 * n.y;
    vec3 color = u_base.rgb * (0.28 + 0.22 * sky + 0.62 * diffuse);
    float glow = clamp(v_selected, 0.0, 1.0) * 0.6;
    color = mix(color, u_highlight.rgb * (0.5 + 0.5 * diffuse), glow);
    gl_FragColor = vec4(color, 1.0);
}
"""

GRID_VERTEX = """#version 100
uniform mat4 u_view_projection;
attribute vec3 a_position;
void main() {
    gl_Position = u_view_projection * vec4(a_position, 1.0);
}
"""

GRID_FRAGMENT = """#version 100
precision mediump float;
uniform vec4 u_color;
void main() {
    gl_FragColor = vec4(u_color.rgb * u_color.a, u_color.a);
}
"""


def translate(source, dialect, stage):
    """Adapt a GLSL ES 1.00 source to desktop GLSL 1.20 or 1.50 core."""
    if dialect == "GLSL ES 1.00":
        return source
    body = source.split("\n", 1)[1].replace("precision mediump float;\n", "")
    if dialect == "GLSL 1.20":
        return "#version 120\n" + body
    if stage == "vertex":
        body = body.replace("attribute ", "in ").replace("varying ", "out ")
    else:
        body = "out vec4 outputColor;\n" + body.replace("varying ", "in ").replace(
            "gl_FragColor", "outputColor")
    return "#version 150\n" + body


@dataclass(frozen=True)
class RenderSnapshot:
    bones: tuple  # 12 floats per joint: three affine rows.
    view_projection: tuple  # 16 floats, column-major.
    selected: int = -1
    grid: bool = True


def bone_rows(matrices):
    rows = []
    for matrix in matrices:
        m = matrix.m
        for r in range(3):
            rows.extend((m[r], m[4 + r], m[8 + r], m[12 + r]))
    return tuple(rows)


def snapshot(skeleton, pose, view_projection, selected=None, grid=True):
    if len(skeleton.joints) > MAX_JOINTS:
        raise ValueError("The figure has more than {} joints.".format(MAX_JOINTS))
    return RenderSnapshot(bone_rows(skeleton.skinning_matrices(pose)), view_projection.m,
                          -1 if selected is None else selected, grid)


def grid_lines(extent=2.0, step=0.25):
    count = int(round(2 * extent / step)) + 1
    points = []
    for i in range(count):
        offset = -extent + i * step
        points += [offset, 0.0, -extent, offset, 0.0, extent, -extent, 0.0, offset, extent, 0.0, offset]
    return points
