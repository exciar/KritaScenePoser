"""The pose state shared by the docker viewport and the canvas overlay."""

from PyQt5.QtCore import QObject, pyqtSignal

from ..core.camera import OrbitCamera
from ..core.editor import DRAG, RINGS, PoseEditor
from ..core.lineart import LineArtSettings
from ..core.output import OutputSettings
from ..core.picking import FigurePicker
from ..core.shape import BodyShape
from ..storage import import_pose, scene_io
from ..storage.shaping import figure_key, shaped_figure

MODES = ("shaded", "lines", "both")


class PoseSession(QObject):
    """One editor and one camera; every view listens to ``changed``."""

    changed = pyqtSignal()
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = None
        self.camera = OrbitCamera()
        self.figure_id = self.rig = self.mesh = None
        self.base_rig = self.base_mesh = None  # The figure as it ships, unshaped.
        self.shape = BodyShape()
        self.mesh_key = None  # Identifies the shaped mesh for the GPU cache.
        self.dragging = False  # A view is mid-drag: renders may be drafts.
        self.mode = "shaded"  # Display: shaded, lines, or both; shared by all views.
        self.lines = LineArtSettings()
        self.opacity = 1.0  # The figure in the viewport and on the canvas.
        self.layer_opacity = 1.0  # Applied to the layer KSP creates.
        self.output = OutputSettings()
        self.limits = True  # Joint limits; off lets a pose go anywhere.
        self.last_layer = None  # (document root id, node id) of the layer KSP made.

    def set_figure(self, figure_id, rig, mesh):
        """Take a figure as it ships; the session applies the body shape."""
        self.figure_id, self.base_rig, self.base_mesh = figure_id, rig, mesh
        self._build_figure()

    def set_shape(self, shape):
        """Reshape the figure. The pose, the camera, and the history are kept."""
        shape = shape.validated()
        if shape == self.shape:
            return False
        self.shape = shape
        if self.base_rig is not None:
            self._build_figure()
        else:
            self.changed.emit()
        return True

    def _build_figure(self):
        """Shape the figure, then hand the result to the editor."""
        first = self.editor is None
        rig, mesh = shaped_figure(self.base_rig, self.base_mesh, self.shape)
        picker = FigurePicker(rig, mesh)
        if first:
            self.editor = PoseEditor(rig.skeleton, picker)
        else:
            self.editor.set_figure(rig.skeleton, picker)
        rig.skeleton.limits_enabled = self.limits
        self.rig, self.mesh = rig, mesh
        self.mesh_key = figure_key(self.figure_id, self.shape)
        if first:
            self.frame()
        self.changed.emit()

    def frame(self):
        if self.editor is not None:
            self.camera.frame([p for segment in self.editor.segments() for p in segment])

    def notify(self):
        self.changed.emit()

    def perform(self, action):
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

    def set_display(self, mode):
        if mode in MODES and mode != self.mode:
            self.mode = mode
            self.changed.emit()

    def set_lines(self, settings):
        settings = settings.validated()
        if settings != self.lines:
            self.lines = settings
            self.changed.emit()

    def set_mode(self, mode):
        if self.editor is not None and self.editor.mode != mode:
            self.perform("mode")

    # Opacity, output, and limits ------------------------------------------------

    def set_opacity(self, value):
        value = _clamped(value)
        if value != self.opacity:
            self.opacity = value
            self.changed.emit()

    def set_layer_opacity(self, value):
        value = _clamped(value)
        if value != self.layer_opacity:
            self.layer_opacity = value
            self.changed.emit()

    def set_output(self, settings):
        settings = settings.validated()
        if settings != self.output:
            self.output = settings
            self.changed.emit()

    def set_limits(self, enabled):
        self.limits = bool(enabled)
        if self.editor is not None:
            self.editor.skeleton.limits_enabled = self.limits
        self.changed.emit()

    def remember_layer(self, document_id, node_id, name):
        self.last_layer = (document_id, node_id, name)

    def layer_target(self, document_id):
        if self.last_layer and self.last_layer[0] == document_id:
            return self.last_layer[1]
        return None

    # Pose and scene files -------------------------------------------------------

    def pose_text(self):
        editor = self._require_figure()
        return scene_io.write_pose(editor.skeleton, editor.pose, self.figure_id or "")

    def scene_text(self):
        editor = self._require_figure()
        return scene_io.write_scene(
            editor.skeleton, editor.pose, figure=self.figure_id or "", camera=self.camera,
            mode=self.mode, lines=self.lines, opacity=self.opacity,
            layer_opacity=self.layer_opacity, output=self.output, shape=self.shape)

    def load_pose_text(self, text):
        """Apply a stored pose as one undo step; returns what it did."""
        editor = self._require_figure()
        applied = scene_io.read_pose(text, editor.skeleton)
        editor.replace_pose(applied.pose)
        self.changed.emit()
        return applied

    def load_pose_file(self, data, name, custom_map=None):
        """Take the pose from a posed model file; returns what it did."""
        editor = self._require_figure()
        result = import_pose.read_pose(data, editor.skeleton, name, custom_map=custom_map)
        editor.replace_pose(result.pose)
        self.changed.emit()
        return result

    def load_scene_text(self, text):
        """Apply a stored scene, including the camera and every setting."""
        editor = self._require_figure()
        scene = scene_io.read_scene(text, editor.skeleton)
        if scene.shape != self.shape and self.base_rig is not None:
            # Shape first: it rebuilds the figure, and the pose goes on the new body.
            self.shape = scene.shape.validated()
            self._build_figure()
            editor = self.editor
            scene = scene_io.read_scene(text, editor.skeleton)
        editor.replace_pose(scene.pose)
        self.camera = scene.camera
        self.mode = scene.mode if scene.mode in MODES else "shaded"
        self.lines = scene.lines
        self.opacity, self.layer_opacity = scene.opacity, scene.layer_opacity
        self.output = scene.output
        self.changed.emit()
        return scene

    def _require_figure(self):
        if self.editor is None:
            raise ValueError("Load a figure before saving or loading a pose.")
        return self.editor


def _clamped(value):
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 1.0
