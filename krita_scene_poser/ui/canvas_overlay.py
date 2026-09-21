"""Show and pose the figure directly on Krita's canvas.

``CanvasOverlay`` is a transparent child widget laid over Krita's canvas
widget. It draws KSP's render through Krita's own image-to-widget transform,
so canvas zoom, rotation, and mirror apply to the figure. While Pose on Canvas
is off, the overlay ignores the mouse and Krita's tools work as usual. While
it is on, the left button poses the figure or, on empty space, moves the 3D
camera. The wheel, the middle and right buttons, and unclaimed keys still
reach Krita, because the overlay leaves them unaccepted.
"""

import math

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QTransform
from PyQt5.QtWidgets import QWidget

from .. import log
from ..core.interaction import PoseInteraction, Screen
from ..core.lineart import line_uniforms
from ..render.shaders import snapshot
from .canvas_bridge import CanvasWatcher, find_canvas_widget, image_to_widget
from .keys import shortcut
from .overlay_paint import paint_primitives

DRAFT_LIMIT = 1024  # Longest side while dragging.
FINAL_LIMIT = 2048  # Longest side once the pose settles.
SETTLE_MS = 150


class CanvasOverlay(QWidget):
    def __init__(self, canvas, view, session, controller):
        super().__init__(canvas)
        self.view, self.session, self.controller = view, session, controller
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.ClickFocus)
        self.image, self.image_scale = None, (1.0, 1.0)
        self.rendered_size = None
        self.transform = None
        self.interaction = None
        self.posing = False
        self.watcher = CanvasWatcher(canvas)
        self.watcher.changed.connect(self.follow_canvas)
        self.set_posing(False)
        self.follow_canvas()
        self.show()
        self.raise_()

    # Geometry --------------------------------------------------------------------

    def follow_canvas(self):
        """Track the canvas size and Krita's zoom, rotation, mirror, and pan."""
        try:
            parent = self.parentWidget()
            if parent is not None and self.geometry() != parent.rect():
                self.setGeometry(parent.rect())
            transform = image_to_widget(self.view)
            if transform != self.transform:
                self.transform = transform
                self.update()
            if self.rendered_size is not None and self.document_size() != self.rendered_size:
                self.controller.render(final=True)  # The document was resized.
        except Exception as error:
            self.controller.report(error)

    def document_size(self):
        document = self.view.document()
        return (document.width(), document.height()) if document is not None else None

    def _screen(self):
        size = self.document_size()
        if size is None or self.transform is None:
            return None
        zoom = math.hypot(self.transform.m11(), self.transform.m12())
        return Screen(size[0], size[1], 1.0 / zoom if zoom > 0 else 1.0)

    def _to_image(self, event):
        inverse, invertible = self.transform.inverted()
        point = inverse.map(QPointF(event.pos()))
        return point.x(), point.y()

    def _to_widget(self, point):
        mapped = self.transform.map(QPointF(*point))
        return mapped.x(), mapped.y()

    def set_image(self, image, scale):
        self.image, self.image_scale = image, scale
        self.update()

    def set_posing(self, posing):
        self.posing = posing
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not posing)
        if posing:
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()  # Krita's tool cursor shows through.
        if not posing and self.interaction is not None:
            self.interaction.cancel()
        self.update()

    # Painting ----------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            if self.transform is None:
                return
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.setRenderHint(QPainter.Antialiasing)
            if self.image is not None:
                painter.setTransform(QTransform.fromScale(*self.image_scale) * self.transform)
                painter.drawImage(0, 0, self.image)
                painter.resetTransform()
            screen = self._screen()
            if self.posing and self.interaction is not None and screen is not None:
                paint_primitives(painter, self.interaction.overlay(screen), self._to_widget)
        except Exception as error:
            self.controller.report(error)
        finally:
            painter.end()

    # Input -----------------------------------------------------------------------

    def _interaction(self):
        if self.interaction is None and self.session.editor is not None:
            self.interaction = PoseInteraction(self.session.editor, self.session.camera)
        return self.interaction

    def mousePressEvent(self, event):
        try:
            screen = self._screen()
            interaction = self._interaction()
            if (not self.posing or event.button() != Qt.LeftButton or screen is None
                    or interaction is None):
                event.ignore()  # Krita handles it: navigation, pop-up palette.
                return
            self.setFocus(Qt.MouseFocusReason)
            modifiers = event.modifiers()
            x, y = self._to_image(event)
            interaction.press(x, y, screen, bool(modifiers & Qt.ShiftModifier),
                              bool(modifiers & Qt.ControlModifier), navigate_on_empty=True)
            self.session.dragging = interaction.drag is not None
            self.session.notify()
            event.accept()
        except Exception as error:
            self.controller.report(error)

    def mouseMoveEvent(self, event):
        try:
            screen = self._screen()
            if self.interaction is None or self.interaction.drag is None or screen is None:
                event.ignore()
                return
            x, y = self._to_image(event)
            if self.interaction.move(x, y, screen):
                self.session.notify()
        except Exception as error:
            self.controller.report(error)

    def mouseReleaseEvent(self, event):
        try:
            if self.interaction is None or self.interaction.drag is None:
                event.ignore()
                return
            if event.buttons() == Qt.NoButton:
                self.interaction.release()
                self.session.dragging = False
                self.session.notify()
        except Exception as error:
            self.controller.report(error)

    def wheelEvent(self, event):
        event.ignore()  # Canvas zoom stays Krita's.

    def tabletEvent(self, event):
        # Handle the pen tip directly rather than relying on synthesized mouse
        # events; other pen buttons are left to Krita.
        try:
            kind = event.type()
            if not self.posing or event.button() not in (Qt.LeftButton, Qt.NoButton):
                event.ignore()
                return
            if kind == QEvent.TabletPress and event.button() == Qt.LeftButton:
                self.mousePressEvent(_PointerEvent(event, Qt.LeftButton))
            elif kind == QEvent.TabletMove and self.interaction is not None and self.interaction.drag:
                self.mouseMoveEvent(_PointerEvent(event, Qt.NoButton))
            elif kind == QEvent.TabletRelease and self.interaction is not None and self.interaction.drag:
                self.mouseReleaseEvent(_PointerEvent(event, Qt.LeftButton, released=True))
            else:
                event.ignore()
                return
            event.accept()
        except Exception as error:
            self.controller.report(error)

    def event(self, event):
        try:
            if (event.type() == QEvent.ShortcutOverride and self.posing
                    and shortcut(event) is not None):
                event.accept()
                return True
        except Exception as error:
            self.controller.report(error)
        return super().event(event)

    def keyPressEvent(self, event):
        try:
            action = shortcut(event) if self.posing else None
            if action is None:
                event.ignore()  # Space, tool keys, and so on reach Krita.
                return
            if action == "cancel" and self.interaction is not None:
                self.interaction.cancel()
            self.session.perform(action)
        except Exception as error:
            self.controller.report(error)

    def detach(self):
        self.watcher.detach()
        self.hide()
        self.deleteLater()


class _PointerEvent:
    """Adapts a tablet event to the mouse handlers above."""

    def __init__(self, event, button, released=False):
        self._event, self._button, self._released = event, button, released

    def button(self):
        return self._button

    def buttons(self):
        return Qt.NoButton if self._released else self._event.buttons()

    def modifiers(self):
        return self._event.modifiers()

    def pos(self):
        return self._event.pos()

    def accept(self):
        self._event.accept()

    def ignore(self):
        pass  # The tablet handler decides.


class CanvasController(QObject):
    """Keeps one overlay on the active view's canvas and feeds it renders."""

    status_changed = pyqtSignal(str)
    state_changed = pyqtSignal()

    def __init__(self, session, offscreen, parent=None):
        super().__init__(parent)
        self.session, self.offscreen = session, offscreen
        self.showing = self.posing = False
        self.overlay = None
        self.reason = "Show on Canvas is off."
        self.windows = {}  # id(QMainWindow) -> Window wrapper, kept so its signals stay connected
        self.settle = QTimer(self)
        self.settle.setSingleShot(True)
        self.settle.timeout.connect(lambda: self.render(final=True))
        session.changed.connect(self._session_changed)

    # Switches ----------------------------------------------------------------------

    def set_showing(self, showing):
        self.showing = showing
        if not showing:
            self.posing = False
            self._detach("Show on Canvas is off.")
        else:
            self.attach()
        self.state_changed.emit()

    def set_posing(self, posing):
        if posing and not self.showing:
            self.showing = True
            self.attach()
        self.posing = posing and self._alive()
        if self._alive():
            self.overlay.set_posing(self.posing)
            self.render(final=True)
        self.state_changed.emit()

    # Attachment --------------------------------------------------------------------

    def _alive(self):
        return self.overlay is not None and not sip.isdeleted(self.overlay)

    def attach(self):
        """(Re)attach to the active view; returns True when the overlay is live."""
        try:
            from krita import Krita
            window = Krita.instance().activeWindow()
            view = window.activeView() if window is not None else None
            if window is not None and id(window.qwindow()) not in self.windows:
                # Krita returns a fresh wrapper per call; keep this one so the
                # connection lives as long as the window.
                window.activeViewChanged.connect(self._view_changed)
                self.windows[id(window.qwindow())] = window
            if view is None or view.document() is None:
                self._detach("Open a document to show the figure on the canvas.")
                return False
            canvas, reason = find_canvas_widget(window)
            if canvas is None:
                self._detach(reason)
                return False
            if self._alive() and self.overlay.parentWidget() is canvas:
                self.overlay.view = view
                self.overlay.follow_canvas()
                return True
            self._detach(None)
            self.overlay = CanvasOverlay(canvas, view, self.session, self)
            self.overlay.set_posing(self.posing)
            self.reason = None
            log.event("canvas_attached", canvas=canvas.metaObject().className())
            self.render(final=True)
            return True
        except Exception as error:
            self._detach("Canvas attachment failed: {}: {}".format(type(error).__name__, error))
            return False
        finally:
            self.state_changed.emit()

    def _detach(self, reason):
        if self._alive():
            self.overlay.detach()
        self.overlay = None
        if reason is not None:
            self.reason = reason
            if self.showing:
                self.status_changed.emit(reason)

    def _view_changed(self, *unused):
        if self.showing:
            QTimer.singleShot(0, self.attach)  # Let Krita finish switching first.

    # Rendering ---------------------------------------------------------------------

    def _session_changed(self):
        if not self._alive():
            return
        if self.session.dragging:
            self.render(final=False)  # Fast draft; sharpen once the drag pauses or ends.
            self.settle.start(SETTLE_MS)
        else:
            self.settle.stop()
            self.render(final=True)

    def render(self, final=True):
        if not self._alive():
            return
        overlay, session = self.overlay, self.session
        try:
            size = overlay.document_size()
            if session.editor is None or session.mesh is None or size is None:
                overlay.set_image(None, (1.0, 1.0))
                return
            width, height = size
            limit = FINAL_LIMIT if final else DRAFT_LIMIT
            factor = min(1.0, limit / max(width, height))
            w, h = max(1, round(width * factor)), max(1, round(height * factor))
            editor = session.editor
            shot = snapshot(editor.skeleton, editor.pose, session.camera, width / height,
                            editor.selected if self.posing else None, grid=self.posing,
                            opacity=session.opacity)
            # Widths are document pixels; this preview may be smaller than the document.
            image = self.offscreen.render(session.mesh_key, session.mesh, shot, w, h,
                                          session.mode, line_uniforms(session.lines, w / width))
            overlay.rendered_size = size
            overlay.set_image(image, (width / w, height / h))
        except Exception as error:
            self.report(error)

    def report(self, error):
        self.status_changed.emit("Canvas: {}: {}".format(type(error).__name__, error))

    def describe(self):
        """Diagnostics: attachment state, canvas class, size, and transform."""
        if not self._alive():
            return {"status": "not attached", "reason": self.reason or "unknown",
                    "show": self.showing, "pose": self.posing}
        overlay = self.overlay
        canvas = overlay.parentWidget()
        transform = overlay.transform
        details = {"status": "attached", "show": self.showing, "pose": self.posing,
                   "canvas_class": canvas.metaObject().className(),
                   "widget_size": "{}x{}".format(canvas.width(), canvas.height()),
                   "device_pixel_ratio": canvas.devicePixelRatioF()}
        if transform is not None:
            details["zoom"] = round(math.hypot(transform.m11(), transform.m12()), 4)
            details["rotation_degrees"] = round(math.degrees(math.atan2(transform.m12(), transform.m11())), 2)
            details["mirrored"] = transform.determinant() < 0
        return details
