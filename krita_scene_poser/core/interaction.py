"""Pointer interaction shared by the docker viewport and the canvas overlay.

Pure Python. A view converts its input to *camera-screen* coordinates: pixels
of the image the camera renders, top-left origin. That is the viewport's own
widget, or the document for the canvas overlay. ``Screen.pixel_scale`` is
how many camera-screen pixels one pixel on the user's display spans, so pick
tolerance, ring size, and drag speeds stay constant on screen at any canvas
zoom. Gesture math stays consistent even on a rotated or mirrored canvas,
because the rays and deltas are all measured in camera-screen space.
"""

from dataclasses import dataclass

from .editor import IK_ENDS, RINGS
from .gizmo import joint_axes, pick_ring, ring_points, screen_tangent
from .math3d import project

PICK_PIXELS = 10  # Minimum pick radius on the display, for pens.
RING_PIXELS = 60
RING_TOLERANCE = 10
PIVOT_PIXELS = 6
IK_MARKER_PIXELS = 14
DOLLY_RATE = 0.01  # Zoom steps per display pixel of Ctrl+drag.
CAMERA_KINDS = ("orbit", "pan", "dolly")


@dataclass(frozen=True)
class Screen:
    width: float  # Camera-screen size: the viewport, or the document.
    height: float
    pixel_scale: float = 1.0  # Camera-screen pixels per display pixel.


@dataclass(frozen=True)
class Primitive:
    """Overlay drawing in camera-screen coordinates; sizes are display pixels."""
    kind: str  # line, circle, or segment
    a: tuple
    b: tuple = ()
    radius: float = 0.0
    style: str = "selection"  # selection, ik, or ring
    axis: int = -1  # Ring axis: 0 X, 1 Y, 2 Z.
    front: bool = True  # Ring segment faces the viewer.


class PoseInteraction:
    """One view's pointer state over a shared editor and camera."""

    def __init__(self, editor, camera):
        self.editor, self.camera = editor, camera
        self.drag = None  # [kind, x, y]; kind is pose or a camera kind.

    def view_projection(self, screen):
        return self.camera.view_projection(screen.width / screen.height)

    def project(self, point, screen, view_projection=None):
        result = project(point, view_projection or self.view_projection(screen),
                         screen.width, screen.height)
        return None if result is None else (result[0], result[1])

    def view_direction(self, point):
        if self.camera.orthographic:
            return self.camera.forward()
        return (point - self.camera.eye()).normalized()

    def rings(self, screen):
        """(pivot, axes, world radius) for the selected joint's rotation rings."""
        transform = self.editor.transforms()[self.editor.selected]
        radius = (self.camera.world_per_pixel(transform.position, screen.height)
                  * RING_PIXELS * screen.pixel_scale)
        return transform.position, joint_axes(transform.rotation), radius

    # Pointer ---------------------------------------------------------------------

    def begin_camera(self, kind, x, y):
        self.drag = [kind, x, y]
        return kind

    def press(self, x, y, screen, shift=False, ctrl=False, navigate_on_empty=False):
        """Primary-button press. Returns ring, pose, select, a camera kind, or None."""
        editor, camera = self.editor, self.camera
        scale = screen.pixel_scale
        ray = camera.ray(x, y, screen.width, screen.height)
        if editor.mode == RINGS and editor.selected is not None:
            pivot, axes, radius = self.rings(screen)
            matrix = self.view_projection(screen)
            projected = [[self.project(p, screen, matrix) for p in ring_points(pivot, axis, radius)]
                         for axis in axes]
            ring = pick_ring((x, y), projected, RING_TOLERANCE * scale)
            if ring is not None:
                axis = axes[ring[0]]
                tangent = screen_tangent(lambda p: self.project(p, screen, matrix),
                                         pivot, axis, radius, ring[1])
                editor.begin_ring_drag(editor.selected, axis, ray, self.view_direction(pivot),
                                       x / scale, y / scale, tangent, RING_PIXELS)
                self.drag = ["pose", x, y]
                return "ring"
        hit = editor.pick(ray, lambda point: camera.world_per_pixel(point, screen.height)
                          * PICK_PIXELS * scale)
        if hit is None:
            editor.select(None)
            if navigate_on_empty:
                return self.begin_camera("pan" if shift else "dolly" if ctrl else "orbit", x, y)
            return None
        if editor.mode == RINGS:
            editor.select(hit.joint)
            return "select"
        editor.begin_drag(hit.joint, hit.point, self.view_direction(hit.point),
                          x / scale, y / scale, shift, ctrl)
        self.drag = ["pose", x, y]
        return "pose"

    def move(self, x, y, screen):
        """Continue the active drag; returns True when something changed."""
        if self.drag is None:
            return False
        kind, last_x, last_y = self.drag
        scale = screen.pixel_scale
        if kind == "orbit":
            self.camera.orbit((x - last_x) / scale, (y - last_y) / scale)
        elif kind == "pan":
            self.camera.pan(x - last_x, y - last_y, screen.height)
        elif kind == "dolly":
            self.camera.zoom((last_y - y) / scale * DOLLY_RATE)  # Dragging up zooms in.
        else:
            self.editor.drag(self.camera.ray(x, y, screen.width, screen.height), x / scale, y / scale)
        self.drag[1:] = [x, y]
        return True

    def release(self):
        """End the drag; returns True when a pose gesture added an undo entry."""
        kind, self.drag = (self.drag or [None])[0], None
        return kind == "pose" and self.editor.end_drag()

    def cancel(self):
        """Esc: abandon a pose drag and restore its starting pose."""
        cancelled = self.editor.cancel_drag()
        self.drag = None
        return cancelled

    # Overlay ---------------------------------------------------------------------

    def overlay(self, screen):
        """Selection markers and rings to draw over the figure."""
        editor = self.editor
        if editor is None or editor.selected is None:
            return []
        matrix = self.view_projection(screen)
        start, end = editor.segments()[editor.selected]
        a, b = self.project(start, screen, matrix), self.project(end, screen, matrix)
        if a is None or b is None:
            return []
        primitives = [Primitive("line", a, b), Primitive("circle", a, radius=PIVOT_PIXELS)]
        if editor.mode == RINGS:
            pivot, axes, radius = self.rings(screen)
            toward_viewer = (-self.camera.forward() if self.camera.orthographic
                             else self.camera.eye() - pivot)
            for index, axis in enumerate(axes):
                points = ring_points(pivot, axis, radius)
                for i, point in enumerate(points):
                    following = points[(i + 1) % len(points)]
                    p, q = self.project(point, screen, matrix), self.project(following, screen, matrix)
                    if p is not None and q is not None:
                        front = ((point + following) * 0.5 - pivot).dot(toward_viewer) >= 0.0
                        primitives.append(Primitive("segment", p, q, style="ring", axis=index,
                                                    front=front))
        elif editor.joint_name(editor.selected) in IK_ENDS:
            primitives.append(Primitive("circle", a, radius=IK_MARKER_PIXELS, style="ik"))
        return primitives
