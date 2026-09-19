"""KSP docker viewport: draws the shared pose session and forwards input.

Posing logic lives in ``core.interaction`` and ``ui.session``; this widget
only draws and translates events. It redraws only when Qt requests a paint
(no timer). Reparenting (float/dock) may recreate the GL context, so
resources are released on ``aboutToBeDestroyed`` and rebuilt in
``initializeGL``. PyQt5 aborts the host process when an exception escapes a
virtual override, so every override reports failures instead.
"""

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QSurfaceFormat
from PyQt5.QtWidgets import QOpenGLWidget

from .. import log
from ..core.editor import RINGS
from ..core.interaction import PoseInteraction, Screen
from ..render.figure_renderer import FigureRenderer
from ..render.gl_renderer import CapabilityError
from ..render.shaders import snapshot
from .keys import shortcut
from .overlay_paint import paint_primitives


class _EmptySkeleton:
    """Stands in before a figure loads, so the grid still draws."""

    joints = ()

    @staticmethod
    def skinning_matrices(pose):
        return ()


class FigureViewport(QOpenGLWidget):
    status_changed = pyqtSignal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        surface = QSurfaceFormat.defaultFormat()
        surface.setDepthBufferSize(24)
        surface.setSamples(4)
        self.setFormat(surface)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(200, 240)
        self.session = session
        session.changed.connect(self.update)
        self.renderer = FigureRenderer()
        self.interaction = None
        self.ready = False
        self.error = None

    def _interaction(self):
        if self.interaction is None and self.session.editor is not None:
            self.interaction = PoseInteraction(self.session.editor, self.session.camera)
        return self.interaction

    def _screen(self):
        return Screen(max(1, self.width()), max(1, self.height()), 1.0)

    def details(self):
        details = dict(self.renderer.details)
        if self.session.figure_id:
            details["figure"] = self.session.figure_id
        return details

    # GL lifecycle ---------------------------------------------------------------

    def initializeGL(self):
        self.ready = False
        try:
            context = self.context()
            context.aboutToBeDestroyed.connect(self._release)
            self.renderer.initialize(context)
        except Exception as error:
            self._fail(error)
            return
        self.ready, self.error = True, None
        log.event("viewport_initialized", **self.renderer.details)
        self.status_changed.emit("Viewport ready: {}.".format(self.renderer.details.get("renderer", "OpenGL")))

    def paintGL(self):
        if not self.ready:
            return
        session = self.session
        try:
            ratio = self.devicePixelRatioF()
            width, height = max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio))
            matrix = session.camera.view_projection(self.width() / max(1, self.height()))
            if session.editor is not None and session.mesh is not None:
                self.renderer.set_mesh(session.figure_id, session.mesh)
                shot = snapshot(session.editor.skeleton, session.editor.pose, matrix,
                                session.editor.selected)
            else:
                shot = snapshot(_EmptySkeleton(), None, matrix)
            self.renderer.draw(shot, width, height)
            self.renderer.reset_state()
        except Exception as error:
            self._fail(error)
            return
        try:
            self._paint_overlay()
        except Exception as error:
            self.status_changed.emit("Overlay failed: {}: {}".format(type(error).__name__, error))

    def _fail(self, error):
        """Record a failure raised while this widget's context is current."""
        self.ready = False
        self.error = error if isinstance(error, CapabilityError) else CapabilityError(
            "{}: {}".format(type(error).__name__, error))
        try:
            self.renderer.destroy()
        except Exception:
            pass
        log.event("viewport_failed", error=self.error)
        self.status_changed.emit("OpenGL unavailable: {}".format(self.error))

    def _release(self):
        self.ready = False
        if self.renderer.functions is None:
            return
        log.event("viewport_context_released")
        try:
            self.makeCurrent()
            self.renderer.destroy()
            self.doneCurrent()
        except Exception:
            # Never free GL names without a current context; the dying context
            # reclaims them. Only drop the Python wrappers.
            self.renderer = FigureRenderer()

    # Input ---------------------------------------------------------------------

    def mousePressEvent(self, event):
        try:
            self.setFocus(Qt.MouseFocusReason)
            interaction = self._interaction()
            if interaction is None:
                return
            button, modifiers = event.button(), event.modifiers()
            alt = bool(modifiers & Qt.AltModifier)
            shift = bool(modifiers & Qt.ShiftModifier)
            ctrl = bool(modifiers & Qt.ControlModifier)
            if button == Qt.RightButton or (button == Qt.LeftButton and alt and not shift):
                interaction.begin_camera("orbit", event.x(), event.y())
            elif button == Qt.MiddleButton or (button == Qt.LeftButton and alt and shift):
                interaction.begin_camera("pan", event.x(), event.y())
            elif button == Qt.LeftButton:
                interaction.press(event.x(), event.y(), self._screen(), shift, ctrl)
            self.session.dragging = interaction.drag is not None
            self.session.notify()
            event.accept()
        except Exception as error:
            self._report(error)

    def mouseMoveEvent(self, event):
        try:
            if self.interaction is not None and self.interaction.move(
                    event.x(), event.y(), self._screen()):
                self.session.notify()
        except Exception as error:
            self._report(error)

    def mouseReleaseEvent(self, event):
        try:
            if event.buttons() != Qt.NoButton or self.interaction is None:
                return
            if self.interaction.release():
                editor = self.session.editor
                log.event("pose_edited", joint=editor.joint_name(editor.selected))
            self.session.dragging = False
            self.session.notify()
        except Exception as error:
            self._report(error)

    def wheelEvent(self, event):
        try:
            self.session.camera.zoom(event.angleDelta().y() / 120.0)
            self.session.notify()
        except Exception as error:
            self._report(error)

    def event(self, event):
        # Claim KSP's keys while focused, before Krita's window shortcuts.
        try:
            if event.type() == QEvent.ShortcutOverride and shortcut(event) is not None:
                event.accept()
                return True
        except Exception as error:
            self._report(error)
        return super().event(event)

    def keyPressEvent(self, event):
        try:
            action = shortcut(event)
            if action is None:
                super().keyPressEvent(event)
                return
            if action == "cancel" and self.interaction is not None:
                self.interaction.cancel()
            self.session.perform(action)
        except Exception as error:
            self._report(error)

    def _report(self, error):
        self.status_changed.emit("{}: {}".format(type(error).__name__, error))

    # Overlay -------------------------------------------------------------------

    def _paint_overlay(self):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            editor = self.session.editor
            mode = "Rings" if editor is not None and editor.mode == RINGS else "Drag"
            projection = "Orthographic" if self.session.camera.orthographic else "Perspective"
            label = self.session.rig.display_name if self.session.rig is not None else ""
            painter.setPen(QColor(210, 214, 222))
            painter.drawText(10, 20, "{}  ·  {} mode  ·  {}".format(label, mode, projection))
            interaction = self._interaction()
            if interaction is not None:
                paint_primitives(painter, interaction.overlay(self._screen()))
        finally:
            painter.end()
