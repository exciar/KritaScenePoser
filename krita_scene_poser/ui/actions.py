"""Menu actions under Tools > Scripts. None has a default shortcut, so none clashes
with Krita's own.
"""

import os

from krita import Extension, Krita
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QMessageBox

from .. import __version__, log
from ..integration import settings as ksp_settings
from .docker import TITLE, KSPDocker

MENU = "tools/scripts"
ICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "assets", "icons", "ksp.svg")


def active_window():
    """Krita makes a fresh Window wrapper per call, and a kept one dies with its
    window: touching it then raises "wrapped C/C++ object has been deleted"."""
    return Krita.instance().activeWindow()


def find_docker(window):
    for dock in window.dockers():
        if isinstance(dock, KSPDocker):
            return dock
    return None


class KSPExtension(Extension):
    """Every slot catches exceptions: PyQt5 may abort Krita on an escaped one."""

    def __init__(self, parent):
        super().__init__(parent)
        self.log_actions = []
        self.canvas_actions = []
        # Load before createActions, which needs the stored checkbox state.
        self.settings = ksp_settings.load()
        self._apply_logging()

    def setup(self):
        log.event("plugin_ready", version=__version__)

    def createActions(self, window):
        icon = QIcon(ICON)
        show = window.createAction(
            "krita_scene_poser_show", "KSP: Show Scene Poser", MENU)
        show.setIcon(icon)
        show.triggered.connect(lambda checked=False: self._show())
        # The id predates line art; keep it so assigned shortcuts survive.
        create = window.createAction(
            "krita_scene_poser_create_layer", "KSP: Create Guide Layer", MENU)
        create.setIcon(icon)
        create.triggered.connect(lambda checked=False: self._create_layer("guide"))
        lineart = window.createAction(
            "krita_scene_poser_create_lineart_layer", "KSP: Create Lineart Layer", MENU)
        lineart.setIcon(icon)
        lineart.setToolTip("Render the posed figure's line art into a new layer.")
        lineart.triggered.connect(lambda checked=False: self._create_layer("lineart"))
        save_pose = window.createAction(
            "krita_scene_poser_save_pose", "KSP: Save Pose…", MENU)
        save_pose.setIcon(icon)
        save_pose.triggered.connect(lambda checked=False: self._pose_file("save"))
        load_pose = window.createAction(
            "krita_scene_poser_load_pose", "KSP: Load Pose…", MENU)
        load_pose.setIcon(icon)
        load_pose.triggered.connect(lambda checked=False: self._pose_file("load"))
        canvas = window.createAction(
            "krita_scene_poser_pose_on_canvas", "KSP: Pose on Canvas", MENU)
        canvas.setIcon(icon)
        canvas.setCheckable(True)
        canvas.setToolTip("Pose the figure directly on the canvas. Brushes are paused while on.")
        canvas.triggered.connect(lambda checked=False: self._pose_on_canvas(checked))
        self.canvas_actions.append(canvas)
        diagnostic = window.createAction(
            "krita_scene_poser_diagnostic_log", "KSP: Diagnostic Log", MENU)
        diagnostic.setCheckable(True)
        diagnostic.setChecked(self.settings.diagnostic_log)
        diagnostic.setToolTip(
            "Record KSP events in Krita's application-data folder. Nothing is sent.")
        diagnostic.toggled.connect(self._set_diagnostic_log)
        self.log_actions.append(diagnostic)

    def _docker(self):
        window = active_window()
        dock = find_docker(window) if window is not None else None
        if dock is None:
            raise RuntimeError(
                "The KSP docker is not loaded. Check that KSP is enabled in "
                "Python Plugin Manager, then restart Krita.")
        dock.show()
        dock.raise_()
        return dock

    def _show(self):
        try:
            self._docker()
        except Exception as error:
            self._warn(error)

    def _create_layer(self, kind):
        try:
            dock = self._docker()
            if kind == "lineart":
                dock.create_lineart_layer()
            else:
                dock.create_layer()
        except Exception as error:
            self._warn(error)

    def _pose_file(self, action):
        try:
            dock = self._docker()
            if action == "save":
                dock.save_pose()
            else:
                dock.load_pose()
        except Exception as error:
            self._warn(error)

    def _pose_on_canvas(self, checked):
        try:
            dock = self._docker()
            if not getattr(dock, "_ksp_canvas_synced", False):
                dock.canvas.state_changed.connect(lambda: self._sync_canvas_actions(dock))
                dock._ksp_canvas_synced = True
            dock.set_pose_on_canvas(bool(checked))
            self._sync_canvas_actions(dock)
        except Exception as error:
            self._warn(error)

    def _sync_canvas_actions(self, dock):
        for action in list(self.canvas_actions):
            try:
                if action.isChecked() != dock.canvas.posing:
                    action.blockSignals(True)
                    action.setChecked(dock.canvas.posing)
                    action.blockSignals(False)
            except RuntimeError:  # The action's window was closed.
                self.canvas_actions.remove(action)

    def _set_diagnostic_log(self, enabled):
        try:
            self.settings = ksp_settings.update(self.settings, diagnostic_log=bool(enabled))
            self._apply_logging()
            for action in list(self.log_actions):
                try:
                    if action.isChecked() != self.settings.diagnostic_log:
                        action.blockSignals(True)
                        action.setChecked(self.settings.diagnostic_log)
                        action.blockSignals(False)
                except RuntimeError:  # The action's window was closed.
                    self.log_actions.remove(action)
        except Exception as error:
            self._warn(error)

    def _apply_logging(self):
        try:
            directory = ksp_settings.log_directory() if self.settings.diagnostic_log else None
            if log.configure(directory):
                log.event("log_enabled", version=__version__)
        except Exception:
            log.configure(None)  # A log that cannot be written stays off.

    @staticmethod
    def _warn(error):
        parent = None
        try:
            window = active_window()
            parent = window.qwindow() if window is not None else None
        except Exception:
            pass
        QMessageBox.warning(parent, TITLE, str(error))
