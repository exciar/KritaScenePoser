"""Shader sources and render snapshots, free of Qt so they can be tested outside Krita.

Sources are GLSL ES 1.00, translated for desktop GLSL 1.20 and 1.50 core.
"""

from dataclasses import dataclass

from ..core.lineart import depth_range

MAX_JOINTS = 64
BONE_ROWS = 3 * MAX_JOINTS
REQUIRED_UNIFORM_VECTORS = BONE_ROWS + 12

BACKGROUND = (0.165, 0.175, 0.195)
PAPER = (0.93, 0.925, 0.905)  # Viewport backdrop for the Lines mode.
BASE_COLOR = (0.80, 0.76, 0.70, 1.0)
HIGHLIGHT = (1.0, 0.58, 0.18, 1.0)
GRID_COLOR = (0.62, 0.65, 0.70, 0.30)
LIGHT = (-0.35, 0.75, 0.56)  # Normalized when uploaded.

# Depth packing needs full precision; ES fragment shaders may default lower.
HIGH_PRECISION = """#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
"""

SKINNING = """uniform vec4 u_bones[%d];
attribute vec3 a_position;
attribute vec3 a_normal;
attribute vec4 a_joints;
attribute vec4 a_weights;
vec3 skin(vec4 p, float joint) {
    int i = int(joint) * 3;
    return vec3(dot(u_bones[i], p), dot(u_bones[i + 1], p), dot(u_bones[i + 2], p));
}
vec3 skin_blend(vec4 p, vec4 joints) {
    return skin(p, joints.x) * a_weights.x + skin(p, joints.y) * a_weights.y
         + skin(p, joints.z) * a_weights.z + skin(p, joints.w) * a_weights.w;
}
""" % BONE_ROWS

FIGURE_VERTEX = """#version 100
uniform mat4 u_view_projection;
uniform float u_selected;
""" + SKINNING + """varying vec3 v_normal;
varying float v_selected;
void main() {
    vec4 joints = floor(a_joints * 255.0 + 0.5);
    vec3 position = skin_blend(vec4(a_position, 1.0), joints);
    v_normal = skin_blend(vec4(a_normal, 0.0), joints);
    v_selected = dot(a_weights, vec4(equal(joints, vec4(u_selected))));
    gl_Position = u_view_projection * vec4(position, 1.0);
}
"""

FIGURE_FRAGMENT = """#version 100
precision mediump float;
uniform vec3 u_light;
uniform vec4 u_base;
uniform vec4 u_highlight;
uniform float u_opacity;
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
    gl_FragColor = vec4(color * u_opacity, u_opacity);  // Premultiplied.
}
"""

# G-buffer pass: view-space normal, part id, and linear depth. Each triangle
# comes from one mesh part, so the interpolated part id is exact.
GBUFFER_VERTEX = """#version 100
uniform mat4 u_view_projection;
uniform mat4 u_view;
uniform float u_depth_near;
uniform float u_depth_range;
""" + SKINNING + """attribute float a_part;
varying vec3 v_normal;
varying float v_depth;
varying float v_part;
void main() {
    vec4 joints = floor(a_joints * 255.0 + 0.5);
    vec4 position = vec4(skin_blend(vec4(a_position, 1.0), joints), 1.0);
    v_normal = (u_view * vec4(skin_blend(vec4(a_normal, 0.0), joints), 0.0)).xyz;
    v_depth = (-(u_view * position).z - u_depth_near) / u_depth_range;
    v_part = a_part;
    gl_Position = u_view_projection * position;
}
"""

GBUFFER_NORMAL_FRAGMENT = """#version 100
precision mediump float;
varying vec3 v_normal;
varying float v_depth;
varying float v_part;
void main() {
    vec3 n = normalize(v_normal);
    if (!gl_FrontFacing) n = -n;
    gl_FragColor = vec4(n * 0.5 + 0.5, v_part + 1.0 / 255.0);
}
"""

GBUFFER_DEPTH_FRAGMENT = """#version 100
""" + HIGH_PRECISION + """varying vec3 v_normal;
varying float v_depth;
varying float v_part;
void main() {
    float t = clamp(v_depth, 0.0, 0.999999);
    vec3 encoded = fract(t * vec3(1.0, 255.0, 65025.0));
    encoded.xy -= encoded.yz / 255.0;
    gl_FragColor = vec4(encoded, 1.0);
}
"""

QUAD_VERTEX = """#version 100
attribute vec2 a_position;
varying vec2 v_uv;
void main() {
    v_uv = a_position * 0.5 + 0.5;
    gl_Position = vec4(a_position, 0.0, 1.0);
}
"""

# Edge pass: a pixel is on a line if a sample at the line radius, in any of
# eight directions, crosses an enabled discontinuity. Four sub-pixel tests
# give antialiased coverage. Output is premultiplied.
EDGE_FRAGMENT = """#version 100
""" + HIGH_PRECISION + """uniform sampler2D u_normals;
uniform sampler2D u_depth;
uniform vec2 u_texel;
uniform float u_outline_radius;
uniform float u_inner_radius;
uniform float u_depth_near;
uniform float u_depth_range;
uniform float u_depth_threshold;
uniform float u_crease_cos;
uniform vec4 u_enabled;
uniform vec4 u_color;
varying vec2 v_uv;
float depth_at(vec2 uv) {
    vec3 e = texture2D(u_depth, uv).rgb;
    return u_depth_near + dot(e, vec3(1.0, 1.0 / 255.0, 1.0 / 65025.0)) * u_depth_range;
}
float edge_at(vec2 uv) {
    vec4 center = texture2D(u_normals, uv);
    bool inside = center.a > 0.5 / 255.0;
    vec3 normal = center.rgb * 2.0 - 1.0;
    float depth = inside ? depth_at(uv) : 0.0;
    for (int i = 0; i < 8; i++) {
        float angle = float(i) * 0.7853982;
        vec2 direction = vec2(cos(angle), sin(angle)) * u_texel;
        if (u_enabled.x > 0.5) {
            bool other = texture2D(u_normals, uv + direction * u_outline_radius).a > 0.5 / 255.0;
            if (other != inside) return 1.0;
        }
        if (inside) {
            vec2 q = uv + direction * u_inner_radius;
            vec4 neighbor = texture2D(u_normals, q);
            if (neighbor.a > 0.5 / 255.0) {
                if (u_enabled.w > 0.5 && abs(neighbor.a - center.a) > 0.5 / 255.0) return 1.0;
                if (u_enabled.z > 0.5 && dot(normal, neighbor.rgb * 2.0 - 1.0) < u_crease_cos) return 1.0;
                if (u_enabled.y > 0.5) {
                    // Measured against the figure's own depth, not its distance
                    // from the camera, so contours do not change as you zoom.
                    float other_depth = depth_at(q);
                    if (abs(other_depth - depth) > u_depth_threshold * u_depth_range) return 1.0;
                }
            }
        }
    }
    return 0.0;
}
void main() {
    vec2 h = u_texel * 0.25;
    float coverage = 0.25 * (edge_at(v_uv + vec2(-h.x, -h.y)) + edge_at(v_uv + vec2(h.x, -h.y))
                           + edge_at(v_uv + vec2(-h.x, h.y)) + edge_at(v_uv + vec2(h.x, h.y)));
    float alpha = coverage * u_color.a;
    gl_FragColor = vec4(u_color.rgb * alpha, alpha);
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
    body = source.split("\n", 1)[1]
    body = body.replace(HIGH_PRECISION, "").replace("precision mediump float;\n", "")
    if dialect == "GLSL 1.20":
        return "#version 120\n" + body
    if stage == "vertex":
        body = body.replace("attribute ", "in ").replace("varying ", "out ")
    else:
        body = "out vec4 outputColor;\n" + body.replace("varying ", "in ").replace(
            "gl_FragColor", "outputColor").replace("texture2D(", "texture(")
    return "#version 150\n" + body


@dataclass(frozen=True)
class RenderSnapshot:
    bones: tuple  # 12 floats per joint: three affine rows.
    view_projection: tuple  # 16 floats, column-major.
    view: tuple = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    depth_near: float = 0.1
    depth_range: float = 10.0
    selected: int = -1
    grid: bool = True
    opacity: float = 1.0  # Figure alpha; lines carry their own in LineArtSettings.


def bone_rows(matrices):
    rows = []
    for matrix in matrices:
        m = matrix.m
        for r in range(3):
            rows.extend((m[r], m[4 + r], m[8 + r], m[12 + r]))
    return tuple(rows)


def snapshot(skeleton, pose, camera, aspect, selected=None, grid=True, opacity=1.0):
    """Immutable render input for one frame of ``camera`` at ``aspect``."""
    if len(skeleton.joints) > MAX_JOINTS:
        raise ValueError("The figure has more than {} joints.".format(MAX_JOINTS))
    points = [t.position for t in skeleton.transforms(pose)] if skeleton.joints else []
    near, span = depth_range(points, camera)
    return RenderSnapshot(bone_rows(skeleton.skinning_matrices(pose)),
                          camera.view_projection(aspect).m, camera.view().m, near, span,
                          -1 if selected is None else selected, grid,
                          min(1.0, max(0.0, float(opacity))))


def grid_lines(extent=2.0, step=0.25):
    count = int(round(2 * extent / step)) + 1
    points = []
    for i in range(count):
        offset = -extent + i * step
        points += [offset, 0.0, -extent, offset, 0.0, extent, -extent, 0.0, offset, extent, 0.0, offset]
    return points


QUAD = (-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)  # Triangle strip covering the viewport.
