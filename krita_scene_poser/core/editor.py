"""Authoritative pose state and posing gestures (pure Python).

The UI turns pointer events into rays and calls these methods; it never edits
joints directly. Every drag recomputes from the pose at the gesture's start,
so repeated events cannot drift. Esc (``cancel_drag``) restores that exact
pose, and each completed gesture or command adds one undo entry.
"""

from dataclasses import dataclass, replace

from .commands import PoseHistory
from .gizmo import is_edge_on, signed_angle
from .math3d import IDENTITY, Quat, Vec3, Y_AXIS, ZERO

DRAG, RINGS = "drag", "rings"
ROOT_JOINT = "hips"
# IK end joint -> default bend direction of its elbow or knee, in the figure's frame.
IK_ENDS = {"hand.L": Vec3(0.0, 0.0, -1.0), "hand.R": Vec3(0.0, 0.0, -1.0),
           "foot.L": Vec3(0.0, 0.0, 1.0), "foot.R": Vec3(0.0, 0.0, 1.0)}
TWIST_RATE = TURN_RATE = 0.01  # Radians per pixel.
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
WORDS = {"upper_arm": "upper arm", "toe": "toes"}


def display_name(name):
    """``upper_arm.L`` -> ``Left upper arm``; ``index.02.R`` -> ``Right index finger 2``."""
    side = {".L": "Left", ".R": "Right"}.get(name[-2:])
    base = name[:-2] if side else name
    parts = base.split(".")
    label = WORDS.get(parts[0], parts[0].replace("_", " "))
    if parts[0] in FINGERS and len(parts) == 2 and parts[1].isdigit():
        label = "{} {}".format("thumb" if parts[0] == "thumb" else parts[0] + " finger", int(parts[1]))
    return "{} {}".format(side, label) if side else label[:1].upper() + label[1:]


@dataclass(frozen=True)
class Gesture:
    kind: str  # aim, twist, ik, move, turn, or ring
    joint: int
    start: object  # Pose at the start of the gesture.
    x: float
    y: float
    grab: Vec3 = ZERO  # World point under the cursor at the start.
    normal: Vec3 = ZERO  # View direction: the drag plane's normal.
    pivot: Vec3 = ZERO  # Joint position at the start.
    axis: Vec3 = ZERO  # Bone direction (twist) or ring axis.
    pole: Vec3 = ZERO  # IK bend target.
    start_vector: Vec3 = ZERO  # Ring plane vector; zero means tangent mode.
    tangent: tuple = (1.0, 0.0)  # Screen direction of increasing ring angle.
    radius_px: float = 60.0


class PoseEditor:
    def __init__(self, skeleton, picker):
        self.skeleton, self.picker = skeleton, picker
        self.pose = skeleton.rest_pose()
        self.selected = None
        self.mode = DRAG
        self.history = PoseHistory()
        self.gesture = None

    # Figure and selection -------------------------------------------------

    def set_figure(self, skeleton, picker):
        """Switch figures; the pose carries over when the joint layout matches."""
        self.cancel_drag()
        same = [j.name for j in skeleton.joints] == [j.name for j in self.skeleton.joints]
        self.skeleton, self.picker = skeleton, picker
        if not same:
            self.pose, self.selected = skeleton.rest_pose(), None
            self.history.clear()

    def select(self, joint):
        self.selected = joint

    def joint_name(self, joint):
        return self.skeleton.joints[joint].name

    def transforms(self):
        return self.skeleton.transforms(self.pose)

    def segments(self):
        return self.picker.segments(self.skeleton, self.pose)

    def pick(self, ray, minimum_radius=lambda point: 0.0):
        return self.picker.pick(ray, self.skeleton, self.pose, minimum_radius)

    def subtree(self, joint):
        """``joint`` and all of its descendants (joints are ordered parents-first)."""
        members = [joint]
        for index in range(joint + 1, len(self.skeleton.joints)):
            if self.skeleton.joints[index].parent in members:
                members.append(index)
        return members

    # Gestures ----------------------------------------------------------------

    def drag_kind(self, joint, shift=False, ctrl=False):
        name = self.joint_name(joint)
        if name == ROOT_JOINT:
            return "turn" if shift else "aim" if ctrl else "move"
        if shift:
            return "twist"
        return "ik" if name in IK_ENDS and not ctrl else "aim"

    def hint(self, joint):
        name = self.joint_name(joint)
        if self.mode == RINGS:
            return "Drag a ring: red and blue bend, green twists."
        if name == ROOT_JOINT:
            return "Drag to move the figure. Shift: turn it. Ctrl: tilt the hips."
        if name in IK_ENDS:
            return "Drag to place it (IK). Ctrl: rotate it. Shift: twist."
        return "Drag to aim it. Shift: twist."

    def begin_drag(self, joint, hit, view_direction, x, y, shift=False, ctrl=False):
        """Start a Drag-mode gesture on ``joint`` grabbed at world point ``hit``."""
        self.cancel_drag()
        self.selected = joint
        kind = self.drag_kind(joint, shift, ctrl)
        transforms = self.skeleton.transforms(self.pose)
        start, end = self.picker.segments(self.skeleton, self.pose, transforms)[joint]
        pivot = transforms[joint].position
        grab = hit
        if kind == "aim" and (hit - pivot).length() < 0.25 * (end - start).length():
            grab = end  # Grabbed at the pivot: aim by the tail instead.
        pole = self._pole(joint, transforms) if kind == "ik" else ZERO
        self.gesture = Gesture(kind, joint, self.pose, x, y, grab=grab, normal=view_direction,
                               pivot=pivot, axis=(end - start).normalized(), pole=pole)
        return kind

    def begin_ring_drag(self, joint, axis, ray, view_direction, x, y, tangent, radius_px):
        """Start a Rings-mode rotation of ``joint`` about world ``axis``."""
        self.cancel_drag()
        self.selected = joint
        pivot = self.skeleton.transforms(self.pose)[joint].position
        start_vector = ZERO
        if not is_edge_on(axis, view_direction):
            t = ray.intersect_plane(pivot, axis)
            if t is not None:
                vector = ray.point_at(t) - pivot
                start_vector = vector if vector.length() > 1e-6 else ZERO
        self.gesture = Gesture("ring", joint, self.pose, x, y, pivot=pivot, axis=axis,
                               start_vector=start_vector, tangent=tangent or (1.0, 0.0),
                               radius_px=max(radius_px, 1.0))
        return "ring"

    def drag(self, ray, x, y):
        """Update the active gesture; returns True when the pose changed."""
        g = self.gesture
        if g is None:
            return False
        skeleton, point = self.skeleton, None
        if g.kind in ("aim", "move", "ik"):
            t = ray.intersect_plane(g.grab, g.normal)
            if t is None:
                return False
            point = ray.point_at(t)
        if g.kind == "aim":
            target = point - g.pivot
            if target.length() < 1e-6:
                return False
            pose = skeleton.rotate_world(g.start, g.joint, Quat.between(g.grab - g.pivot, target))
        elif g.kind == "move":
            pose = replace(g.start, root_translation=g.start.root_translation + (point - g.grab))
        elif g.kind == "turn":
            turn = Quat.from_axis_angle(Y_AXIS, (x - g.x) * TURN_RATE)
            pose = replace(g.start, root_rotation=(turn * g.start.root_rotation).normalized())
        elif g.kind == "twist":
            pose = skeleton.rotate_world(g.start, g.joint,
                                         Quat.from_axis_angle(g.axis, (x - g.x) * TWIST_RATE))
        elif g.kind == "ik":
            pose, _ = skeleton.solve_ik(g.start, g.joint, g.pivot + (point - g.grab), g.pole)
        else:  # ring
            if g.start_vector != ZERO:
                t = ray.intersect_plane(g.pivot, g.axis)
                if t is None:
                    return False
                current = ray.point_at(t) - g.pivot
                if current.length() < 1e-6:
                    return False
                angle = signed_angle(g.start_vector, current, g.axis)
            else:
                angle = ((x - g.x) * g.tangent[0] + (y - g.y) * g.tangent[1]) / g.radius_px
            pose = skeleton.rotate_world(g.start, g.joint, Quat.from_axis_angle(g.axis, angle))
        self.pose = pose
        return True

    def end_drag(self):
        """Finish the gesture; returns True when it added an undo entry."""
        gesture, self.gesture = self.gesture, None
        return gesture is not None and self.history.record(gesture.start, self.pose)

    def cancel_drag(self):
        if self.gesture is None:
            return False
        self.pose, self.gesture = self.gesture.start, None
        return True

    def _pole(self, end, transforms):
        """A point the elbow or knee keeps bending toward during an IK drag."""
        middle = self.skeleton.joints[end].parent
        upper = self.skeleton.joints[middle].parent
        root, mid, tip = (transforms[i].position for i in (upper, middle, end))
        length = (mid - root).length() + (tip - mid).length()
        bend = mid - (root + tip) * 0.5
        axis = tip - root
        if axis.length() > 1e-9:
            direction = axis.normalized()
            bend = bend - direction * bend.dot(direction)
        if bend.length() < 1e-3 * length:
            bend = self.pose.root_rotation.rotate(IK_ENDS[self.joint_name(end)])
        return mid + bend.normalized() * length

    # Commands ---------------------------------------------------------------

    def _apply(self, pose):
        self.cancel_drag()
        before, self.pose = self.pose, pose
        return self.history.record(before, pose)

    def replace_pose(self, pose):
        """Put a whole pose in place, such as a loaded file, as one undo step."""
        return self._apply(pose)

    def reset_joint(self):
        if self.selected is None:
            return False
        pose = self.pose.with_rotation(self.selected, IDENTITY)
        if self.joint_name(self.selected) == ROOT_JOINT:
            pose = replace(pose, root_translation=ZERO, root_rotation=IDENTITY)
        return self._apply(pose)

    def reset_pose(self):
        return self._apply(replace(self.skeleton.rest_pose(), root_scale=self.pose.root_scale))

    def mirror_pose(self):
        return self._apply(self.skeleton.mirror_pose(self.pose))

    def mirror_limb(self):
        """Copy the selected limb's pose onto its other-side counterpart."""
        if self.selected is None or self.skeleton.mirror_indices[self.selected] == self.selected:
            return False
        targets = [self.skeleton.mirror_indices[i] for i in self.subtree(self.selected)]
        return self._apply(self.skeleton.mirror_pose(self.pose, targets))

    def undo(self):
        self.cancel_drag()
        pose = self.history.undo()
        if pose is None:
            return False
        self.pose = pose
        return True

    def redo(self):
        self.cancel_drag()
        pose = self.history.redo()
        if pose is None:
            return False
        self.pose = pose
        return True
