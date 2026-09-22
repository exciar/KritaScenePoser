"""The only code that depends on Krita's undocumented widget tree.

Every function returns a reason instead of raising, and canvas posing turns itself
off when the lookup fails.
"""

from PyQt5.QtCore import QEvent, QObject, pyqtSignal
from PyQt5.QtWidgets import QMdiArea, QWidget

CANVAS_CLASSES = ("KisOpenGLCanvas2", "KisQPainterCanvas")
WATCHED_EVENTS = (QEvent.Resize, QEvent.Paint, QEvent.UpdateRequest, QEvent.Show)


def find_canvas_widget(window):
    try:
        main = window.qwindow() if window is not None else None
        if main is None:
            return None, "No Krita window is active."
        area = main.findChild(QMdiArea)
        if area is None:
            return None, "Krita's document area was not found."
        sub = area.currentSubWindow() or area.activeSubWindow()
        if sub is None or sub.widget() is None:
            return None, "No document view is open."
        for widget in sub.widget().findChildren(QWidget):
            if widget.metaObject().className() in CANVAS_CLASSES:
                return widget, None
        return None, "Krita's canvas widget was not found; this Krita version may be unsupported."
    except Exception as error:
        return None, "Canvas lookup failed: {}: {}".format(type(error).__name__, error)


def image_to_widget(view):
    """QTransform from image pixels to canvas-widget pixels, or None.

    Built only from documented View transforms; it includes Krita's zoom,
    rotation, mirror, and pan.
    """
    try:
        image_to_flake, invertible = view.flakeToImageTransform().inverted()
        return image_to_flake * view.flakeToCanvasTransform() if invertible else None
    except Exception:
        return None


class CanvasWatcher(QObject):
    """Passive observer of canvas repaints and resizes; never consumes an event."""

    changed = pyqtSignal()

    def __init__(self, widget):
        super().__init__(widget)
        self._widget = widget
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        try:
            if event.type() in WATCHED_EVENTS:
                self.changed.emit()
        except Exception:
            pass
        return False  # Always let Krita handle the event.

    def detach(self):
        try:
            self._widget.removeEventFilter(self)
        except RuntimeError:
            pass  # The canvas was already destroyed.
