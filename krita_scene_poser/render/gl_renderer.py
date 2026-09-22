"""Indexed-triangle renderer used by Self-Test.

Shaders emit premultiplied alpha: Qt labels framebuffer readback as premultiplied,
so straight alpha would be corrupted on conversion.
"""
import struct

from PyQt5.QtGui import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject, QSurfaceFormat,
)

from .gl_functions import CapabilityError, resolve_from_context

__all__ = ["CapabilityError", "TriangleRenderer"]

GL_FLOAT = 0x1406
GL_UNSIGNED_SHORT = 0x1403
GL_TRIANGLES = 0x0004
GL_COLOR_BUFFER_BIT = 0x4000
GL_BLEND = 0x0BE2
GL_ONE = 1
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_MAX_TEXTURE_SIZE = 0x0D33

# Premultiplied output; see the module docstring.
SHADERS = {
    "GLSL ES 1.00": (
        """#version 100
attribute vec2 position; attribute vec4 color; varying vec4 vertexColor;
void main() { gl_Position=vec4(position,0.0,1.0); vertexColor=color; }
""",
        """#version 100
precision mediump float; varying vec4 vertexColor;
void main() { gl_FragColor=vec4(vertexColor.rgb*vertexColor.a,vertexColor.a); }
"""),
    "GLSL 1.20": (
        """#version 120
attribute vec2 position; attribute vec4 color; varying vec4 vertexColor;
void main() { gl_Position=vec4(position,0.0,1.0); vertexColor=color; }
""",
        """#version 120
varying vec4 vertexColor;
void main() { gl_FragColor=vec4(vertexColor.rgb*vertexColor.a,vertexColor.a); }
"""),
    "GLSL 1.50 core": (
        """#version 150
in vec2 position; in vec4 color; out vec4 vertexColor;
void main() { gl_Position=vec4(position,0.0,1.0); vertexColor=color; }
""",
        """#version 150
in vec4 vertexColor; out vec4 outputColor;
void main() { outputColor=vec4(vertexColor.rgb*vertexColor.a,vertexColor.a); }
"""),
}


class TriangleRenderer:
    def __init__(self):
        self.functions = None
        self.program = None
        self.vertices = None
        self.indices = None
        self.vao = None
        self.details = {}

    def initialize(self, context):
        """Create resources in ``context``, which must be current."""
        es = context.isOpenGLES()
        core = not es and context.format().profile() == QSurfaceFormat.CoreProfile
        dialect = "GLSL ES 1.00" if es else "GLSL 1.50 core" if core else "GLSL 1.20"
        self.functions = gl = resolve_from_context(context)
        self.details = {
            "vendor": gl.get_string(0x1F00), "renderer": gl.get_string(0x1F01),
            "gl_version": gl.get_string(0x1F02), "glsl_version": gl.get_string(0x8B8C),
            "api": "OpenGL ES" if es else "OpenGL core" if core else "OpenGL",
            "shaders": dialect, "functions": "ctypes via QOpenGLContext.getProcAddress",
        }
        vertex, fragment = SHADERS[dialect]
        self.program = QOpenGLShaderProgram()
        for kind, source in ((QOpenGLShader.Vertex, vertex), (QOpenGLShader.Fragment, fragment)):
            if not self.program.addShaderFromSourceCode(kind, source):
                raise CapabilityError("Probe shader failed: " + self.program.log())
        self.program.bindAttributeLocation("position", 0)
        self.program.bindAttributeLocation("color", 1)
        if not self.program.link():
            raise CapabilityError("Probe shader link failed: " + self.program.log())
        # A VAO is mandatory in core contexts and useful where available otherwise.
        self.vao = QOpenGLVertexArrayObject()
        if not self.vao.create() and core:
            raise CapabilityError("Cannot create the required OpenGL vertex array.")
        if self.vao.isCreated():
            self.vao.bind()
        self.vertices = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self.indices = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
        # Top red, lower-left green, lower-right blue. 75% alpha detects
        # accidental premultiplication as well as channel order and inversion.
        data = struct.pack("=18f", 0, .75, 1, 0, 0, .75,
                           -.75, -.65, 0, 1, 0, .75,
                           .75, -.65, 0, 0, 1, .75)
        index_data = struct.pack("=3H", 0, 1, 2)
        for buffer, payload in ((self.vertices, data), (self.indices, index_data)):
            if not buffer.create() or not buffer.bind():
                raise CapabilityError("Cannot create/bind an OpenGL probe buffer.")
            buffer.setUsagePattern(QOpenGLBuffer.StaticDraw)
            buffer.allocate(payload, len(payload))
        self.vertices.release()
        if self.vao.isCreated():
            self.vao.release()
        self.indices.release()
        self._check_error("buffer upload")

    def _check_error(self, stage):
        error = self.functions.glGetError()
        if error:
            raise CapabilityError("OpenGL error 0x{:04x} during {}".format(error, stage))

    def draw(self, width, height, transparent=False):
        gl = self.functions
        gl.glViewport(0, 0, width, height)
        for flag in (0x0B71, 0x0B44, 0x0C11, 0x0BD0):
            gl.glDisable(flag)  # depth, cull, scissor, dither
        # Over transparent black this stores exactly the premultiplied source;
        # over the opaque preview background it composites to alpha 1.
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        gl.glColorMask(1, 1, 1, 1)
        if transparent:
            gl.glClearColor(0, 0, 0, 0)
        else:
            gl.glClearColor(.09, .105, .135, 1)
        gl.glClear(GL_COLOR_BUFFER_BIT)
        if self.vao.isCreated():
            self.vao.bind()
        if not self.program.bind() or not self.vertices.bind() or not self.indices.bind():
            raise CapabilityError("Cannot bind probe geometry.")
        try:
            self.program.enableAttributeArray(0)
            self.program.enableAttributeArray(1)
            self.program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 24)
            self.program.setAttributeBuffer(1, GL_FLOAT, 8, 4, 24)
            # A null pointer is offset zero into the bound index buffer.
            gl.glDrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, None)
            self._check_error("indexed triangle draw")
        finally:
            self.program.disableAttributeArray(0)
            self.program.disableAttributeArray(1)
            self.program.release()
            self.vertices.release()
            if self.vao.isCreated():
                self.vao.release()
            self.indices.release()

    def render_image(self, width, height):
        """Return a top-down QImage in a premultiplied format."""
        from .pixel_transfer import validate_dimensions
        validate_dimensions(width, height)
        if max(width, height) > self.functions.get_integer(GL_MAX_TEXTURE_SIZE):
            raise CapabilityError("Output exceeds this GPU's maximum texture size.")
        # Qt's default internal format is RGBA8 on desktop GL and RGBA on ES.
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.NoAttachment)
        fmt.setSamples(0)
        fbo = QOpenGLFramebufferObject(width, height, fmt)
        try:
            if not fbo.isValid() or not fbo.bind():
                raise CapabilityError("Cannot allocate the transparent offscreen framebuffer.")
            self.draw(width, height, transparent=True)
            # OpenGL bottom-up -> explicit Qt top-down.
            image = fbo.toImage(True)
            if image.isNull() or (image.width(), image.height()) != (width, height):
                raise CapabilityError("Framebuffer readback returned an empty or incorrectly sized image.")
            self._check_error("framebuffer readback")
            return image
        finally:
            # QOpenGLWidget owns a nonzero default FBO. Qt restores its default,
            # never an assumed raw framebuffer zero. Destroy while current.
            QOpenGLFramebufferObject.bindDefault()
            del fbo

    def destroy(self):
        """Release resources; the owning context must be current."""
        for buffer in (self.vertices, self.indices):
            if buffer is not None and buffer.isCreated():
                buffer.destroy()
        if self.vao is not None and self.vao.isCreated():
            self.vao.destroy()
        if self.program is not None:
            self.program.removeAllShaders()
        self.program = self.vertices = self.indices = self.vao = None
        self.functions = None
