"""KSP docker: pose viewport, view and line-art settings, canvas posing, and export."""

from dataclasses import replace
import time

from krita import DockWidget, Krita
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import diagnostics, log
from ..core.editor import DRAG, RINGS, display_name
from ..core.lineart import LIMITS, LineArtSettings, line_uniforms
from ..core.picking import FigurePicker
from ..integration import settings as ksp_settings
from ..integration.krita_document import (
    GUIDE_LAYER_NAME, LINEART_LAYER_NAME, DocumentExportError, export_layer, read_document,
    snapshot_document,
)
from ..render.gl_renderer import CapabilityError
from ..render.offscreen import OffscreenRenderer
from ..render.pixel_transfer import PixelTransferError, qimage_to_bgra
from ..render.shaders import snapshot
from ..storage.figures import DEFAULT_FIGURE, available_figures, load_figure
from ..storage.mesh_io import MeshFormatError
from ..storage.rig_io import RigFormatError
from .canvas_overlay import CanvasController
from .session import PoseSession
from .viewport import FigureViewport

DOCKER_ID = "krita_scene_poser"
TITLE = "KSP — Krita Scene Poser"
EXPECTED_ERRORS = (CapabilityError, DocumentExportError, PixelTransferError,
                   MeshFormatError, RigFormatError)
NO_DOCUMENT = "Open or create a document to create a KSP layer."
GUIDE_TOOLTIP = ("Render the posed, shaded figure from this view into a new transparent "
                 "paint layer at the document's size.")
LINEART_TOOLTIP = ("Render the posed figure's line art, using the Line Art settings, into a "
                   "new transparent paint layer at the document's size.")
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
        self.session.lines = self._stored_lines()
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
        layout.addWidget(tabs)
        self.status = QLabel("The figure loads when this docker is first shown.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        root = QWidget(self)
        root.setLayout(layout)
        self.setWidget(root)
        self.visibilityChanged.connect(self._on_visibility)
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
        page = QWidget()
        page.setLayout(grid)
        return page

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
        self.color_button = QPushButton()
        self.color_button.setToolTip("Line color.")
        self.color_button.clicked.connect(self._choose_line_color)
        form.addRow("Color", self.color_button)
        page = QWidget()
        page.setLayout(form)
        return page

    def _build_output_tab(self):
        self.guide_button = QPushButton("Create Guide Layer")
        self.guide_button.setToolTip(GUIDE_TOOLTIP)
        self.guide_button.clicked.connect(self.create_layer)
        self.lineart_button = QPushButton("Create Lineart Layer")
        self.lineart_button.setToolTip(LINEART_TOOLTIP)
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
        page = QWidget()
        page.setLayout(grid)
        return page

    # Figures -----------------------------------------------------------------

    def ensure_loaded(self):
        if not self.loaded:
            self.loaded = True
            self._populate_figures()

    def _on_visibility(self, visible=True):
        try:
            self._refresh_document_state()
            if visible:
                self.ensure_loaded()
        except Exception as error:
            self._show_status("Could not start KSP. " + describe(error))

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
    def _stored_lines():
        try:
            return LineArtSettings.from_json(ksp_settings.load().lineart)
        except Exception:
            return LineArtSettings()

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
                **{name: box.isChecked() for name, box in self.line_toggles.items()})
            self._apply_lines(settings)
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
        try:
            ksp_settings.update(ksp_settings.Settings(), lineart=self.session.lines.to_json())
        except Exception as error:
            self._show_status("Line settings could not be saved. " + describe(error))

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
        for button, tip in ((self.guide_button, GUIDE_TOOLTIP), (self.lineart_button, LINEART_TOOLTIP)):
            button.setEnabled(has_document)
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
            if selected is not None:
                self.hint.setText("{}: {}".format(display_name(editor.joint_name(selected)),
                                                  editor.hint(selected)))
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
            self.color_button.setText(lines.color)
            self.color_button.setStyleSheet(
                "background-color: {0}; color: {1};".format(
                    lines.color, "#ffffff" if QColor(lines.color).lightness() < 128 else "#000000"))
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
        """Render the shaded posed figure into a new layer (the guide)."""
        self._create("shaded", GUIDE_LAYER_NAME)

    def create_lineart_layer(self, *unused):
        """Render the posed figure's line art into a new layer."""
        self._create("lines", LINEART_LAYER_NAME)

    def _create(self, mode, name):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        started = time.perf_counter()
        width = height = None
        try:
            self.ensure_loaded()
            document = Krita.instance().activeDocument()
            # Describe the document first so diagnostics show rejected ones too.
            self.last_document = None
            self.last_document = read_document(document)
            shot_document = snapshot_document(document)
            width, height = shot_document.width, shot_document.height
            session = self.session
            if session.editor is None:
                raise CapabilityError("No figure is loaded.")
            log.event("export_started", width=width, height=height, mode=mode)
            shot = snapshot(session.editor.skeleton, session.editor.pose, session.camera,
                            width / height, None, grid=False)
            image = self.offscreen.render(session.figure_id, session.mesh, shot, width, height,
                                          mode, line_uniforms(session.lines), keep_buffers=False)
            export_layer(document, qimage_to_bgra(image), width, height,
                         name=name, snapshot=shot_document)
        except Exception as error:
            self.last_error = error
            log.event("export_failed", error=error, width=width, height=height, mode=mode)
            self.status.setText("Layer not created. " + describe(error))
        else:
            self.last_error = None
            log.event("export_finished", width=width, height=height, mode=mode,
                      seconds=round(time.perf_counter() - started, 3))
            self.status.setText("Created “{}” at {} × {}.".format(name, width, height))
        finally:
            QApplication.restoreOverrideCursor()

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
