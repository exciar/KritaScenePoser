"""Conversion from rendered images to Krita's top-down BGRA, straight-alpha layout.

Qt converts premultiplied readback to straight alpha when asked for RGBA8888, which
is why the renderer must write premultiplied pixels. Qt is imported only when a
QImage is converted.
"""

MAX_DIMENSION = 4096
MAX_PIXELS = 16_777_216


class PixelTransferError(ValueError):
    """A render cannot safely be interpreted as RGBA/U8 pixels."""


def validate_dimensions(width, height):
    """Return byte count for a bounded four-channel image; reject before allocation."""
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in (width, height)):
        raise PixelTransferError("Image dimensions must be whole numbers.")
    if width <= 0 or height <= 0:
        raise PixelTransferError("Image dimensions must be positive.")
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise PixelTransferError(
            "The Phase 0 renderer supports canvases up to 4096 by 4096 pixels."
        )
    return width * height * 4


def rgba_to_bgra(data, width, height, bytes_per_line=None, *, bottom_up=False):
    """Pack RGBA bytes as top-down BGRA without changing alpha or color values.

    ``bytes_per_line`` permits padded source rows. ``bottom_up`` is only for
    direct GL byte buffers; QImage readback already uses top-down rows.
    """
    byte_count = validate_dimensions(width, height)
    stride = width * 4 if bytes_per_line is None else bytes_per_line
    if isinstance(stride, bool) or not isinstance(stride, int) or stride < width * 4:
        raise PixelTransferError("The source row stride is invalid.")
    try:
        source = memoryview(data).cast("B")
    except (TypeError, ValueError) as error:
        raise PixelTransferError("Pixel data must be a contiguous byte buffer.") from error
    if source.nbytes != stride * height:
        raise PixelTransferError("The pixel buffer length does not match its dimensions.")

    result = bytearray(byte_count)
    row_size = width * 4
    for row in range(height):
        source_row = height - 1 - row if bottom_up else row
        rgba = source[source_row * stride:source_row * stride + row_size]
        start = row * row_size
        end = start + row_size
        result[start:end:4] = rgba[2::4]
        result[start + 1:end:4] = rgba[1::4]
        result[start + 2:end:4] = rgba[0::4]
        result[start + 3:end:4] = rgba[3::4]
    return bytes(result)


def qimage_to_bgra(image):
    """Convert a non-null QImage to packed, top-down, straight-alpha BGRA."""
    if image is None or image.isNull():
        raise PixelTransferError("The framebuffer did not return an image.")
    validate_dimensions(image.width(), image.height())
    from PyQt5.QtGui import QImage

    converted = image.convertToFormat(QImage.Format_RGBA8888)
    if converted.isNull():
        raise PixelTransferError("Qt could not convert the framebuffer image to RGBA8888.")
    stride = converted.bytesPerLine()
    raw = converted.constBits().asstring(stride * converted.height())
    return rgba_to_bgra(raw, converted.width(), converted.height(), stride)


def validate_probe_pixels(bgra_bytes, width, height):
    """Check the Phase 0 RGB triangle, orientation, and straight transparency.

    Renderer contract (NDC): red (0, .75), green (-.75, -.65),
    blue (.75, -.65), all alpha .75, written premultiplied in GL and
    expected here as straight alpha after Qt's conversion. The tolerance
    absorbs that 8-bit round trip. Tests sample well inside the triangle so
    edge rasterization and antialiasing cannot matter.
    """
    expected_size = validate_dimensions(width, height)
    if width < 32 or height < 32:
        raise PixelTransferError(
            "The Phase 0 readback probe needs a document of at least 32 by 32 pixels.")
    if len(bgra_bytes) != expected_size:
        raise PixelTransferError("The readback probe received an incomplete image.")

    def rgba_at(x, y):
        offset = (y * width + x) * 4
        blue, green, red, alpha = bgra_bytes[offset:offset + 4]
        return red, green, blue, alpha

    corners = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    for x, y in corners:
        if rgba_at(x, y) != (0, 0, 0, 0):
            raise PixelTransferError("Framebuffer corners must be transparent black.")

    results = {}
    for name, u, v in (("top_red", .5, .22), ("lower_left_green", .24, .73),
                       ("lower_right_blue", .76, .73)):
        x, y = int(u * width), int(v * height)
        ndc_x = 2.0 * (x + .5) / width - 1.0
        ndc_y = 1.0 - 2.0 * (y + .5) / height
        red_weight = (ndc_y + .65) / 1.4
        blue_weight = ((1.0 - red_weight) + ndc_x / .75) / 2.0
        green_weight = 1.0 - red_weight - blue_weight
        expected = tuple(round(weight * 255) for weight in (
            red_weight, green_weight, blue_weight
        ))
        actual = rgba_at(x, y)
        if any(abs(actual[channel] - expected[channel]) > 5 for channel in range(3)):
            raise PixelTransferError(
                "Framebuffer {} sample has incorrect RGB values {}; expected {}. "
                "Channel order, orientation, or straight-alpha readback is unsupported."
                .format(name, actual[:3], expected)
            )
        if abs(actual[3] - 191) > 2:
            raise PixelTransferError("Framebuffer {} sample lost its 75% alpha.".format(name))
        results[name] = {"xy": [x, y], "rgba": list(actual)}
    return {"width": width, "height": height, "transparent_corners": True, "samples": results}


def validate_probe(image):
    return validate_probe_pixels(qimage_to_bgra(image), image.width(), image.height())
