"""Byte-layout and probe fixtures; these do not require Qt or an OpenGL driver."""

import sys
import types
import unittest
from unittest.mock import patch

from krita_scene_poser.render.pixel_transfer import (
    PixelTransferError, qimage_to_bgra, rgba_to_bgra, validate_dimensions,
    validate_probe_pixels,
)


class PixelTransferTests(unittest.TestCase):
    def test_distinct_channels_alpha_and_odd_width(self):
        rgba = bytes((255, 1, 2, 0, 3, 240, 5, 128, 6, 7, 230, 255))
        self.assertEqual(rgba_to_bgra(rgba, 3, 1), bytes((
            2, 1, 255, 0, 5, 240, 3, 128, 230, 7, 6, 255
        )))

    def test_padding_is_removed_and_top_down_order_retained(self):
        rgba = bytes((1, 2, 3, 4, 99, 99, 99, 99, 5, 6, 7, 8, 99, 99, 99, 99))
        self.assertEqual(rgba_to_bgra(rgba, 1, 2, 8), bytes((3, 2, 1, 4, 7, 6, 5, 8)))

    def test_direct_gl_bottom_up_conversion(self):
        self.assertEqual(rgba_to_bgra(bytes((5, 6, 7, 8, 1, 2, 3, 4)), 1, 2, bottom_up=True),
                         bytes((3, 2, 1, 4, 7, 6, 5, 8)))

    def test_invalid_dimensions_rejected_before_allocation(self):
        for dimensions in ((0, 1), (-1, 1), (1, 0), (True, 1), (1.0, 1), (4097, 1), (1, 4097)):
            with self.subTest(dimensions=dimensions), self.assertRaises(PixelTransferError):
                validate_dimensions(*dimensions)

    def test_invalid_buffer_and_stride(self):
        for data, stride in ((b"", 4), (b"12345", 4), (b"1234", 3), (b"1234", 4.0),
                             (None, 4), ("1234", 4)):
            with self.subTest(data=data, stride=stride), self.assertRaises(PixelTransferError):
                rgba_to_bgra(data, 1, 1, stride)

    def test_qimage_requests_byte_ordered_straight_rgba(self):
        class Image:
            def __init__(self):
                self.requested = None

            def isNull(self):
                return False

            def width(self):
                return 1

            def height(self):
                return 2

            def convertToFormat(self, value):
                self.requested = value
                return self

            def bytesPerLine(self):
                return 8

            def constBits(self):
                return types.SimpleNamespace(asstring=lambda length: bytes((
                    200, 2, 3, 191, 99, 99, 99, 99, 4, 5, 220, 128, 99, 99, 99, 99
                )))

        image = Image()
        fake_gui = types.SimpleNamespace(QImage=types.SimpleNamespace(Format_RGBA8888=17))
        with patch.dict(sys.modules, {"PyQt5.QtGui": fake_gui}):
            self.assertEqual(qimage_to_bgra(image), bytes((3, 2, 200, 191, 220, 5, 4, 128)))
        self.assertEqual(image.requested, 17)

    def test_null_qimage_fails_without_importing_qt(self):
        with self.assertRaises(PixelTransferError):
            qimage_to_bgra(None)


def qt_unpremultiply(value, alpha):
    """Qt's premultiplied-to-straight conversion; its SIMD path saturates."""
    return min(255, (value * 255 + alpha // 2) // alpha)


def triangle_fixture(width=257, height=193, encode=lambda weight: round(weight * 255)):
    """Analytic interpolation reference, independent of Qt and GL.

    ``encode`` maps one interpolated color weight to the final straight byte.
    """
    result = bytearray(width * height * 4)
    for y in range(height):
        red = (1 - 2 * (y + .5) / height + .65) / 1.4
        for x in range(width):
            blue = (1 - red + (2 * (x + .5) / width - 1) / .75) / 2
            green = 1 - red - blue
            if min(red, green, blue) >= 0:
                offset = (y * width + x) * 4
                result[offset:offset + 4] = bytes((
                    encode(blue), encode(green), encode(red), 191
                ))
    return bytes(result)


class ProbeValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.width, cls.height = 257, 193
        cls.pixels = triangle_fixture(cls.width, cls.height)

    def test_asymmetric_rgb_triangle_with_transparent_corners(self):
        measured = validate_probe_pixels(self.pixels, self.width, self.height)
        self.assertTrue(measured["transparent_corners"])
        self.assertGreater(measured["samples"]["top_red"]["rgba"][0], 200)

    def test_red_blue_swap_is_detected(self):
        swapped = rgba_to_bgra(self.pixels, self.width, self.height)
        with self.assertRaises(PixelTransferError):
            validate_probe_pixels(swapped, self.width, self.height)

    def test_vertical_flip_is_detected(self):
        stride = self.width * 4
        flipped = b"".join(self.pixels[row * stride:(row + 1) * stride]
                           for row in reversed(range(self.height)))
        with self.assertRaises(PixelTransferError):
            validate_probe_pixels(flipped, self.width, self.height)

    def test_premultiplied_alpha_is_detected(self):
        premultiplied = bytearray(self.pixels)
        for index in range(0, len(premultiplied), 4):
            for channel in range(3):
                premultiplied[index + channel] = round(premultiplied[index + channel] * .75)
        with self.assertRaises(PixelTransferError):
            validate_probe_pixels(premultiplied, self.width, self.height)

    def test_premultiplied_gl_output_survives_qt_conversion(self):
        # The renderer's contract: GL stores c * a; Qt readback un-premultiplies.
        readback = triangle_fixture(self.width, self.height, lambda weight: qt_unpremultiply(
            round(weight * .75 * 255), 191))
        validate_probe_pixels(readback, self.width, self.height)

    def test_straight_gl_output_mislabelled_premultiplied_is_detected(self):
        # Qt labels RGBA8 framebuffer readback premultiplied regardless of content.
        readback = triangle_fixture(self.width, self.height, lambda weight: qt_unpremultiply(
            round(weight * 255), 191))
        with self.assertRaises(PixelTransferError):
            validate_probe_pixels(readback, self.width, self.height)

    def test_small_image_rejected_with_size_message(self):
        with self.assertRaisesRegex(PixelTransferError, "at least 32 by 32"):
            validate_probe_pixels(bytes(31 * 40 * 4), 31, 40)

    def test_opaque_background_is_detected(self):
        opaque = bytearray(self.pixels)
        opaque[3::4] = bytes((255,)) * (self.width * self.height)
        with self.assertRaises(PixelTransferError):
            validate_probe_pixels(opaque, self.width, self.height)


if __name__ == "__main__":
    unittest.main()
