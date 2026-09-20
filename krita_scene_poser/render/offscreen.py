"""Figure rendering in KSP's own offscreen GL context.

Serves the canvas overlay, Create Layer, and Self-Test, so none of them
depends on the docker viewport being visible. It uses the same pattern as the
headless probe: a ``QOffscreenSurface`` plus a ``QOpenGLContext`` that shares
Krita's global share context. Call it only from the UI thread. It leaves no
context current afterwards; Krita's widgets make their own contexts current
before drawing.
"""

from PyQt5.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat

from .figure_renderer import FigureRenderer
from .gl_renderer import TriangleRenderer
from .gl_functions import CapabilityError
from .pixel_transfer import validate_probe


class OffscreenRenderer:
    def __init__(self):
        self.surface = self.context = None
        self.figures = FigureRenderer()
        self.probe = TriangleRenderer()
        self.probe_ready = False

    @property
    def details(self):
        return dict(self.figures.details)

    def _make_current(self):
        if self.context is None or not self.context.isValid():
            self.destroy()
            fmt = QSurfaceFormat.defaultFormat()
            surface = QOffscreenSurface()
            surface.setFormat(fmt)
            surface.create()
            if not surface.isValid():
                raise CapabilityError("KSP could not create an offscreen drawing surface.")
            context = QOpenGLContext()
            context.setFormat(fmt)
            share = QOpenGLContext.globalShareContext()
            if share is not None:
                context.setShareContext(share)
            if not context.create():
                raise CapabilityError("KSP could not create an offscreen OpenGL context.")
            self.surface, self.context = surface, context
            if not context.makeCurrent(surface):
                raise CapabilityError("KSP could not activate its offscreen OpenGL context.")
            try:
                self.figures.initialize(context)
            except Exception:
                context.doneCurrent()
                self.destroy()  # Never leave a half-built context behind.
                raise
            return
        if not self.context.makeCurrent(self.surface):
            raise CapabilityError("KSP could not activate its offscreen OpenGL context.")

    def render(self, figure_id, mesh, snapshot, width, height, mode="shaded", lines=None,
               keep_buffers=True, scale_to=None):
        """Top-down, premultiplied QImage of the figure on a transparent background.

        ``keep_buffers=False`` frees the line-art buffers afterwards (large exports).
        ``scale_to`` scales a supersampled render down to the output size.
        """
        self._make_current()
        try:
            self.figures.set_mesh(figure_id, mesh)
            return self.figures.render_image(snapshot, width, height, mode, lines, scale_to)
        finally:
            if not keep_buffers:
                self.figures.release_gbuffers()
            self.context.doneCurrent()

    def self_test(self):
        """Render and validate the Phase 0 probe; adds nothing to any document."""
        self._make_current()
        try:
            if not self.probe_ready:
                self.probe.initialize(self.context)
                self.probe_ready = True
            return validate_probe(self.probe.render_image(257, 193))
        finally:
            self.context.doneCurrent()

    def destroy(self):
        if self.context is not None and self.context.isValid() and self.surface is not None:
            if self.context.makeCurrent(self.surface):
                for renderer in (self.figures, self.probe):
                    try:
                        renderer.destroy()
                    except Exception:
                        pass
                self.context.doneCurrent()
        self.figures, self.probe, self.probe_ready = FigureRenderer(), TriangleRenderer(), False
        self.surface = self.context = None
