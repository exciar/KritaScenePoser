"""ctypes binding tests with native callbacks standing in for a GL driver.

Each fake entry point is a real C-callable function pointer built from the
same ABI, so arguments cross the ctypes boundary exactly as with a driver.
"""

import ctypes
import types
import unittest

from krita_scene_poser.render.gl_functions import (
    FUNCTYPE, PROTOTYPES, CapabilityError, GLFunctions, resolve_from_context,
)


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.renderer = ctypes.create_string_buffer(b"Fake GPU")
        self.callbacks = {}
        for name, (restype, argtypes) in PROTOTYPES.items():
            # Callbacks cannot safely return c_char_p; return the address.
            callback_restype = ctypes.c_void_p if restype is ctypes.c_char_p else restype
            self.callbacks[name] = FUNCTYPE(callback_restype, *argtypes)(self._handler(name))

    def _handler(self, name):
        def handler(*args):
            if name == "glGetIntegerv":
                args[1][0] = 16384
                args = args[:1]
            elif name == "glUniform4fv":
                args = (args[0], args[1], [args[2][i] for i in range(4 * args[1])])
            elif name == "glUniformMatrix4fv":
                args = (args[0], args[1], args[2], [args[3][i] for i in range(16)])
            self.calls.append((name, args))
            if name == "glGetString":
                return ctypes.addressof(self.renderer) if args[0] == 0x1F01 else None
            if name == "glGetError":
                return 0x0502
            return None
        return handler

    def resolve(self, name):
        callback = self.callbacks.get(name)
        return ctypes.cast(callback, ctypes.c_void_p).value if callback else 0


class GLFunctionTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.gl = GLFunctions(self.driver.resolve)

    def test_arguments_cross_the_ctypes_boundary(self):
        self.gl.glViewport(0, 1, 257, 193)
        self.gl.glClearColor(0, .5, 1, .25)
        self.gl.glColorMask(1, 1, 1, 1)
        self.gl.glDrawElements(4, 3, 0x1403, None)
        self.assertEqual(self.driver.calls[0], ("glViewport", (0, 1, 257, 193)))
        self.assertEqual(self.driver.calls[1], ("glClearColor", (0.0, 0.5, 1.0, 0.25)))
        self.assertEqual(self.driver.calls[2], ("glColorMask", (1, 1, 1, 1)))
        self.assertEqual(self.driver.calls[3], ("glDrawElements", (4, 3, 0x1403, None)))

    def test_uniform_arrays_and_matrices_cross_as_pointers(self):
        self.gl.uniform_vec4_array(7, [1, 2, 3, 4, 5, 6, 7, 8])
        self.gl.uniform_matrix4(9, list(range(16)))
        self.gl.glUniform3f(2, 0.5, 0.25, 1.0)
        self.gl.glDrawArrays(1, 0, 68)
        self.assertEqual(self.driver.calls[0], ("glUniform4fv", (7, 2, [1, 2, 3, 4, 5, 6, 7, 8])))
        self.assertEqual(self.driver.calls[1], ("glUniformMatrix4fv", (9, 1, 0, list(range(16)))))
        self.assertEqual(self.driver.calls[2], ("glUniform3f", (2, 0.5, 0.25, 1.0)))
        self.assertEqual(self.driver.calls[3], ("glDrawArrays", (1, 0, 68)))

    def test_query_helpers(self):
        self.assertEqual(self.gl.get_integer(0x0D33), 16384)
        self.assertEqual(self.gl.get_string(0x1F01), "Fake GPU")
        self.assertEqual(self.gl.get_string(0x1F00), "unavailable")
        self.assertEqual(self.gl.glGetError(), 0x0502)

    def test_missing_entry_points_are_named(self):
        def resolve(name):
            return 0 if name in ("glClear", "glDrawElements") else self.driver.resolve(name)
        with self.assertRaisesRegex(CapabilityError, "glClear, glDrawElements"):
            GLFunctions(resolve)

    def test_context_resolution_encodes_names_and_handles_null(self):
        requested = []

        def get_proc_address(name):
            requested.append(name)
            return self.driver.resolve(name.decode("ascii")) or None

        resolve_from_context(types.SimpleNamespace(getProcAddress=get_proc_address))
        self.assertEqual(requested, [name.encode("ascii") for name in PROTOTYPES])
        with self.assertRaises(CapabilityError):
            resolve_from_context(types.SimpleNamespace(getProcAddress=lambda name: None))


if __name__ == "__main__":
    unittest.main()
