"""The pose state shared by the docker viewport and the canvas overlay."""

from PyQt5.QtCore import QObject, pyqtSignal

from ..core.camera import OrbitCamera
from ..core.editor import DRAG, RINGS, PoseEditor


class PoseSession(QObject):
    """One editor and one camera; every view listens to ``changed``."""

    changed = pyqtSignal()
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = None
        self.camera = OrbitCamera()
        self.figure_id = self.rig = self.mesh = None
        self.dragging = False  # A view is mid-drag: renders may be drafts.

    def set_figure(self, figure_id, rig, mesh, picker):
        first = self.editor is None
        if first:
            self.editor = PoseEditor(rig.skeleton, picker)
        else:
            self.editor.set_figure(rig.skeleton, picker)
        self.figure_id, self.rig, self.mesh = figure_id, rig, mesh
        if first:
            self.frame()
        self.changed.emit()

    def frame(self):
        if self.editor is not None:
            self.camera.frame([p for segment in self.editor.segments() for p in segment])

    def notify(self):
        self.changed.emit()

    def perform(self, action):
        """Run a named command from a key, a button, or a menu action."""
        editor = self.editor
        if action == "frame":
            self.frame()
        elif action == "ortho":
            self.camera.orthographic = not self.camera.orthographic
        elif editor is None:
            return
        elif action == "cancel":
            editor.cancel_drag()
            self.dragging = False
        elif action == "mode":
            editor.cancel_drag()
            editor.mode = RINGS if editor.mode == DRAG else DRAG
        elif action == "mirror_limb":
            if not editor.mirror_limb():
                self.status.emit("Select an arm, leg, hand, foot, or finger to copy its pose "
                                 "to the other side.")
        elif action in ("undo", "redo", "reset_joint", "reset_pose", "mirror_pose"):
            getattr(editor, action)()
        self.changed.emit()

    def set_mode(self, mode):
        if self.editor is not None and self.editor.mode != mode:
            self.perform("mode")
