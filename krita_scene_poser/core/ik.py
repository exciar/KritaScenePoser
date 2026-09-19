"""Analytic two-bone IK for arms and legs.

Solves joint positions only; ``core.skeleton`` turns them into rotations.
The pole is a world point that the middle joint (elbow or knee) bends
toward. When the pole cannot define a bend direction, the limb keeps its
current bend plane, so dragging never flips the elbow unexpectedly.
"""

import math
from typing import NamedTuple

from .math3d import EPSILON, X_AXIS, Y_AXIS, Z_AXIS

# Keep limbs a hair short of fully straight or fully folded; both extremes
# leave the bend direction undefined for the next drag.
REACH_LIMIT = 0.9999


class TwoBoneSolution(NamedTuple):
    middle: object  # Vec3: new elbow or knee position
    end: object  # Vec3: new wrist or ankle position
    reached: bool  # False when the target was out of range and clamped


def solve_two_bone(root, middle, end, target, pole=None):
    """Place ``middle`` and ``end`` so the end reaches ``target`` if possible.

    Bone lengths come from the current positions and are preserved exactly.
    """
    upper = (middle - root).length()
    lower = (end - middle).length()
    total = upper + lower
    if upper < EPSILON or lower < EPSILON:
        raise ValueError("Two-bone IK needs bones of non-zero length.")

    to_target = target - root
    distance = to_target.length()
    if distance > EPSILON * total:
        direction = to_target / distance
    else:  # Target on the root: keep aiming the limb where it points now.
        current = end - root
        direction = (current if current.length() > EPSILON * total else middle - root).normalized()

    max_reach = total * REACH_LIMIT
    min_reach = abs(upper - lower) + total * (1.0 - REACH_LIMIT)
    reach = min(max(distance, min_reach), max_reach)
    reached = min_reach <= distance <= max_reach

    bend = _bend_direction(root, middle, direction, pole, total)
    cos_angle = (upper * upper + reach * reach - lower * lower) / (2.0 * upper * reach)
    cos_angle = min(1.0, max(-1.0, cos_angle))
    sin_angle = math.sqrt(1.0 - cos_angle * cos_angle)
    new_middle = root + direction * (upper * cos_angle) + bend * (upper * sin_angle)
    return TwoBoneSolution(new_middle, root + direction * reach, reached)


def _bend_direction(root, middle, direction, pole, scale):
    """Unit vector perpendicular to ``direction``, toward the pole if possible."""
    candidates = [] if pole is None else [pole - root]
    candidates += [middle - root, Z_AXIS * scale, X_AXIS * scale, Y_AXIS * scale]
    for candidate in candidates:
        perpendicular = candidate - direction * candidate.dot(direction)
        if perpendicular.length() > 1e-4 * scale:
            return perpendicular.normalized()
    raise AssertionError("Three axes cannot all be parallel to one direction.")
