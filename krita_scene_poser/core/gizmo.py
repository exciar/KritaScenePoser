"""Rotation-ring geometry and ring picking."""

import math

from .math3d import X_AXIS, Y_AXIS, Z_AXIS

SEGMENTS = 64
EDGE_ON = 0.2  # |axis . view| below this measures the drag along the screen tangent.


def joint_axes(rotation):
    return tuple(rotation.rotate(axis) for axis in (X_AXIS, Y_AXIS, Z_AXIS))


def perpendicular_basis(axis):
    helper = X_AXIS if abs(axis.dot(X_AXIS)) < 0.9 else Y_AXIS
    u = axis.cross(helper).normalized()
    return u, axis.cross(u)


def ring_points(pivot, axis, radius, segments=SEGMENTS):
    """World points around ``axis``; point i is at angle 2*pi*i/segments,
    counter-clockwise by the right-hand rule."""
    u, v = perpendicular_basis(axis)
    return [pivot + (u * math.cos(a) + v * math.sin(a)) * radius
            for a in (2.0 * math.pi * i / segments for i in range(segments))]


def distance_to_ring(cursor, projected):
    """Screen distance to a closed projected polyline, and the ring parameter
    (0..1 of a turn) at the nearest point. ``None`` points are skipped."""
    best, where = float("inf"), 0.0
    count = len(projected)
    for i in range(count):
        a, b = projected[i], projected[(i + 1) % count]
        if a is None or b is None:
            continue
        ax, ay = a[0], a[1]
        dx, dy = b[0] - ax, b[1] - ay
        length_squared = dx * dx + dy * dy
        t = 0.0 if length_squared == 0 else max(0.0, min(
            1.0, ((cursor[0] - ax) * dx + (cursor[1] - ay) * dy) / length_squared))
        distance = math.hypot(cursor[0] - (ax + dx * t), cursor[1] - (ay + dy * t))
        if distance < best:
            best, where = distance, (i + t) / count
    return best, where


def pick_ring(cursor, projected_rings, tolerance):
    best = None
    for index, projected in enumerate(projected_rings):
        distance, where = distance_to_ring(cursor, projected)
        if distance <= tolerance and (best is None or distance < best[1]):
            best = (index, distance, where)
    return None if best is None else (best[0], best[2])


def signed_angle(v0, v1, axis):
    return math.atan2(axis.dot(v0.cross(v1)), v0.dot(v1))


def screen_tangent(project, pivot, axis, radius, where):
    """Unit screen direction of increasing angle at ring parameter ``where``."""
    u, v = perpendicular_basis(axis)
    angle = 2.0 * math.pi * where
    point = pivot + (u * math.cos(angle) + v * math.sin(angle)) * radius
    tangent = u * -math.sin(angle) + v * math.cos(angle)
    here, ahead = project(point), project(point + tangent * (radius * 0.01))
    if here is None or ahead is None:
        return None
    dx, dy = ahead[0] - here[0], ahead[1] - here[1]
    length = math.hypot(dx, dy)
    return None if length < 1e-9 else (dx / length, dy / length)


def is_edge_on(axis, view_direction):
    return abs(axis.dot(view_direction)) < EDGE_ON
