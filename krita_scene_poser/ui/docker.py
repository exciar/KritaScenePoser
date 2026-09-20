"""KSP docker: pose viewport, view and line-art settings, canvas posing, and export."""

from dataclasses import replace
import time

from krita import DockWidget, Krita
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import diagnostics, log
from ..core.editor import DRAG, RINGS, display_name
from ..core.lineart import LIMITS, LineArtSettings, line_uniforms
from ..core.output import QUALITIES, OutputSettings, OutputSizeError
from ..core.picking import FigurePicker
from ..integration import settings as ksp_settings
from ..integration.krita_document import (
    GUIDE_LAYER_NAME, LINEART_LAYER_NAME, DocumentExportError, export_layer, layer_id,
    read_document, snapshot_document, update_layer,
)
from ..render.gl_renderer import CapabilityError
from ..render.offscreen import OffscreenRenderer
from ..render.pixel_transfer import PixelTransferError, qimage_to_bgra
from ..render.shaders import snapshot
from ..storage.figures import DEFAULT_FIGURE, available_figures, load_figure
from ..storage.mesh_io import MeshFormatError
from ..storage.presets import available_presets
from ..storage.rig_io import RigFormatError
from ..storage.scene_io import SceneFormatError
from . import scene_files
from .canvas_overlay import CanvasController
from .session import PoseSession
from .viewport import FigureViewport

DOCKER_ID = "krita_scene_poser"
TITLE = "KSP — Krita Scene Poser"
EXPECTED_ERRORS = (CapabilityError, DocumentExportError, PixelTransferError,
                   MeshFormatError, RigFormatError, SceneFormatError, OutputSizeError)
WORKSPACE_DELAY = 1500  # Milliseconds of quiet before the workspace is stored.
NO_DOCUMENT = "Open or create a document to create a KSP layer."
GUIDE_TOOLTIP = ("Render the posed, shaded figure from this view into a transparent paint "
                 "layer, at the size set below.")
LINEART_TOOLTIP = ("Render the posed figure's line art, using the Line Art settings, into a "
                   "transparent paint layer at the size set below.")
IDLE_HINT = ("Click a body part to select it. Right-drag or Alt+drag orbits, middle-drag "
             "pans, the wheel zooms, F frames.")
CANVAS_HINT = ("Posing on the canvas: drag the figure to pose it; drag empty space to orbit "
               "(Shift pans, Ctrl dollies). Brushes are paused until you turn this off.")
EDIT_BUTTONS = (
    ("undo", "Undo", "Undo the last pose change (Ctrl+Z)."),
    ("redo", "Redo", "Redo the pose change (Ctrl+Shift+Z)."),
    ("reset_joint", "Reset Joint", "Return the selected joint to its rest pose (R)."),
    ("reset_pose", "Reset Pose", "Return the whole figure to its rest pose."),
    ("mirror_pose", "Mirror Pose", "Mirror the whole pose left to right."),
    ("mirror_limb", "Mirror Limb", "Copy the selected limb's pose to the other side."),
)
VIEWS = (("shaded", "Shaded", "Show the shaded figure."),
         ("lines", "Lines", "Show only the line art, as Create Lineart Layer will draw it."),
         ("both", "Both", "Show line art over the shaded figure."))
SIZE_LABELS = (("document", "Document size"), ("custom", "Custom size"))
ANCHOR_LABELS = (("center", "Centered"), ("topleft", "Top left"))
LINE_TOGGLES = (("outline", "Outline", "The figure's silhouette."),
                ("contours", "Contours", "Where one part overlaps another, e.g. an arm across the body."),
                ("creases", "Creases", "Sharp folds of the surface."),
                ("seams", "Seams", "Joints between the mannequin's parts."))


def describe(error):
    """KSP errors are written for users; anything else keeps its type name."""
    if isinstance(error, EXPECTED_ERRORS):
        return str(error)
    return "{}: {}".format(type(error).__name__, error)


class KSPDocker(DockWidget):
    """All slots catch exceptions: PyQt5 may abort Krita on an escaped one."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.last_probe = self.last_error = self.last_document = None
        self.figures = {}  # figure id -> (rig, mesh, picker), loaded on demand
        self.loaded = False
        self.updating = False  # Guards settings widgets while syncing them.
        self.shown_lines = None  # Settings the Line Art widgets last showed.

        self.session = PoseSession(self)
        self.settings = self._stored_settings()
        self.session.lines = LineArtSettings.from_json(self.settings.lineart)
        self.session.output = OutputSettings.from_json(self.settings.output)
        self.session.limits = bool(self.settings.joint_limits)
        self.workspace_timer = QTimer(self)
        self.workspace_timer.setSingleShot(True)
        self.workspace_timer.setInterval(WORKSPACE_DELAY)
        self.workspace_timer.timeout.connect(self._store_workspace)
        self.session.changed.connect(self._refresh_controls)
        self.session.status.connect(self._show_status)
        self.offscreen = OffscreenRenderer()
        self.canvas = CanvasController(self.session, self.offscreen, self)
        self.canvas.status_changed.connect(self._show_status)
        self.canvas.state_changed.connect(self._refresh_controls)
        self.viewport = FigureViewport(self.session)
        self.viewport.status_changed.connect(self._show_status)

        layout = QVBoxLayout()
        layout.addLayout(self._build_top_rows())
        layout.addWidget(self.viewport, 1)
        self.hint = QLabel(IDLE_HINT)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.hint)
        tabs = QTabWidget()
        tabs.addTab(self._build_pose_tab(), "Pose")
        tabs.addTab(self._build_lines_tab(), "Line Art")
        tabs.addTab(self._build_output_tab(), "Output")
        tabs.addTab(self._build_scene_tab(), "Scene")
        layout.addWidget(tabs)
        self.status = QLabel("The figure loads when this docker is first shown.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        root = QWidget(self)
        root.setLayout(layout)
        self.setWidget(root)
        self.visibilityChanged.connect(self._on_visibility)
        self.session.changed.connect(self.workspace_timer.start)
        self._refresh_document_state()
        self._refresh_controls()

    # Layout --------------------------------------------------------------------------

    @staticmethod
    def _tool(label, tip, checkable=False):
        button = QToolButton()
        button.setText(label)
        button.setToolTip(tip)
        button.setCheckable(checkable)
        return button

    def _build_top_rows(self):
        self.figure_box = QComboBox()
        self.figure_box.setToolTip("Figure to pose. The pose carries over when you switch.")
        self.figure_box.currentIndexChanged.connect(self._choose_figure)
        self.drag_button = self._tool("Drag", "Drag body parts to pose them (T switches modes).", True)
        self.rings_button = self._tool("Rings", "Rotate the selected joint with axis rings (T).", True)
        modes = QButtonGroup(self)
        for button in (self.drag_button, self.rings_button):
            modes.addButton(button)
        self.drag_button.clicked.connect(lambda checked=False: self._set_mode(DRAG))
        self.rings_button.clicked.connect(lambda checked=False: self._set_mode(RINGS))
        frame = self._tool("Frame", "Fit the figure in view (F).")
        frame.clicked.connect(lambda checked=False: self._perform("frame"))
        self.ortho_button = self._tool("Ortho", "Orthographic projection (O).", True)
        self.ortho_button.clicked.connect(lambda checked=False: self._perform("ortho"))
        top = QHBoxLayout()
        top.addWidget(self.figure_box, 1)
        for button in (self.drag_button, self.rings_button, frame, self.ortho_button):
            top.addWidget(button)

        views = QHBoxLayout()
        views.addWidget(QLabel("View:"))
        self.view_buttons = {}
        group = QButtonGroup(self)
        for mode, label, tip in VIEWS:
            button = self._tool(label, tip, True)
            button.clicked.connect(lambda checked=False, m=mode: self._set_display(m))
            group.addButton(button)
            views.addWidget(button)
            self.view_buttons[mode] = button
        views.addStretch(1)
        rows = QVBoxLayout()
        rows.addLayout(top)
        rows.addLayout(views)
        return rows

    def _build_pose_tab(self):
        self.show_canvas = QPushButton("Show on Canvas")
        self.show_canvas.setCheckable(True)
        self.show_canvas.setToolTip("Draw the figure on the canvas, exactly where Create Layer "
                                    "would put it. Krita's tools keep working.")
        self.show_canvas.clicked.connect(self._toggle_show)
        self.pose_canvas = QPushButton("Pose on Canvas")
        self.pose_canvas.setCheckable(True)
        self.pose_canvas.setToolTip("Pose the figure directly on the canvas, like an object "
                                    "tool. Brushes are paused while this is on.")
        self.pose_canvas.clicked.connect(self._toggle_pose)
        grid = QGridLayout()
        grid.addWidget(self.show_canvas, 0, 0, 1, 3)
        grid.addWidget(self.pose_canvas, 0, 3, 1, 3)
        self.buttons = {}
        for index, (action, label, tip) in enumerate(EDIT_BUTTONS):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, name=action: self._perform(name))
            grid.addWidget(button, 1 + index // 3, (index % 3) * 2, 1, 2)
            self.buttons[action] = button
        self.limits_box = QCheckBox("Joint limits")
        self.limits_box.setToolTip("Stop joints bending further than a body could. "
                                   "Turn off for exaggerated or stylized poses.")
        self.limits_box.toggled.connect(self._toggle_limits)
        grid.addWidget(self.limits_box, 3, 0, 1, 3)
        self.opacity_slider = self._slider(
            "How solid the figure looks in the viewport and on the canvas.",
            self._opacity_edited)
        grid.addWidget(QLabel("Figure opacity"), 4, 0, 1, 2)
        grid.addWidget(self.opacity_slider, 4, 2, 1, 4)
        page = QWidget()
        page.setLayout(grid)
        return page

    def _slider(self, tip, slot):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(100)
        slider.setToolTip(tip)
        slider.valueChanged.connect(slot)
        return slider

    def _build_lines_tab(self):
        form = QFormLayout()
        toggles = QGridLayout()
        self.line_toggles = {}
        for index, (name, label, tip) in enumerate(LINE_TOGGLES):
            box = QCheckBox(label)
            box.setToolTip(tip)
            box.toggled.connect(self._lines_edited)
            toggles.addWidget(box, index // 2, index % 2)
            self.line_toggles[name] = box
        form.addRow(toggles)

        def width_box(name, tip):
            box = QDoubleSpinBox()
            box.setRange(*LIMITS[name])
            box.setSingleStep(0.5)
            box.setDecimals(1)
            box.setSuffix(" px")
            box.setToolTip(tip)
            box.valueChanged.connect(self._lines_edited)
            return box
        self.outline_width = width_box("outline_width", "Silhouette line width, in document pixels.")
        self.inner_width = width_box("inner_width", "Contour, crease, and seam width, in document pixels.")
        form.addRow("Outline width", self.outline_width)
        form.addRow("Inner width", self.inner_width)
        self.crease_angle = QSpinBox()
        self.crease_angle.setRange(*(int(v) for v in LIMITS["crease_angle"]))
        self.crease_angle.setSuffix("°")
        self.crease_angle.setToolTip("Fold angle that counts as a crease; lower finds more.")
        self.crease_angle.valueChanged.connect(self._lines_edited)
        form.addRow("Crease angle", self.crease_angle)
        self.depth_slider = QSlider(Qt.Horizontal)
        self.depth_slider.setRange(0, 100)
        self.depth_slider.setToolTip("How small an overlap depth still gets a contour line.")
        self.depth_slider.valueChanged.connect(self._lines_edited)
        form.addRow("Contour sensitivity", self.depth_slider)
        self.line_opacity = self._slider("How solid the lines are, in the preview and "
                                         "in the layer.", self._lines_edited)
        form.addRow("Line opacity", self.line_opacity)
        self.color_button = QPushButton()
        self.color_button.setToolTip("Line color.")
        self.color_button.clicked.connect(self._choose_line_color)
        form.addRow("Color", self.color_button)
        page = QWidget()
        page.setLayout(form)
        return page

    def _build_output_tab(self):
        form = QFormLayout()
        self.size_box = QComboBox()
        for mode, label in SIZE_LABELS:
            self.size_box.addItem(label, mode)
        self.size_box.setToolTip("Document size matches the canvas. A custom size renders the "
                                 "figure at any size and places it in the document.")
        self.size_box.currentIndexChanged.connect(self._output_edited)
        form.addRow("Size", self.size_box)

        def size_spin(tip):
            box = QSpinBox()
            box.setRange(1, 4096)
            box.setSuffix(" px")
            box.setToolTip(tip)
            box.valueChanged.connect(self._output_edited)
            return box
        self.width_spin = size_spin("Layer width in document pixels.")
        self.height_spin = size_spin("Layer height in document pixels.")
        sizes = QHBoxLayout()
        sizes.addWidget(self.width_spin)
        sizes.addWidget(QLabel("\u00d7"))
        sizes.addWidget(self.height_spin)
        form.addRow("Custom", sizes)
        self.anchor_box = QComboBox()
        for anchor, label in ANCHOR_LABELS:
            self.anchor_box.addItem(label, anchor)
        self.anchor_box.setToolTip("Where a custom size sits in the document. Pixels outside "
                                   "the canvas stay in the layer.")
        self.anchor_box.currentIndexChanged.connect(self._output_edited)
        form.addRow("Place", self.anchor_box)
        self.quality_box = QComboBox()
        for quality in QUALITIES:
            self.quality_box.addItem("{}\u00d7".format(quality), quality)
        self.quality_box.setToolTip("Render larger and scale down, for smoother edges and "
                                    "hairlines. Uses more memory.")
        self.quality_box.currentIndexChanged.connect(self._output_edited)
        form.addRow("Quality", self.quality_box)
        self.layer_opacity = self._slider("Opacity of the layer KSP creates. You can change it "
                                          "afterwards in Krita's Layers docker.",
                                          self._layer_opacity_edited)
        form.addRow("Layer opacity", self.layer_opacity)
        self.update_box = QCheckBox("Update the layer I made last")
        self.update_box.setToolTip("Rewrite the KSP layer from the last render in this document "
                                   "instead of adding another one. If it is gone or renamed, a "
                                   "new layer is created.")
        self.update_box.toggled.connect(lambda checked=False: self._refresh_document_state())
        form.addRow(self.update_box)

        self.guide_button = QPushButton("Create Guide Layer")
        self.guide_button.clicked.connect(self.create_layer)
        self.lineart_button = QPushButton("Create Lineart Layer")
        self.lineart_button.clicked.connect(self.create_lineart_layer)
        self_test = QPushButton("Self-Test")
        self_test.setToolTip("Check GPU readback: channel order, orientation, and transparency. "
                             "Nothing is added to the document.")
        self_test.clicked.connect(self._self_test)
        copy = QPushButton("Copy Diagnostics")
        copy.setToolTip("Copy version, GPU, canvas, and test details to the clipboard. "
                        "Nothing is sent.")
        copy.clicked.connect(self._copy_diagnostics)
        grid = QGridLayout()
        grid.addWidget(self.guide_button, 0, 0)
        grid.addWidget(self.lineart_button, 0, 1)
        grid.addWidget(self_test, 1, 0)
        grid.addWidget(copy, 1, 1)
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(grid)
        page = QWidget()
        page.setLayout(layout)
        return page

    def _build_scene_tab(self):
        self.preset_box = QComboBox()
        self.preset_box.setToolTip("Bundled poses. Applying one is a single undo step.")
        apply_preset = QPushButton("Apply Pose")
        apply_preset.setToolTip("Put the selected pose on the current figure.")
        apply_preset.clicked.connect(self._apply_preset)
        buttons = (
            ("Save Pose\u2026", "Save the figure's pose to a file you can reuse on either "
             "figure.", lambda: scene_files.save_pose(self, self.session)),
            ("Load Pose\u2026", "Load a pose file onto the current figure.",
             lambda: scene_files.load_pose(self, self.session)),
            ("Save Scene\u2026", "Save the pose, the camera, and every KSP setting.",
             lambda: scene_files.save_scene(self, self.session)),
            ("Load Scene\u2026", "Restore a saved pose, camera, and settings.",
             lambda: scene_files.load_scene(self, self.session)),
        )
        grid = QGridLayout()
        grid.addWidget(self.preset_box, 0, 0)
        grid.addWidget(apply_preset, 0, 1)
        for index, (label, tip, action) in enumerate(buttons):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, run=action: self._run_file_action(run))
            grid.addWidget(button, 1 + index // 2, index % 2)
        page = QWidget()
        page.setLayout(grid)
        return page

    # Figures -----------------------------------------------------------------

    def ensure_loaded(self):
        if not self.loaded:
            self.loaded = True
            self._populate_figures()
            self._populate_presets()
            self._restore_workspace()

    def _on_visibility(self, visible=True):
        try:
            self._refresh_document_state()
            if visible:
                self.ensure_loaded()
        except Exception as error:
            self._show_status("Could not start KSP. " + describe(error))

    def _populate_presets(self):
        presets, problems = available_presets()
        self.preset_box.clear()
        for preset, label in presets:
            self.preset_box.addItem(label, preset)
        if problems:
            self._show_status("Some poses could not be read: " + "; ".join(problems))

    def _populate_figures(self):
        figures, problems = available_figures()
        self.figure_box.blockSignals(True)
        self.figure_box.clear()
        for figure, name in figures:
            self.figure_box.addItem(name, figure)
        default = self.figure_box.findData(DEFAULT_FIGURE)
        self.figure_box.setCurrentIndex(max(default, 0))
        self.figure_box.blockSignals(False)
        if problems:
            self._show_status("Some figures could not be read: " + "; ".join(problems))
        if figures:
            self._choose_figure(self.figure_box.currentIndex())
        else:
            self._show_status("No figures were found. Reinstall KSP to restore them.")

    def _choose_figure(self, index):
        figure = self.figure_box.itemData(index)
        if figure is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if figure not in self.figures:
                rig, mesh = load_figure(figure)
                self.figures[figure] = (rig, mesh, FigurePicker(rig, mesh))
            rig, mesh, picker = self.figures[figure]
            self.session.set_figure(figure, rig, mesh, picker)
            self._show_status("{} ready.".format(rig.display_name))
            log.event("figure_loaded", figure=figure)
        except Exception as error:
            self._show_status("Could not load the figure. " + describe(error))
        finally:
            QApplication.restoreOverrideCursor()

    # Canvas posing -------------------------------------------------------------

    def _toggle_show(self, checked=False):
        try:
            self.ensure_loaded()
            self.canvas.set_showing(bool(checked))
        except Exception as error:
            self._show_status(describe(error))

    def _toggle_pose(self, checked=False):
        self.set_pose_on_canvas(bool(checked))

    def set_pose_on_canvas(self, posing):
        """Also used by the KSP: Pose on Canvas menu action."""
        try:
            self.ensure_loaded()
            self.canvas.set_posing(posing)
            if posing and not self.canvas.posing:
                self._show_status("Pose on Canvas is unavailable: " + (self.canvas.reason or "unknown"))
        except Exception as error:
            self._show_status(describe(error))

    # Line art settings -----------------------------------------------------------

    @staticmethod
    def _stored_settings():
        try:
            return ksp_settings.load()
        except Exception:
            return ksp_settings.Settings()  # A damaged store must not stop KSP starting.

    def _store(self, **changes):
        """Persist a few settings; other owners' values are left alone."""
        try:
            self.settings = ksp_settings.update(self.settings, **changes)
        except Exception as error:
            self._show_status("Settings could not be saved. " + describe(error))

    def _store_workspace(self):
        """Keep the current pose and settings for the next Krita session."""
        if self.session.editor is None:
            return
        try:
            self._store(workspace=self.session.scene_text())
        except Exception:
            pass  # A workspace that cannot be written is never worth a message.

    def _restore_workspace(self):
        text = (self.settings.workspace or "").strip()
        if not text or self.session.editor is None:
            return
        try:
            self.session.load_scene_text(text)
            self._show_status("Restored your last pose and settings.")
        except Exception:
            self._store(workspace="")  # Damaged: start clean rather than fail every time.

    def _lines_edited(self, *unused):
        if self.updating:
            return
        try:
            current = self.session.lines
            settings = LineArtSettings(
                color=current.color,
                outline_width=self.outline_width.value(),
                inner_width=self.inner_width.value(),
                crease_angle=float(self.crease_angle.value()),
                depth_sensitivity=self.depth_slider.value() / 100.0,
                opacity=self.line_opacity.value() / 100.0,
                **{name: box.isChecked() for name, box in self.line_toggles.items()})
            self._apply_lines(settings)
        except Exception as error:
            self._show_status(describe(error))

    def _opacity_edited(self, value):
        if self.updating:
            return
        try:
            self.session.set_opacity(value / 100.0)
        except Exception as error:
            self._show_status(describe(error))

    def _layer_opacity_edited(self, value):
        if self.updating:
            return
        try:
            self.session.set_layer_opacity(value / 100.0)
        except Exception as error:
            self._show_status(describe(error))

    def _toggle_limits(self, checked=False):
        if self.updating:
            return
        try:
            self.session.set_limits(bool(checked))
            self._store(joint_limits=bool(checked))
            self._show_status("Joint limits are {}.".format("on" if checked else "off"))
        except Exception as error:
            self._show_status(describe(error))

    def _output_edited(self, *unused):
        if self.updating:
            return
        try:
            settings = OutputSettings(
                mode=self.size_box.currentData() or "document",
                width=self.width_spin.value(), height=self.height_spin.value(),
                anchor=self.anchor_box.currentData() or "center",
                supersample=self.quality_box.currentData() or 1)
            self.session.set_output(settings)
            self._store(output=self.session.output.to_json())
            self._refresh_document_state()
        except Exception as error:
            self._show_status(describe(error))

    def save_pose(self):
        """Also used by the KSP: Save Pose menu action."""
        self._run_file_action(lambda: scene_files.save_pose(self, self.session))

    def load_pose(self):
        """Also used by the KSP: Load Pose menu action."""
        self._run_file_action(lambda: scene_files.load_pose(self, self.session))

    def _apply_preset(self, *unused):
        try:
            self.ensure_loaded()
            preset = self.preset_box.currentData()
            if preset is None:
                self._show_status("No bundled poses were found.")
                return
            self._show_status(scene_files.apply_preset(self.session, preset))
            log.event("preset_applied", preset=preset)
        except Exception as error:
            self._show_status("The pose could not be applied. " + describe(error))

    def _run_file_action(self, action):
        """Every save or load reports through the status line and never raises."""
        try:
            self.ensure_loaded()
            message = action()
            if message:
                self._show_status(message)
        except OSError as error:
            self._show_status("The file could not be used: {}".format(error))
        except Exception as error:
            self._show_status(describe(error))

    def _choose_line_color(self, *unused):
        try:
            color = QColorDialog.getColor(QColor(self.session.lines.color), self, "Line color")
            if color.isValid():
                self._apply_lines(replace(self.session.lines, color=color.name()))
        except Exception as error:
            self._show_status(describe(error))

    def _apply_lines(self, settings):
        self.session.set_lines(settings)
        if self.session.mode == "shaded":
            self.session.set_display("both")  # Show what the settings change.
        self._store(lineart=self.session.lines.to_json())

    def _set_display(self, mode):
        try:
            self.session.set_display(mode)
            self.viewport.setFocus()
        except Exception as error:
            self._show_status(describe(error))

    # Controls ----------------------------------------------------------------

    def canvasChanged(self, canvas):
        self._refresh_document_state()
        if self.canvas.showing:
            self.canvas.attach()

    def _refresh_document_state(self, *unused):
        """Empty state: without a document, explain instead of failing on click."""
        try:
            has_document = Krita.instance().activeDocument() is not None
        except Exception:
            has_document = True  # Unknown: leave the click to report details.
        updating = self.update_box.isChecked()
        verb = "Update" if updating else "Create"
        for button, label, tip in ((self.guide_button, "Guide Layer", GUIDE_TOOLTIP),
                                   (self.lineart_button, "Lineart Layer", LINEART_TOOLTIP)):
            button.setEnabled(has_document)
            button.setText("{} {}".format(verb, label))
            button.setToolTip(tip if has_document else NO_DOCUMENT)

    def _refresh_controls(self):
        try:
            session, editor = self.session, self.session.editor
            selected = editor.selected if editor is not None else None
            enabled = {
                "undo": editor is not None and editor.history.can_undo,
                "redo": editor is not None and editor.history.can_redo,
                "reset_joint": selected is not None,
                "reset_pose": editor is not None,
                "mirror_pose": editor is not None,
                "mirror_limb": selected is not None
                and editor.skeleton.mirror_indices[selected] != selected,
            }
            for action, button in self.buttons.items():
                button.setEnabled(enabled[action])
            mode = editor.mode if editor is not None else DRAG
            self.drag_button.setChecked(mode == DRAG)
            self.rings_button.setChecked(mode == RINGS)
            self.ortho_button.setChecked(session.camera.orthographic)
            self.show_canvas.setChecked(self.canvas.showing)
            self.pose_canvas.setChecked(self.canvas.posing)
            for view, button in self.view_buttons.items():
                button.setChecked(view == session.mode)
            self._sync_line_widgets(session.lines)
            self._sync_output_widgets(session)
            if selected is not None:
                hint = editor.hint(selected)
                if editor.skeleton.at_limit(editor.pose, selected):
                    hint += " At its limit; turn off Joint limits in the Pose tab to go further."
                self.hint.setText("{}: {}".format(display_name(editor.joint_name(selected)), hint))
            else:
                self.hint.setText(CANVAS_HINT if self.canvas.posing else IDLE_HINT)
        except Exception as error:
            self._show_status("Could not update the controls. " + describe(error))

    def _sync_line_widgets(self, lines):
        if lines == self.shown_lines:
            return  # Pose drags refresh often; restyling each time is wasteful.
        self.shown_lines = lines
        self.updating = True
        try:
            for name, box in self.line_toggles.items():
                box.setChecked(getattr(lines, name))
            self.outline_width.setValue(lines.outline_width)
            self.inner_width.setValue(lines.inner_width)
            self.crease_angle.setValue(int(round(lines.crease_angle)))
            self.depth_slider.setValue(int(round(lines.depth_sensitivity * 100)))
            self.line_opacity.setValue(int(round(lines.opacity * 100)))
            self.color_button.setText(lines.color)
            self.color_button.setStyleSheet(
                "background-color: {0}; color: {1};".format(
                    lines.color, "#ffffff" if QColor(lines.color).lightness() < 128 else "#000000"))
        finally:
            self.updating = False

    def _sync_output_widgets(self, session):
        """Show what the session holds without re-triggering the editing slots."""
        self.updating = True
        try:
            self.limits_box.setChecked(session.limits)
            self.opacity_slider.setValue(int(round(session.opacity * 100)))
            self.layer_opacity.setValue(int(round(session.layer_opacity * 100)))
            output = session.output
            self.size_box.setCurrentIndex(max(0, self.size_box.findData(output.mode)))
            self.width_spin.setValue(output.width)
            self.height_spin.setValue(output.height)
            self.anchor_box.setCurrentIndex(max(0, self.anchor_box.findData(output.anchor)))
            self.quality_box.setCurrentIndex(max(0, self.quality_box.findData(output.supersample)))
            custom = output.mode == "custom"
            for widget in (self.width_spin, self.height_spin, self.anchor_box):
                widget.setEnabled(custom)
        finally:
            self.updating = False

    def _perform(self, action):
        try:
            self.session.perform(action)
            self.viewport.setFocus()  # Keep viewport shortcuts working after a click.
        except Exception as error:
            self._show_status(describe(error))

    def _set_mode(self, mode):
        try:
            self.session.set_mode(mode)
            self.viewport.setFocus()
        except Exception as error:
            self._show_status(describe(error))

    def _show_status(self, message):
        if self.viewport.error is not None:
            self.last_error = self.viewport.error
        self.status.setText(message)

    # Output ------------------------------------------------------------------

    def create_layer(self, *unused):
        """Render the shaded posed figure into a layer (the guide)."""
        self._create("shaded", GUIDE_LAYER_NAME)

    def create_lineart_layer(self, *unused):
        """Render the posed figure's line art into a layer."""
        self._create("lines", LINEART_LAYER_NAME)

    def _create(self, mode, name):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        started = time.perf_counter()
        plan = None
        try:
            self.ensure_loaded()
            document = Krita.instance().activeDocument()
            # Describe the document first so diagnostics show rejected ones too.
            self.last_document = None
            self.last_document = read_document(document)
            shot_document = snapshot_document(document)
            session = self.session
            if session.editor is None:
                raise CapabilityError("No figure is loaded.")
            plan = session.output.resolve(shot_document.width, shot_document.height)
            log.event("export_started", width=plan.width, height=plan.height, mode=mode,
                      supersample=plan.supersample)
            shot = snapshot(session.editor.skeleton, session.editor.pose, session.camera,
                            plan.aspect, None, grid=False, opacity=session.opacity)
            image = self.offscreen.render(
                session.figure_id, session.mesh, shot, plan.render_width, plan.render_height,
                mode, line_uniforms(session.lines, plan.supersample), keep_buffers=False,
                scale_to=(plan.width, plan.height))
            pixels = qimage_to_bgra(image)
            node, verb = self._write_layer(document, shot_document, pixels, plan, name)
            session.remember_layer(shot_document.root_id, layer_id(node), name)
        except Exception as error:
            self.last_error = error
            log.event("export_failed", error=error, mode=mode)
            self.status.setText("Layer not created. " + describe(error))
        else:
            self.last_error = None
            log.event("export_finished", width=plan.width, height=plan.height, mode=mode,
                      seconds=round(time.perf_counter() - started, 3))
            self.status.setText("{} “{}” at {}.".format(verb, name, plan.describe()))
            self._refresh_document_state()
        finally:
            QApplication.restoreOverrideCursor()

    def _write_layer(self, document, shot_document, pixels, plan, name):
        """Rewrite the remembered KSP layer when asked, otherwise add a new one."""
        session = self.session
        if self.update_box.isChecked():
            target = session.layer_target(shot_document.root_id)
            node = update_layer(document, target, pixels, plan.width, plan.height,
                                snapshot=shot_document, origin=plan.origin,
                                opacity=session.layer_opacity) if target else None
            if node is not None:
                return node, "Updated"
        node = export_layer(document, pixels, plan.width, plan.height, name=name,
                            snapshot=shot_document, origin=plan.origin,
                            opacity=session.layer_opacity)
        return node, "Created"

    def _self_test(self):
        try:
            self.last_probe = self.offscreen.self_test()
            self.last_error = None
            self.status.setText("GPU self-test passed: channel order, orientation, and "
                                "transparency are correct.")
        except Exception as error:
            self.last_error = error
            self.status.setText("GPU self-test failed. " + describe(error))

    def _copy_diagnostics(self):
        try:
            error = self.last_error or self.viewport.error
            details = self.viewport.details() or self.offscreen.details
            report = diagnostics.format_report(diagnostics.collect(
                details, self.last_probe, describe(error) if error else None,
                self.last_document, canvas=self.canvas.describe()))
            QApplication.clipboard().setText(report)
            self.status.setText("Diagnostics copied to the clipboard. Nothing was sent.")
        except Exception as error:
            self.status.setText("Could not build diagnostics. " + describe(error))
