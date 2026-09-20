"""Create KSP paint layers using only Krita's public API.

No operation targets an existing user's node. Document identity comes from
its root UUID because separate Python wrappers may reference the same image.
The API does not promise an undo transaction for these calls; none is claimed.
"""

from dataclasses import dataclass

from ..render.pixel_transfer import PixelTransferError, validate_dimensions


SUPPORTED_PROFILES = frozenset((
    "sRGB-elle-V2-srgbtrc.icc",
    "sRGB-elle-V4-srgbtrc.icc",
    # LCMS cmsCreate_sRGBProfile(): Rec.709 primaries, D65, IEC 61966-2.1 curve.
    # Krita assigns it to images opened or pasted without an embedded profile.
    "sRGB built-in",
))
GUIDE_LAYER_NAME = "KSP Figure Guide"
LINEART_LAYER_NAME = "KSP Lineart"
PROBE_LAYER_NAME = "KSP Feasibility Triangle"
# Layers KSP may rewrite in place; anything else in the document is never touched.
KSP_LAYER_NAMES = frozenset((GUIDE_LAYER_NAME, LINEART_LAYER_NAME, PROBE_LAYER_NAME))
CONVERT_HINT = (
    " Convert it with Image > Convert Image Color Space… to RGB/Alpha, "
    "8-bit integer, sRGB-elle-V2-srgbtrc.icc.")


class DocumentExportError(RuntimeError):
    """The destination is unsuitable or the layer could not be created."""


@dataclass(frozen=True)
class DocumentSnapshot:
    width: int
    height: int
    color_model: str
    color_depth: str
    color_profile: str
    root_id: str


def _root_id(document):
    if document is None:
        raise DocumentExportError("Open a document before creating a KSP layer.")
    root = document.rootNode()
    if root is None:
        raise DocumentExportError("The destination document has been closed.")
    identifier = root.uniqueId()
    if hasattr(identifier, "isNull") and identifier.isNull():
        raise DocumentExportError("The destination document has no valid root layer.")
    value = identifier.toString() if hasattr(identifier, "toString") else str(identifier)
    if not value:
        raise DocumentExportError("The destination document has no valid identity.")
    return value


def read_document(document):
    """Describe an open document without judging its size or color space."""
    try:
        identity = _root_id(document)
        return DocumentSnapshot(
            document.width(), document.height(), document.colorModel(),
            document.colorDepth(), document.colorProfile(), identity,
        )
    except (RuntimeError, AttributeError) as error:
        if isinstance(error, DocumentExportError):
            raise
        raise DocumentExportError("The document cannot be used: {}".format(error)) from error


def snapshot_document(document):
    """Capture and validate the target before rendering allocates any buffers."""
    snapshot = read_document(document)
    try:
        validate_dimensions(snapshot.width, snapshot.height)
    except PixelTransferError as error:
        raise DocumentExportError("The document cannot be used: {}".format(error)) from error
    if snapshot.color_model != "RGBA" or snapshot.color_depth != "U8":
        raise DocumentExportError(
            "KSP layers require an RGB/Alpha, 8-bit integer document. "
            "This one is {}/{}.".format(snapshot.color_model, snapshot.color_depth)
            + CONVERT_HINT
        )
    if snapshot.color_profile not in SUPPORTED_PROFILES:
        raise DocumentExportError(
            "KSP layers require an sRGB profile ({}). The current profile is {}."
            .format(", ".join(sorted(SUPPORTED_PROFILES)),
                    snapshot.color_profile or "unknown")
            + CONVERT_HINT
        )
    return snapshot


def _assert_current(document, snapshot, application):
    try:
        if _root_id(application.activeDocument()) != snapshot.root_id:
            raise DocumentExportError("The active document changed. Render again in the intended document.")
        if not any(_root_id(item) == snapshot.root_id for item in application.documents()):
            raise DocumentExportError("The destination document is no longer open.")
        if snapshot_document(document) != snapshot:
            raise DocumentExportError("The document dimensions or color space changed. Render again.")
    except (AttributeError, RuntimeError) as error:
        if isinstance(error, DocumentExportError):
            raise
        raise DocumentExportError("Could not confirm the destination document is still available.") from error


def _prepare(document, bgra_bytes, width, height, snapshot, application):
    """Shared checks for writing a render into a document."""
    try:
        expected_size = validate_dimensions(width, height)
    except PixelTransferError as error:
        raise DocumentExportError(str(error)) from error
    if not isinstance(bgra_bytes, bytes) or len(bgra_bytes) != expected_size:
        raise DocumentExportError("The render must contain exactly width * height * 4 BGRA bytes.")
    current = snapshot_document(document)
    snapshot = current if snapshot is None else snapshot
    if current != snapshot:
        raise DocumentExportError("The document changed while rendering. Render again.")
    if application is None:
        from krita import Krita
        application = Krita.instance()
    _assert_current(document, snapshot, application)
    return snapshot, application


def layer_id(node):
    """A node's identity as text, for remembering a layer KSP created."""
    try:
        identifier = node.uniqueId()
    except (AttributeError, RuntimeError):
        return ""
    if identifier is None:
        return ""
    return identifier.toString() if hasattr(identifier, "toString") else str(identifier)


def find_layer(document, target):
    """The KSP layer with this id, or ``None`` if it is gone.

    Only layers KSP itself created are ever returned: the id must match and
    the node must still be one of KSP's own paint layers, so a renamed or
    replaced node is treated as gone and a new layer is created instead.
    """
    if not target:
        return None
    try:
        root = document.rootNode()
        children = root.childNodes() if root is not None else []
    except (AttributeError, RuntimeError):
        return None
    for node in children:
        try:
            if layer_id(node) != target or node.type() != "paintlayer":
                continue
            if node.name() not in KSP_LAYER_NAMES:
                return None  # Renamed by the user: leave it alone.
        except (AttributeError, RuntimeError):
            continue
        return node
    return None


def update_layer(document, target, bgra_bytes, width, height, *, snapshot=None,
                 origin=(0, 0), opacity=1.0, application=None):
    """Rewrite the pixels of a KSP layer created earlier.

    Returns the node, or ``None`` when that layer is gone and the caller
    should create a new one instead. Never touches any other node.
    """
    node = find_layer(document, target)
    if node is None:
        return None
    snapshot, application = _prepare(document, bgra_bytes, width, height, snapshot, application)
    x, y = _origin(origin)
    try:
        if not node.setPixelData(bgra_bytes, x, y, width, height):
            raise DocumentExportError("Krita could not write the rendered pixels.")
        _apply_opacity(node, opacity)
        document.refreshProjection()
        return node
    except Exception as error:
        message = str(error) if isinstance(error, DocumentExportError) else (
            "Krita could not update the KSP layer: {}".format(error))
        raise DocumentExportError(message) from error


def _origin(origin):
    try:
        x, y = origin
    except (TypeError, ValueError) as error:
        raise DocumentExportError("The layer origin must be two whole numbers.") from error
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
        raise DocumentExportError("The layer origin must be two whole numbers.")
    return x, y


def _apply_opacity(node, opacity):
    """Krita stores layer opacity as 0-255; only KSP's own layers are set."""
    if opacity is None:
        return
    value = min(255, max(0, int(round(float(opacity) * 255))))
    if value != 255:
        node.setOpacity(value)


def export_layer(document, bgra_bytes, width, height, *, name, snapshot=None, application=None,
                 origin=(0, 0), opacity=1.0):
    """Add one new paint layer, returning it on success.

    Pass the ``snapshot`` captured before rendering. All pixel writes are to a
    freshly created, detached paint layer; only then is it added above the top
    existing layer. Failed attachment/refresh removes only that newly created
    node. ``origin`` places the render in the document, which may be outside
    the canvas when the output is a custom size. The optional application
    argument supports pure-Python API tests. Call this synchronously from the
    Krita UI thread.
    """
    snapshot, application = _prepare(document, bgra_bytes, width, height, snapshot, application)
    x, y = _origin(origin)

    node = None
    attachment_attempted = False
    try:
        node = document.createNode(name, "paintlayer")
        if node is None or node.type() != "paintlayer":
            raise DocumentExportError("Krita could not create a paint layer.")
        # createNode promises the document's color space. Check it before handing
        # native code a four-byte-per-pixel buffer: a different depth is unsafe.
        if (node.colorModel(), node.colorDepth(), node.colorProfile()) != (
                snapshot.color_model, snapshot.color_depth, snapshot.color_profile):
            raise DocumentExportError("Krita created a layer with an unexpected color space.")
        _assert_current(document, snapshot, application)
        if not node.setPixelData(bgra_bytes, x, y, width, height):
            raise DocumentExportError("Krita could not write the rendered pixels.")
        _apply_opacity(node, opacity)
        _assert_current(document, snapshot, application)
        root = document.rootNode()
        children = root.childNodes()
        above = children[-1] if children else None
        attachment_attempted = True
        if not root.addChildNode(node, above):
            raise DocumentExportError("Krita could not add the rendered layer to the document.")
        document.refreshProjection()
        return node
    except Exception as error:
        cleanup_error = None
        if node is not None and attachment_attempted:
            try:
                # The retained object is the only node this operation can remove.
                if node.parentNode() is not None and not node.remove():
                    cleanup_error = "Krita refused to remove the incomplete KSP layer."
                document.refreshProjection()
            except Exception as cleanup:
                cleanup_error = "Cleanup could not finish: {}".format(cleanup)
        message = str(error) if isinstance(error, DocumentExportError) else (
            "Krita could not create the KSP layer: {}".format(error)
        )
        if cleanup_error:
            message += " " + cleanup_error
        raise DocumentExportError(message) from error
