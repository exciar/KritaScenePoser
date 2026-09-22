"""CPU picking against the visible surface, with capsules as a broad phase.

An ID render would be simpler, but GLSL ES 1.00 cannot keep joint ids from blending
across a triangle.
"""

import math
from typing import NamedTuple

from .math3d import Vec3


class Hit(NamedTuple):
    joint: int
    distance: float  # Along the ray.
    point: Vec3


def ray_triangle(origin, direction, a, b, c):
    """Möller-Trumbore distance to a triangle (either side), or None."""
    e1x, e1y, e1z = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    e2x, e2y, e2z = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    dx, dy, dz = direction
    px, py, pz = dy * e2z - dz * e2y, dz * e2x - dx * e2z, dx * e2y - dy * e2x
    determinant = e1x * px + e1y * py + e1z * pz
    if -1e-12 < determinant < 1e-12:
        return None
    inverse = 1.0 / determinant
    tx, ty, tz = origin[0] - a[0], origin[1] - a[1], origin[2] - a[2]
    u = (tx * px + ty * py + tz * pz) * inverse
    if u < 0.0 or u > 1.0:
        return None
    qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
    v = (dx * qx + dy * qy + dz * qz) * inverse
    if v < 0.0 or u + v > 1.0:
        return None
    t = (e2x * qx + e2y * qy + e2z * qz) * inverse
    return t if t >= 0.0 else None


def segment_distance(p, a, b):
    ab = b - a
    length_squared = ab.dot(ab)
    t = 0.0 if length_squared == 0.0 else max(0.0, min(1.0, (p - a).dot(ab) / length_squared))
    return (p - (a + ab * t)).length()


def ray_capsule(origin, direction, a, b, radius):
    """Distance along a unit-direction ray to a capsule, or None."""
    ba, oa = b - a, origin - a
    baba, bard, baoa = ba.dot(ba), ba.dot(direction), ba.dot(oa)
    rdoa, oaoa = direction.dot(oa), oa.dot(oa)
    k2 = baba - bard * bard
    if k2 > 1e-12:
        k1 = baba * rdoa - baoa * bard
        k0 = baba * oaoa - baoa * baoa - radius * radius * baba
        h = k1 * k1 - k2 * k0
        if h < 0.0:
            return None
        t = (-k1 - math.sqrt(h)) / k2
        y = baoa + t * bard
        if 0.0 < y < baba:
            return t if t >= 0.0 else None
        cap = a if y <= 0.0 else b
    else:  # Looking straight down the bone: only an end sphere can be hit first.
        cap = a if (a - origin).dot(direction) < (b - origin).dot(direction) else b
    offset = origin - cap
    k1 = direction.dot(offset)
    h = k1 * k1 - (offset.dot(offset) - radius * radius)
    if h < 0.0:
        return None
    t = -k1 - math.sqrt(h)
    return t if t >= 0.0 else None


class JointCapsules:
    def __init__(self, local_tails, radii):
        self.local_tails = tuple(local_tails)  # Tail in each joint's rest frame.
        self.radii = tuple(radii)

    @staticmethod
    def from_figure(rig, mesh, percentile=0.9, minimum=0.008):
        """Radius per joint: that percentile of its dominant vertices' distance to the bone."""
        rest = rig.skeleton.rest_transforms
        segments = [(t.position, j.tail) for j, t in zip(rig.joints, rest)]
        buckets = [[] for _ in rig.joints]
        positions, joints = mesh.positions, mesh.joints
        for v in range(mesh.vertex_count):
            joint = joints[4 * v]  # Slot 0 holds the largest weight.
            a, b = segments[joint]
            buckets[joint].append(segment_distance(
                Vec3(positions[3 * v], positions[3 * v + 1], positions[3 * v + 2]), a, b))
        radii = []
        for bucket in buckets:
            bucket.sort()
            radii.append(max(minimum, bucket[int(percentile * (len(bucket) - 1))]) if bucket else minimum)
        local_tails = [t.rotation.inverse().rotate(j.tail - t.position)
                       for j, t in zip(rig.joints, rest)]
        return JointCapsules(local_tails, radii)

    def segments(self, skeleton, pose, transforms=None):
        transforms = transforms or skeleton.transforms(pose)
        return [(t.position, t.position + t.rotation.rotate(tail * pose.root_scale))
                for t, tail in zip(transforms, self.local_tails)]

    def pick(self, ray, skeleton, pose, minimum_radius=lambda point: 0.0):
        """Nearest capsule hit by ``ray``. ``minimum_radius(point)`` widens small
        parts to a pen-friendly screen size at their depth."""
        best = None
        for joint, (a, b) in enumerate(self.segments(skeleton, pose)):
            radius = max(self.radii[joint] * pose.root_scale, minimum_radius((a + b) * 0.5))
            t = ray_capsule(ray.origin, ray.direction, a, b, radius)
            if t is not None and (best is None or t < best.distance):
                best = Hit(joint, t, ray.point_at(t))
        return best


def skin_point(matrices, joints, weights, positions, vertex):
    """One vertex skinned on the CPU; matches the GPU shader."""
    x, y, z = positions[3 * vertex], positions[3 * vertex + 1], positions[3 * vertex + 2]
    rx = ry = rz = 0.0
    for slot in range(4):
        weight = weights[4 * vertex + slot]
        if weight:
            m = matrices[joints[4 * vertex + slot]].m
            w = weight / 255.0
            rx += w * (m[0] * x + m[4] * y + m[8] * z + m[12])
            ry += w * (m[1] * x + m[5] * y + m[9] * z + m[13])
            rz += w * (m[2] * x + m[6] * y + m[10] * z + m[14])
    return rx, ry, rz


class FigurePicker:
    """Exact surface picking for one figure (rig plus mesh)."""

    BOUND_MARGIN = 1.25  # Posing can pull blended vertices beyond rest bounds.

    def __init__(self, rig, mesh):
        self.mesh = mesh
        self.capsules = JointCapsules.from_figure(rig, mesh)
        self.triangles = [[] for _ in rig.joints]
        indices, joints = mesh.indices, mesh.joints
        for start in range(0, len(indices), 3):
            self.triangles[joints[4 * indices[start]]].append(start)
        rest = rig.skeleton.rest_transforms
        positions = mesh.positions
        self.bounds = []
        for joint, starts in enumerate(self.triangles):
            a, b = rest[joint].position, rig.joints[joint].tail
            farthest = self.capsules.radii[joint]
            vertices = {vertex for start in starts for vertex in indices[start:start + 3]}
            for vertex in vertices:
                p = Vec3(positions[3 * vertex], positions[3 * vertex + 1], positions[3 * vertex + 2])
                farthest = max(farthest, segment_distance(p, a, b))
            self.bounds.append(farthest * self.BOUND_MARGIN + 0.01)

    def segments(self, skeleton, pose, transforms=None):
        return self.capsules.segments(skeleton, pose, transforms)

    def pick(self, ray, skeleton, pose, minimum_radius=lambda point: 0.0):
        """The joint whose surface is nearest along ``ray``; near misses fall
        back to widened capsules."""
        segments = self.capsules.segments(skeleton, pose)
        candidates = []
        for joint, (a, b) in enumerate(segments):
            if self.triangles[joint]:
                t = ray_capsule(ray.origin, ray.direction, a, b, self.bounds[joint] * pose.root_scale)
                if t is not None:
                    candidates.append((t, joint))
        candidates.sort()
        matrices = skeleton.skinning_matrices(pose)
        mesh = self.mesh
        origin, direction = tuple(ray.origin), tuple(ray.direction)
        best = None
        for entry, joint in candidates:
            if best is not None and entry > best.distance:
                break  # Every remaining part starts behind the current hit.
            skinned = {}
            for start in self.triangles[joint]:
                corners = []
                for vertex in mesh.indices[start:start + 3]:
                    if vertex not in skinned:
                        skinned[vertex] = skin_point(matrices, mesh.joints, mesh.weights,
                                                     mesh.positions, vertex)
                    corners.append(skinned[vertex])
                t = ray_triangle(origin, direction, *corners)
                if t is not None and (best is None or t < best.distance):
                    best = Hit(joint, t, ray.point_at(t))
        if best is not None:
            return best
        return self.capsules.pick(ray, skeleton, pose, minimum_radius)
