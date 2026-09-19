"""Skinned figure rendering on the GPU, plus a ground grid.

The renderer receives immutable ``RenderSnapshot`` values and never owns pose
state. Skinning runs in the vertex shader: each joint's matrix arrives as
three ``vec4`` rows of an affine transform. Output keeps the premultiplied
alpha contract of ``gl_renderer``: the figure is opaque, and grid lines are
premultiplied and blended.
"""

from array import array

from PyQt5.QtGui import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject, QSurfaceFormat,
)

from .gl_functions import CapabilityError, resolve_from_context
from .pixel_transfer import validate_dimensions
from .shaders import (
    BACKGROUND, BASE_COLOR, FIGURE_FRAGMENT, FIGURE_VERTEX, GRID_COLOR, GRID_FRAGMENT,
    GRID_VERTEX, HIGHLIGHT, LIGHT, REQUIRED_UNIFORM_VECTORS, grid_lines, translate,
)

GL_FLOAT, GL_UNSIGNED_BYTE = 0x1406, 0x1401
GL_UNSIGNED_SHORT, GL_UNSIGNED_INT = 0x1403, 0x1405
GL_LINES, GL_TRIANGLES = 0x0001, 0x0004
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_DEPTH_TEST, GL_BLEND, GL_CULL_FACE, GL_SCISSOR_TEST, GL_DITHER = 0x0B71, 0x0BE2, 0x0B44, 0x0C11, 0x0BD0
GL_LEQUAL = 0x0203
GL_ONE, GL_ONE_MINUS_SRC_ALPHA = 1, 0x0303
GL_MAX_TEXTURE_SIZE = 0x0D33
GL_MAX_VERTEX_UNIFORM_VECTORS, GL_MAX_VERTEX_UNIFORM_COMPONENTS = 0x8DFB, 0x8B4A


class FigureRenderer:
    def __init__(self):
        self.functions = None
        self.figure_program = self.grid_program = None
        self.vao = self.grid_buffer = None
        self.buffers = {}
        self.index_buffer = None
        self.index_count = 0
        self.index_type = GL_UNSIGNED_SHORT
        self.grid_count = 0
        self.mesh_key = None
        self.locations = {}
        self.details = {}

    def initialize(self, context):
        """Create shaders and the grid in ``context``, which must be current."""
        es = context.isOpenGLES()
        core = not es and context.format().profile() == QSurfaceFormat.CoreProfile
        dialect = "GLSL ES 1.00" if es else "GLSL 1.50 core" if core else "GLSL 1.20"
        self.functions = gl = resolve_from_context(context)
        capacity = self._uniform_capacity()
        self.details = {
            "vendor": gl.get_string(0x1F00), "renderer": gl.get_string(0x1F01),
            "gl_version": gl.get_string(0x1F02), "glsl_version": gl.get_string(0x8B8C),
            "api": "OpenGL ES" if es else "OpenGL core" if core else "OpenGL",
            "shaders": dialect, "functions": "ctypes via QOpenGLContext.getProcAddress",
            "vertex_uniform_vectors": capacity,
            "export_antialiasing": "4x MSAA" if QOpenGLFramebufferObject.hasOpenGLFramebufferBlit() else "none",
        }
        if capacity < REQUIRED_UNIFORM_VECTORS:
            raise CapabilityError(
                "This GPU allows {} vertex uniform vectors; KSP's skinning needs {}.".format(
                    capacity, REQUIRED_UNIFORM_VECTORS))
        self.figure_program = self._program(FIGURE_VERTEX, FIGURE_FRAGMENT, dialect,
                                            ("a_position", "a_normal", "a_joints", "a_weights"))
        self.grid_program = self._program(GRID_VERTEX, GRID_FRAGMENT, dialect, ("a_position",))
        for program, names in ((self.figure_program, ("u_view_projection", "u_bones", "u_selected",
                                                      "u_light", "u_base", "u_highlight")),
                               (self.grid_program, ("u_view_projection", "u_color"))):
            for name in names:
                location = program.uniformLocation(name)
                if location < 0:
                    raise CapabilityError("Shader uniform {} is missing.".format(name))
                self.locations[(id(program), name)] = location
        self.vao = QOpenGLVertexArrayObject()
        if not self.vao.create() and core:
            raise CapabilityError("Cannot create the required OpenGL vertex array.")
        points = grid_lines()
        self.grid_buffer = self._buffer(QOpenGLBuffer.VertexBuffer, _floats(points))
        self.grid_count = len(points) // 3
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
        return program

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

    def draw(self, snapshot, width, height, transparent=False):
        gl = self.functions
        gl.glViewport(0, 0, width, height)
        for flag in (GL_CULL_FACE, GL_SCISSOR_TEST, GL_DITHER, GL_BLEND):
            gl.glDisable(flag)
        gl.glColorMask(1, 1, 1, 1)
        gl.glDepthMask(1)
        if transparent:
            gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        else:
            gl.glClearColor(BACKGROUND[0], BACKGROUND[1], BACKGROUND[2], 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glDepthFunc(GL_LEQUAL)
        if self.vao.isCreated():
            self.vao.bind()
        try:
            if snapshot.grid:
                self._draw_grid(snapshot)
            if self.mesh_key is not None:
                self._draw_figure(snapshot)
            self._check_error("figure draw")
        finally:
            if self.vao.isCreated():
                self.vao.release()

    def _uniform(self, program, name):
        return self.locations[(id(program), name)]

    def _draw_grid(self, snapshot):
        gl, program = self.functions, self.grid_program
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

    def _draw_figure(self, snapshot):
        gl, program = self.functions, self.figure_program
        if not program.bind():
            raise CapabilityError("Cannot bind the figure shader.")
        try:
            gl.uniform_matrix4(self._uniform(program, "u_view_projection"), snapshot.view_projection)
            gl.uniform_vec4_array(self._uniform(program, "u_bones"), snapshot.bones)
            gl.glUniform1f(self._uniform(program, "u_selected"), float(snapshot.selected))
            length = sum(c * c for c in LIGHT) ** 0.5
            gl.glUniform3f(self._uniform(program, "u_light"), *(c / length for c in LIGHT))
            gl.glUniform4f(self._uniform(program, "u_base"), *BASE_COLOR)
            gl.glUniform4f(self._uniform(program, "u_highlight"), *HIGHLIGHT)
            for location, (buffer, kind, size) in self.buffers.items():
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
            for location in self.buffers:
                program.disableAttributeArray(location)
            if self.index_buffer is not None:
                self.index_buffer.release()
            program.release()

    def render_image(self, snapshot, width, height):
        """Top-down, premultiplied QImage on a transparent background."""
        validate_dimensions(width, height)
        if max(width, height) > self.functions.get_integer(GL_MAX_TEXTURE_SIZE):
            raise CapabilityError("Output exceeds this GPU's maximum texture size.")
        multisample = QOpenGLFramebufferObject.hasOpenGLFramebufferBlit()
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Depth)
        fmt.setSamples(4 if multisample else 0)
        target = QOpenGLFramebufferObject(width, height, fmt)
        resolved = None
        try:
            if not target.isValid() or not target.bind():
                raise CapabilityError("Cannot allocate the offscreen framebuffer.")
            self.draw(snapshot, width, height, transparent=True)
            if multisample:
                plain = QOpenGLFramebufferObjectFormat()
                plain.setAttachment(QOpenGLFramebufferObject.NoAttachment)
                resolved = QOpenGLFramebufferObject(width, height, plain)
                QOpenGLFramebufferObject.blitFramebuffer(resolved, target)
            image = (resolved or target).toImage(True)
            if image.isNull() or (image.width(), image.height()) != (width, height):
                raise CapabilityError("Framebuffer readback returned an empty or incorrectly sized image.")
            self._check_error("figure readback")
            return image
        finally:
            QOpenGLFramebufferObject.bindDefault()
            del target, resolved

    def destroy(self):
        """Release resources; the owning context must be current."""
        self._release_mesh()
        if self.grid_buffer is not None:
            self.grid_buffer.destroy()
        if self.vao is not None and self.vao.isCreated():
            self.vao.destroy()
        for program in (self.figure_program, self.grid_program):
            if program is not None:
                program.removeAllShaders()
        self.figure_program = self.grid_program = self.vao = self.grid_buffer = None
        self.locations, self.functions = {}, None


def _floats(values):
    return array("f", values).tobytes()
