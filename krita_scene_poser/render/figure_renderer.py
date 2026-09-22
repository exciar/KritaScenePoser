"""GPU rendering of the skinned figure, the grid and the line art.

Every pass writes premultiplied alpha, so a faded figure, the grid and
the lines all blend correctly.
"""

from array import array

from PyQt5.QtCore import Qt
from PyQt5.QtGui import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject, QSurfaceFormat,
)

from .gl_functions import CapabilityError, resolve_from_context
from .pixel_transfer import validate_dimensions
from .shaders import (
    BACKGROUND, BASE_COLOR, EDGE_FRAGMENT, FIGURE_FRAGMENT, FIGURE_VERTEX, GBUFFER_DEPTH_FRAGMENT,
    GBUFFER_NORMAL_FRAGMENT, GBUFFER_VERTEX, GRID_COLOR, GRID_FRAGMENT, GRID_VERTEX, HIGHLIGHT,
    LIGHT, PAPER, QUAD, QUAD_VERTEX, REQUIRED_UNIFORM_VECTORS, grid_lines, translate,
)

GL_FLOAT, GL_UNSIGNED_BYTE = 0x1406, 0x1401
GL_UNSIGNED_SHORT, GL_UNSIGNED_INT = 0x1403, 0x1405
GL_LINES, GL_TRIANGLES, GL_TRIANGLE_STRIP = 0x0001, 0x0004, 0x0005
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_DEPTH_TEST, GL_BLEND, GL_CULL_FACE, GL_SCISSOR_TEST, GL_DITHER = 0x0B71, 0x0BE2, 0x0B44, 0x0C11, 0x0BD0
GL_LEQUAL, GL_EQUAL = 0x0203, 0x0202
GL_ONE, GL_ONE_MINUS_SRC_ALPHA = 1, 0x0303
GL_TEXTURE_2D, GL_TEXTURE0 = 0x0DE1, 0x84C0
GL_MAX_TEXTURE_SIZE = 0x0D33
GL_MAX_VERTEX_UNIFORM_VECTORS, GL_MAX_VERTEX_UNIFORM_COMPONENTS = 0x8DFB, 0x8B4A

SKINNED = ("a_position", "a_normal", "a_joints", "a_weights")
PROGRAMS = {
    "figure": (FIGURE_VERTEX, FIGURE_FRAGMENT, SKINNED),
    "normals": (GBUFFER_VERTEX, GBUFFER_NORMAL_FRAGMENT, SKINNED + ("a_part",)),
    "depth": (GBUFFER_VERTEX, GBUFFER_DEPTH_FRAGMENT, SKINNED + ("a_part",)),
    "grid": (GRID_VERTEX, GRID_FRAGMENT, ("a_position",)),
    "edges": (QUAD_VERTEX, EDGE_FRAGMENT, ("a_position",)),
}
MODES = ("shaded", "lines", "both")


class FigureRenderer:
    def __init__(self):
        self.functions = None
        self.programs = {}
        self.vao = self.grid_buffer = self.quad_buffer = None
        self.buffers = {}
        self.index_buffer = None
        self.index_count = 0
        self.index_type = GL_UNSIGNED_SHORT
        self.grid_count = 0
        self.mesh_key = None
        self.gbuffers = None  # (width, height, normals FBO, depth FBO)
        self.details = {}

    def initialize(self, context):
        """Create shaders and geometry in ``context``, which must be current."""
        es = context.isOpenGLES()
        core = not es and context.format().profile() == QSurfaceFormat.CoreProfile
        dialect = "GLSL ES 1.00" if es else "GLSL 1.50 core" if core else "GLSL 1.20"
        self.functions = gl = resolve_from_context(context)
        capacity = self._uniform_capacity()
        blit = QOpenGLFramebufferObject.hasOpenGLFramebufferBlit()
        self.details = {
            "vendor": gl.get_string(0x1F00), "renderer": gl.get_string(0x1F01),
            "gl_version": gl.get_string(0x1F02), "glsl_version": gl.get_string(0x8B8C),
            "api": "OpenGL ES" if es else "OpenGL core" if core else "OpenGL",
            "shaders": dialect, "functions": "ctypes via QOpenGLContext.getProcAddress",
            "vertex_uniform_vectors": capacity,
            "export_antialiasing": "4x MSAA" if blit else "none",
        }
        if capacity < REQUIRED_UNIFORM_VECTORS:
            raise CapabilityError(
                "This GPU allows {} vertex uniform vectors; KSP's skinning needs {}.".format(
                    capacity, REQUIRED_UNIFORM_VECTORS))
        for name, (vertex, fragment, attributes) in PROGRAMS.items():
            self.programs[name] = self._program(vertex, fragment, dialect, attributes)
        self.vao = QOpenGLVertexArrayObject()
        if not self.vao.create() and core:
            raise CapabilityError("Cannot create the required OpenGL vertex array.")
        points = grid_lines()
        self.grid_buffer = self._buffer(QOpenGLBuffer.VertexBuffer, _floats(points))
        self.grid_count = len(points) // 3
        self.quad_buffer = self._buffer(QOpenGLBuffer.VertexBuffer, _floats(QUAD))
        self._check_error("figure renderer setup")

    def _uniform_capacity(self):
        gl = self.functions
        value = gl.get_integer(GL_MAX_VERTEX_UNIFORM_VECTORS)  # ES and GL 4.1+.
        if gl.glGetError() or value <= 0:
            value = gl.get_integer(GL_MAX_VERTEX_UNIFORM_COMPONENTS) // 4
            gl.glGetError()
        return value

    def _program(self, vertex, fragment, dialect, attributes):
        program = QOpenGLShaderProgram()
        for kind, source, stage in ((QOpenGLShader.Vertex, vertex, "vertex"),
                                    (QOpenGLShader.Fragment, fragment, "fragment")):
            if not program.addShaderFromSourceCode(kind, translate(source, dialect, stage)):
                raise CapabilityError("Figure shader failed: " + program.log())
        for location, name in enumerate(attributes):
            program.bindAttributeLocation(name, location)
        if not program.link():
            raise CapabilityError("Figure shader link failed: " + program.log())
        program.ksp_attributes = len(attributes)
        program.ksp_uniforms = {}
        return program

    @staticmethod
    def _uniform(program, name):
        """Location, cached; -1 (optimized out) is silently ignored by GL."""
        if name not in program.ksp_uniforms:
            program.ksp_uniforms[name] = program.uniformLocation(name)
        return program.ksp_uniforms[name]

    def _buffer(self, kind, payload):
        buffer = QOpenGLBuffer(kind)
        if not buffer.create() or not buffer.bind():
            raise CapabilityError("Cannot create an OpenGL buffer.")
        buffer.setUsagePattern(QOpenGLBuffer.StaticDraw)
        buffer.allocate(payload, len(payload))
        buffer.release()
        return buffer

    def _check_error(self, stage):
        error = self.functions.glGetError()
        if error:
            raise CapabilityError("OpenGL error 0x{:04x} during {}".format(error, stage))

    # Geometry ----------------------------------------------------------------------

    def set_mesh(self, key, mesh):
        """Upload a figure once; later calls with the same key do nothing."""
        if self.mesh_key == key:
            return
        self._release_mesh()
        if self.vao.isCreated():
            self.vao.bind()
        try:
            self.buffers = {
                0: (self._buffer(QOpenGLBuffer.VertexBuffer, mesh.positions.tobytes()), GL_FLOAT, 3),
                1: (self._buffer(QOpenGLBuffer.VertexBuffer, mesh.normals.tobytes()), GL_FLOAT, 3),
                2: (self._buffer(QOpenGLBuffer.VertexBuffer, bytes(mesh.joints)), GL_UNSIGNED_BYTE, 4),
                3: (self._buffer(QOpenGLBuffer.VertexBuffer, bytes(mesh.weights)), GL_UNSIGNED_BYTE, 4),
                4: (self._buffer(QOpenGLBuffer.VertexBuffer, bytes(mesh.parts)), GL_UNSIGNED_BYTE, 1),
            }
            self.index_buffer = self._buffer(QOpenGLBuffer.IndexBuffer, mesh.indices.tobytes())
        finally:
            if self.vao.isCreated():
                self.vao.release()
        self.index_count = len(mesh.indices)
        self.index_type = GL_UNSIGNED_SHORT if mesh.indices.typecode == "H" else GL_UNSIGNED_INT
        self.mesh_key = key
        self._check_error("figure upload")

    def _release_mesh(self):
        for buffer, _, _ in self.buffers.values():
            buffer.destroy()
        if self.index_buffer is not None:
            self.index_buffer.destroy()
        self.buffers, self.index_buffer, self.mesh_key = {}, None, None

    def reset_state(self):
        """Leave GL state as QPainter expects before drawing an overlay."""
        gl = self.functions
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDisable(GL_BLEND)
        gl.glDepthMask(1)
        gl.glActiveTexture(GL_TEXTURE0)

    # Drawing -----------------------------------------------------------------------

    def draw(self, snapshot, width, height, transparent=False, mode="shaded", lines=None,
             bind_target=None):
        """Draw into the bound target; ``bind_target`` re-binds it after line G-buffers."""
        if mode not in MODES:
            raise ValueError("Unknown display mode: {}".format(mode))
        want_lines = mode != "shaded" and lines is not None and self.mesh_key is not None
        gl = self.functions
        if self.vao.isCreated():
            self.vao.bind()
        try:
            if want_lines:
                self._draw_gbuffers(snapshot, width, height)
                (bind_target or QOpenGLFramebufferObject.bindDefault)()
            gl.glViewport(0, 0, width, height)
            for flag in (GL_CULL_FACE, GL_SCISSOR_TEST, GL_DITHER, GL_BLEND):
                gl.glDisable(flag)
            gl.glColorMask(1, 1, 1, 1)
            gl.glDepthMask(1)
            if transparent:
                gl.glClearColor(0.0, 0.0, 0.0, 0.0)
            else:
                backdrop = PAPER if mode == "lines" else BACKGROUND
                gl.glClearColor(backdrop[0], backdrop[1], backdrop[2], 1.0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            gl.glEnable(GL_DEPTH_TEST)
            gl.glDepthFunc(GL_LEQUAL)
            if snapshot.grid:
                self._draw_grid(snapshot)
            if self.mesh_key is not None and mode in ("shaded", "both"):
                self._draw_figure(snapshot)
            if want_lines:
                self._draw_edges(snapshot, lines, width, height)
            self._check_error("figure draw")
        finally:
            if self.vao.isCreated():
                self.vao.release()

    def _draw_figure(self, snapshot):
        """The shaded figure, opaque or evenly translucent.

        Below full opacity the figure is drawn twice: once into the depth
        buffer alone, then only where that depth won. Without the first pass
        the far side of a limb would blend through the near side and the
        figure would look mottled rather than evenly faded.
        """
        gl = self.functions
        if snapshot.opacity >= 1.0:
            self._draw_skinned("figure", snapshot)
            return
        gl.glColorMask(0, 0, 0, 0)
        gl.glDepthMask(1)
        self._draw_skinned("figure", snapshot)
        gl.glColorMask(1, 1, 1, 1)
        gl.glDepthMask(0)
        gl.glDepthFunc(GL_EQUAL)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        try:
            self._draw_skinned("figure", snapshot)
        finally:
            gl.glDisable(GL_BLEND)
            gl.glDepthFunc(GL_LEQUAL)
            gl.glDepthMask(1)

    def _draw_grid(self, snapshot):
        gl, program = self.functions, self.programs["grid"]
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(0)
        if not program.bind() or not self.grid_buffer.bind():
            raise CapabilityError("Cannot bind the grid.")
        try:
            gl.uniform_matrix4(self._uniform(program, "u_view_projection"), snapshot.view_projection)
            gl.glUniform4f(self._uniform(program, "u_color"), *GRID_COLOR)
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 0)
            gl.glDrawArrays(GL_LINES, 0, self.grid_count)
        finally:
            program.disableAttributeArray(0)
            self.grid_buffer.release()
            program.release()
            gl.glDepthMask(1)
            gl.glDisable(GL_BLEND)

    def _draw_skinned(self, name, snapshot):
        gl, program = self.functions, self.programs[name]
        if not program.bind():
            raise CapabilityError("Cannot bind the {} shader.".format(name))
        locations = range(program.ksp_attributes)
        try:
            gl.uniform_matrix4(self._uniform(program, "u_view_projection"), snapshot.view_projection)
            gl.uniform_vec4_array(self._uniform(program, "u_bones"), snapshot.bones)
            if name == "figure":
                gl.glUniform1f(self._uniform(program, "u_selected"), float(snapshot.selected))
                length = sum(c * c for c in LIGHT) ** 0.5
                gl.glUniform3f(self._uniform(program, "u_light"), *(c / length for c in LIGHT))
                gl.glUniform4f(self._uniform(program, "u_base"), *BASE_COLOR)
                gl.glUniform4f(self._uniform(program, "u_highlight"), *HIGHLIGHT)
                gl.glUniform1f(self._uniform(program, "u_opacity"), snapshot.opacity)
            else:
                gl.uniform_matrix4(self._uniform(program, "u_view"), snapshot.view)
                gl.glUniform1f(self._uniform(program, "u_depth_near"), snapshot.depth_near)
                gl.glUniform1f(self._uniform(program, "u_depth_range"), snapshot.depth_range)
            for location in locations:
                buffer, kind, size = self.buffers[location]
                if not buffer.bind():
                    raise CapabilityError("Cannot bind figure geometry.")
                program.enableAttributeArray(location)
                program.setAttributeBuffer(location, kind, 0, size, 0)
                buffer.release()
            if not self.index_buffer.bind():
                raise CapabilityError("Cannot bind figure indices.")
            # A null pointer is offset zero into the bound index buffer.
            gl.glDrawElements(GL_TRIANGLES, self.index_count, self.index_type, None)
        finally:
            for location in locations:
                program.disableAttributeArray(location)
            if self.index_buffer is not None:
                self.index_buffer.release()
            program.release()

    def _gbuffer_targets(self, width, height):
        if self.gbuffers is None or self.gbuffers[:2] != (width, height):
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Depth)
            fmt.setSamples(0)
            targets = (QOpenGLFramebufferObject(width, height, fmt),
                       QOpenGLFramebufferObject(width, height, fmt))
            if not all(t.isValid() for t in targets):
                raise CapabilityError("Cannot allocate the line-art buffers.")
            self.gbuffers = (width, height) + targets
        return self.gbuffers[2], self.gbuffers[3]

    def _draw_gbuffers(self, snapshot, width, height):
        gl = self.functions
        for target, name in zip(self._gbuffer_targets(width, height), ("normals", "depth")):
            if not target.bind():
                raise CapabilityError("Cannot bind a line-art buffer.")
            gl.glViewport(0, 0, width, height)
            gl.glDisable(GL_BLEND)
            gl.glDepthMask(1)
            gl.glClearColor(0.0, 0.0, 0.0, 0.0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            gl.glEnable(GL_DEPTH_TEST)
            gl.glDepthFunc(GL_LEQUAL)
            self._draw_skinned(name, snapshot)

    def _draw_edges(self, snapshot, lines, width, height):
        gl, program = self.functions, self.programs["edges"]
        normals, depth = self.gbuffers[2], self.gbuffers[3]
        gl.glDisable(GL_DEPTH_TEST)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        if not program.bind() or not self.quad_buffer.bind():
            raise CapabilityError("Cannot bind the line-art shader.")
        try:
            for unit, texture, name in ((0, normals.texture(), "u_normals"),
                                        (1, depth.texture(), "u_depth")):
                gl.glActiveTexture(GL_TEXTURE0 + unit)
                gl.glBindTexture(GL_TEXTURE_2D, texture)
                gl.glUniform1i(self._uniform(program, name), unit)
            gl.glUniform2f(self._uniform(program, "u_texel"), 1.0 / width, 1.0 / height)
            gl.glUniform1f(self._uniform(program, "u_outline_radius"), lines.outline_radius)
            gl.glUniform1f(self._uniform(program, "u_inner_radius"), lines.inner_radius)
            gl.glUniform1f(self._uniform(program, "u_depth_near"), snapshot.depth_near)
            gl.glUniform1f(self._uniform(program, "u_depth_range"), snapshot.depth_range)
            gl.glUniform1f(self._uniform(program, "u_depth_threshold"), lines.depth_threshold)
            gl.glUniform1f(self._uniform(program, "u_crease_cos"), lines.crease_cos)
            gl.glUniform4f(self._uniform(program, "u_enabled"), *lines.enabled)
            gl.glUniform4f(self._uniform(program, "u_color"), *lines.rgba)
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 0)
            gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        finally:
            program.disableAttributeArray(0)
            for unit in (1, 0):
                gl.glActiveTexture(GL_TEXTURE0 + unit)
                gl.glBindTexture(GL_TEXTURE_2D, 0)
            self.quad_buffer.release()
            program.release()
            gl.glDisable(GL_BLEND)

    def render_image(self, snapshot, width, height, mode="shaded", lines=None, scale_to=None):
        """Top-down, premultiplied QImage on a transparent background.

        ``scale_to`` is the final ``(width, height)`` when the render is a
        supersampled one; Qt scales premultiplied pixels correctly, so the
        smooth result keeps the alpha contract.
        """
        validate_dimensions(width, height)
        if max(width, height) > self.functions.get_integer(GL_MAX_TEXTURE_SIZE):
            raise CapabilityError("Output exceeds this GPU's maximum texture size.")
        # The edge shader antialiases lines itself; MSAA would only cost memory.
        multisample = QOpenGLFramebufferObject.hasOpenGLFramebufferBlit() and mode != "lines"
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Depth)
        fmt.setSamples(4 if multisample else 0)
        target = QOpenGLFramebufferObject(width, height, fmt)
        resolved = None
        try:
            if not target.isValid() or not target.bind():
                raise CapabilityError("Cannot allocate the offscreen framebuffer.")
            self.draw(snapshot, width, height, transparent=True, mode=mode, lines=lines,
                      bind_target=target.bind)
            if multisample:
                plain = QOpenGLFramebufferObjectFormat()
                plain.setAttachment(QOpenGLFramebufferObject.NoAttachment)
                resolved = QOpenGLFramebufferObject(width, height, plain)
                QOpenGLFramebufferObject.blitFramebuffer(resolved, target)
            image = (resolved or target).toImage(True)
            if image.isNull() or (image.width(), image.height()) != (width, height):
                raise CapabilityError("Framebuffer readback returned an empty or incorrectly sized image.")
            self._check_error("figure readback")
            if scale_to is not None and tuple(scale_to) != (width, height):
                image = image.scaled(int(scale_to[0]), int(scale_to[1]),
                                     Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                if image.isNull():
                    raise CapabilityError("KSP could not scale the render to the output size.")
            return image
        finally:
            QOpenGLFramebufferObject.bindDefault()
            del target, resolved

    def release_gbuffers(self):
        """Free large line-art buffers, e.g. after a full-size export."""
        self.gbuffers = None

    def destroy(self):
        """Release resources; the owning context must be current."""
        self._release_mesh()
        self.gbuffers = None
        for buffer in (self.grid_buffer, self.quad_buffer):
            if buffer is not None:
                buffer.destroy()
        if self.vao is not None and self.vao.isCreated():
            self.vao.destroy()
        for program in self.programs.values():
            program.removeAllShaders()
        self.programs = {}
        self.vao = self.grid_buffer = self.quad_buffer = None
        self.functions = None


def _floats(values):
    return array("f", values).tobytes()
