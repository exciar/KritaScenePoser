"""Minimal OpenGL entry points through Qt's resolver and stdlib ctypes.

Krita on Windows defaults to ANGLE (OpenGL ES), for which its bundled PyQt5
has no function wrapper. Resolving a fixed set of entry points with
``QOpenGLContext.getProcAddress`` works for ANGLE and desktop OpenGL alike.

A wrong ctypes prototype crashes the host instead of raising. Every entry point
is declared once below from the OpenGL ES 2.0 / OpenGL 2.0 C prototypes; add
one only with the same review, and only call them while the owning context is
current.
"""

import ctypes
import sys

GLenum = GLbitfield = GLuint = ctypes.c_uint
GLint = GLsizei = ctypes.c_int
GLfloat = ctypes.c_float
GLboolean = ctypes.c_ubyte

# GL entry points use the stdcall ABI on Windows (identical to cdecl on x64).
FUNCTYPE = ctypes.WINFUNCTYPE if sys.platform == "win32" else ctypes.CFUNCTYPE

PROTOTYPES = {
    # const GLubyte *glGetString(GLenum name)
    "glGetString": (ctypes.c_char_p, (GLenum,)),
    # GLenum glGetError(void)
    "glGetError": (GLenum, ()),
    # void glGetIntegerv(GLenum pname, GLint *data)
    "glGetIntegerv": (None, (GLenum, ctypes.POINTER(GLint))),
    # void glViewport(GLint x, GLint y, GLsizei width, GLsizei height)
    "glViewport": (None, (GLint, GLint, GLsizei, GLsizei)),
    # void glEnable(GLenum cap) / glDisable(GLenum cap)
    "glEnable": (None, (GLenum,)),
    "glDisable": (None, (GLenum,)),
    # void glBlendFunc(GLenum sfactor, GLenum dfactor)
    "glBlendFunc": (None, (GLenum, GLenum)),
    # void glColorMask(GLboolean r, GLboolean g, GLboolean b, GLboolean a)
    "glColorMask": (None, (GLboolean, GLboolean, GLboolean, GLboolean)),
    # void glClearColor(GLfloat r, GLfloat g, GLfloat b, GLfloat a)
    "glClearColor": (None, (GLfloat, GLfloat, GLfloat, GLfloat)),
    # void glClear(GLbitfield mask)
    "glClear": (None, (GLbitfield,)),
    # void glDrawElements(GLenum mode, GLsizei count, GLenum type, const void *indices)
    "glDrawElements": (None, (GLenum, GLsizei, GLenum, ctypes.c_void_p)),
    # void glDrawArrays(GLenum mode, GLint first, GLsizei count)
    "glDrawArrays": (None, (GLenum, GLint, GLsizei)),
    # void glDepthFunc(GLenum func)
    "glDepthFunc": (None, (GLenum,)),
    # void glDepthMask(GLboolean flag)
    "glDepthMask": (None, (GLboolean,)),
    # void glUniform1f(GLint location, GLfloat v0)
    "glUniform1f": (None, (GLint, GLfloat)),
    # void glUniform1i(GLint location, GLint v0)
    "glUniform1i": (None, (GLint, GLint)),
    # void glUniform2f(GLint location, GLfloat v0, GLfloat v1)
    "glUniform2f": (None, (GLint, GLfloat, GLfloat)),
    # void glActiveTexture(GLenum texture)
    "glActiveTexture": (None, (GLenum,)),
    # void glBindTexture(GLenum target, GLuint texture)
    "glBindTexture": (None, (GLenum, GLuint)),
    # void glUniform3f(GLint location, GLfloat v0, GLfloat v1, GLfloat v2)
    "glUniform3f": (None, (GLint, GLfloat, GLfloat, GLfloat)),
    # void glUniform4f(GLint location, GLfloat v0, GLfloat v1, GLfloat v2, GLfloat v3)
    "glUniform4f": (None, (GLint, GLfloat, GLfloat, GLfloat, GLfloat)),
    # void glUniform4fv(GLint location, GLsizei count, const GLfloat *value)
    "glUniform4fv": (None, (GLint, GLsizei, ctypes.POINTER(GLfloat))),
    # void glUniformMatrix4fv(GLint location, GLsizei count, GLboolean transpose, const GLfloat *value)
    "glUniformMatrix4fv": (None, (GLint, GLsizei, GLboolean, ctypes.POINTER(GLfloat))),
}


class CapabilityError(RuntimeError):
    """The host's OpenGL cannot provide an operation KSP requires."""


class GLFunctions:
    """Callable entry points for one context; discard when it is destroyed."""

    def __init__(self, resolve):
        """``resolve(name)`` returns an entry point address, or 0 if absent."""
        missing = []
        for name, (restype, argtypes) in PROTOTYPES.items():
            address = resolve(name)
            if not address:
                missing.append(name)
                continue
            setattr(self, name, FUNCTYPE(restype, *argtypes)(address))
        if missing:
            raise CapabilityError(
                "This OpenGL driver does not provide: " + ", ".join(missing))

    def get_integer(self, name):
        value = GLint(0)
        self.glGetIntegerv(name, ctypes.byref(value))
        return value.value

    def get_string(self, name):
        value = self.glGetString(name)
        return value.decode("utf-8", "replace") if value else "unavailable"

    def uniform_vec4_array(self, location, values):
        """Upload ``len(values) / 4`` vec4s from a flat float sequence."""
        data = (GLfloat * len(values))(*values)
        self.glUniform4fv(location, len(values) // 4, data)

    def uniform_matrix4(self, location, column_major):
        self.glUniformMatrix4fv(location, 1, 0, (GLfloat * 16)(*column_major))


def resolve_from_context(context):
    """Bind the entry points of a current QOpenGLContext."""
    def resolve(name):
        pointer = context.getProcAddress(name.encode("ascii"))
        return int(pointer) if pointer is not None else 0
    return GLFunctions(resolve)
