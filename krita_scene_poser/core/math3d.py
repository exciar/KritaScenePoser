"""Vectors, quaternions, 4x4 matrices and rays, without NumPy.

Right-handed, Y up, cameras look down -Z. Mat4 is column-major (element (r, c) is
m[c * 4 + r]) so it goes straight to glUniformMatrix4fv. Quaternions are
(w, x, y, z). Angles are radians; screen y points down.
"""

import math
from typing import NamedTuple

EPSILON = 1e-9


class Vec3(NamedTuple):
    x: float
    y: float
    z: float

    def __add__(self, other):
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar):
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar):
        return Vec3(self.x / scalar, self.y / scalar, self.z / scalar)

    def __neg__(self):
        return Vec3(-self.x, -self.y, -self.z)

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other):
        return Vec3(self.y * other.z - self.z * other.y,
                    self.z * other.x - self.x * other.z,
                    self.x * other.y - self.y * other.x)

    def length(self):
        return math.sqrt(self.dot(self))

    def normalized(self):
        length = self.length()
        if length < EPSILON:
            raise ValueError("Cannot normalize a zero-length vector.")
        return self / length

    def lerp(self, other, t):
        return self + (other - self) * t

    def is_close(self, other, tolerance=1e-6):
        return all(abs(a - b) <= tolerance for a, b in zip(self, other))


ZERO = Vec3(0.0, 0.0, 0.0)
X_AXIS = Vec3(1.0, 0.0, 0.0)
Y_AXIS = Vec3(0.0, 1.0, 0.0)
Z_AXIS = Vec3(0.0, 0.0, 1.0)


class Quat(NamedTuple):
    w: float
    x: float
    y: float
    z: float

    @staticmethod
    def from_axis_angle(axis, angle):
        axis = axis.normalized()
        half = angle * 0.5
        s = math.sin(half)
        return Quat(math.cos(half), axis.x * s, axis.y * s, axis.z * s)

    @staticmethod
    def between(source, target):
        """Shortest rotation that turns direction ``source`` into ``target``."""
        a, b = source.normalized(), target.normalized()
        d = a.dot(b)
        if d >= 1.0 - EPSILON:
            return IDENTITY
        if d <= -1.0 + EPSILON:
            axis = X_AXIS.cross(a)
            if axis.length() < 1e-6:
                axis = Y_AXIS.cross(a)
            return Quat.from_axis_angle(axis, math.pi)
        axis = a.cross(b)
        return Quat(1.0 + d, axis.x, axis.y, axis.z).normalized()

    @staticmethod
    def from_matrix(matrix):
        """Rotation of a Mat4 whose upper 3x3 is a rotation with any positive scale."""
        m = matrix.m
        columns = [Vec3(m[0], m[1], m[2]), Vec3(m[4], m[5], m[6]), Vec3(m[8], m[9], m[10])]
        (r00, r10, r20), (r01, r11, r21), (r02, r12, r22) = (c.normalized() for c in columns)
        trace = r00 + r11 + r22
        if trace > 0.0:
            s = math.sqrt(trace + 1.0) * 2.0
            q = Quat(0.25 * s, (r21 - r12) / s, (r02 - r20) / s, (r10 - r01) / s)
        elif r00 > r11 and r00 > r22:
            s = math.sqrt(1.0 + r00 - r11 - r22) * 2.0
            q = Quat((r21 - r12) / s, 0.25 * s, (r01 + r10) / s, (r02 + r20) / s)
        elif r11 > r22:
            s = math.sqrt(1.0 + r11 - r00 - r22) * 2.0
            q = Quat((r02 - r20) / s, (r01 + r10) / s, 0.25 * s, (r12 + r21) / s)
        else:
            s = math.sqrt(1.0 + r22 - r00 - r11) * 2.0
            q = Quat((r10 - r01) / s, (r02 + r20) / s, (r12 + r21) / s, 0.25 * s)
        return q.normalized()

    def __mul__(self, other):
        w1, x1, y1, z1 = self
        w2, x2, y2, z2 = other
        return Quat(w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
                    w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                    w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                    w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)

    def __neg__(self):
        return Quat(-self.w, -self.x, -self.y, -self.z)

    def dot(self, other):
        return self.w * other.w + self.x * other.x + self.y * other.y + self.z * other.z

    def length(self):
        return math.sqrt(self.dot(self))

    def normalized(self):
        length = self.length()
        if length < EPSILON:
            raise ValueError("Cannot normalize a zero-length quaternion.")
        return Quat(self.w / length, self.x / length, self.y / length, self.z / length)

    def conjugate(self):
        return Quat(self.w, -self.x, -self.y, -self.z)

    def inverse(self):
        return self.conjugate()

    def rotate(self, v):
        u = Vec3(self.x, self.y, self.z)
        t = u.cross(v) * 2.0
        return v + t * self.w + u.cross(t)

    def angle(self):
        return 2.0 * math.acos(min(1.0, abs(self.w)))

    def slerp(self, other, t):
        d = self.dot(other)
        if d < 0.0:
            other, d = -other, -d
        if d > 0.9995:
            return Quat(*(a + (b - a) * t for a, b in zip(self, other))).normalized()
        theta = math.acos(d)
        s = math.sin(theta)
        a, b = math.sin((1.0 - t) * theta) / s, math.sin(t * theta) / s
        return Quat(*(a * p + b * q for p, q in zip(self, other)))

    def is_close(self, other, tolerance=1e-6):
        """Compare rotations; q and -q are the same rotation."""
        return (all(abs(a - b) <= tolerance for a, b in zip(self, other))
                or all(abs(a + b) <= tolerance for a, b in zip(self, other)))


IDENTITY = Quat(1.0, 0.0, 0.0, 0.0)


class Mat4:
    __slots__ = ("m",)

    def __init__(self, m):
        if len(m) != 16:
            raise ValueError("Mat4 needs 16 values.")
        self.m = tuple(float(value) for value in m)

    def __repr__(self):
        return "Mat4({})".format(self.m)

    def __eq__(self, other):
        return isinstance(other, Mat4) and self.m == other.m

    def __hash__(self):
        return hash(self.m)

    def at(self, row, column):
        return self.m[column * 4 + row]

    @staticmethod
    def identity():
        return Mat4((1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))

    @staticmethod
    def translation(v):
        return Mat4((1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, v.x, v.y, v.z, 1))

    @staticmethod
    def from_trs(translation=ZERO, rotation=IDENTITY, scale=1.0):
        """Translate @ rotate @ scale; ``scale`` is a number or a Vec3."""
        sx, sy, sz = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
        w, x, y, z = rotation
        return Mat4((
            (1 - 2 * (y * y + z * z)) * sx, (2 * (x * y + w * z)) * sx, (2 * (x * z - w * y)) * sx, 0,
            (2 * (x * y - w * z)) * sy, (1 - 2 * (x * x + z * z)) * sy, (2 * (y * z + w * x)) * sy, 0,
            (2 * (x * z + w * y)) * sz, (2 * (y * z - w * x)) * sz, (1 - 2 * (x * x + y * y)) * sz, 0,
            translation.x, translation.y, translation.z, 1))

    @staticmethod
    def perspective(fov_y, aspect, near, far):
        if not (0.0 < fov_y < math.pi and aspect > 0.0 and 0.0 < near < far):
            raise ValueError("Invalid perspective parameters.")
        f = 1.0 / math.tan(fov_y / 2.0)
        return Mat4((f / aspect, 0, 0, 0, 0, f, 0, 0,
                     0, 0, (far + near) / (near - far), -1,
                     0, 0, 2.0 * far * near / (near - far), 0))

    @staticmethod
    def orthographic(left, right, bottom, top, near, far):
        if left == right or bottom == top or near == far:
            raise ValueError("Invalid orthographic volume.")
        return Mat4((2.0 / (right - left), 0, 0, 0, 0, 2.0 / (top - bottom), 0, 0,
                     0, 0, -2.0 / (far - near), 0,
                     -(right + left) / (right - left), -(top + bottom) / (top - bottom),
                     -(far + near) / (far - near), 1))

    @staticmethod
    def look_at(eye, target, up=Y_AXIS):
        """View matrix placing ``eye`` at the origin looking down -Z at ``target``."""
        forward = (target - eye).normalized()
        side = forward.cross(up)
        if side.length() < 1e-6:
            raise ValueError("The up vector is parallel to the view direction.")
        side = side.normalized()
        true_up = side.cross(forward)
        return Mat4((side.x, true_up.x, -forward.x, 0,
                     side.y, true_up.y, -forward.y, 0,
                     side.z, true_up.z, -forward.z, 0,
                     -side.dot(eye), -true_up.dot(eye), forward.dot(eye), 1))

    def __matmul__(self, other):
        a, b = self.m, other.m
        return Mat4(tuple(
            a[r] * b[c4] + a[4 + r] * b[c4 + 1] + a[8 + r] * b[c4 + 2] + a[12 + r] * b[c4 + 3]
            for c4 in (0, 4, 8, 12) for r in range(4)))

    def transform4(self, x, y, z, w):
        m = self.m
        return (m[0] * x + m[4] * y + m[8] * z + m[12] * w,
                m[1] * x + m[5] * y + m[9] * z + m[13] * w,
                m[2] * x + m[6] * y + m[10] * z + m[14] * w,
                m[3] * x + m[7] * y + m[11] * z + m[15] * w)

    def transform_point(self, p):
        """Affine transform of a point (w = 1); no perspective divide."""
        x, y, z, _ = self.transform4(p.x, p.y, p.z, 1.0)
        return Vec3(x, y, z)

    def transform_vector(self, v):
        x, y, z, _ = self.transform4(v.x, v.y, v.z, 0.0)
        return Vec3(x, y, z)

    def translation_part(self):
        return Vec3(self.m[12], self.m[13], self.m[14])

    def transposed(self):
        return Mat4(tuple(self.m[r * 4 + c] for c in range(4) for r in range(4)))

    def inverse(self):
        """General inverse; raises ValueError for a singular matrix."""
        m = self.m
        inv = [0.0] * 16
        inv[0] = (m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15]
                  + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10])
        inv[4] = (-m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15]
                  - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10])
        inv[8] = (m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15]
                  + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9])
        inv[12] = (-m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14]
                   - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9])
        inv[1] = (-m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15]
                  - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10])
        inv[5] = (m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15]
                  + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10])
        inv[9] = (-m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15]
                  - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9])
        inv[13] = (m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14]
                   + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9])
        inv[2] = (m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15]
                  + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6])
        inv[6] = (-m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15]
                  - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6])
        inv[10] = (m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15]
                   + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5])
        inv[14] = (-m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14]
                   - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5])
        inv[3] = (-m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11]
                  - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6])
        inv[7] = (m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11]
                  + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6])
        inv[11] = (-m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11]
                   - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5])
        inv[15] = (m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10]
                   + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5])
        determinant = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12]
        if abs(determinant) < 1e-12:
            raise ValueError("The matrix is singular.")
        return Mat4(tuple(value / determinant for value in inv))

    def is_close(self, other, tolerance=1e-6):
        return all(abs(a - b) <= tolerance for a, b in zip(self.m, other.m))


class Ray(NamedTuple):
    origin: Vec3
    direction: Vec3  # Unit length.

    def point_at(self, t):
        return self.origin + self.direction * t

    def intersect_plane(self, point, normal):
        """Distance along the ray to the plane, or None if parallel or behind."""
        denominator = normal.dot(self.direction)
        if abs(denominator) < EPSILON:
            return None
        t = normal.dot(point - self.origin) / denominator
        return t if t >= 0.0 else None

    def intersect_sphere(self, center, radius):
        """Distance to the first hit in front of the origin, or None."""
        offset = self.origin - center
        b = offset.dot(self.direction)
        c = offset.dot(offset) - radius * radius
        discriminant = b * b - c
        if discriminant < 0.0:
            return None
        root = math.sqrt(discriminant)
        t = -b - root
        if t < 0.0:
            t = -b + root
        return t if t >= 0.0 else None


def project(point, view_projection, width, height):
    x, y, z, w = view_projection.transform4(point.x, point.y, point.z, 1.0)
    if w <= EPSILON:
        return None
    return ((x / w + 1.0) * 0.5 * width, (1.0 - y / w) * 0.5 * height, z / w)


def unproject(x, y, ndc_depth, inverse_view_projection, width, height):
    """Pixel position and NDC depth back to a world point."""
    nx, ny = 2.0 * x / width - 1.0, 1.0 - 2.0 * y / height
    wx, wy, wz, w = inverse_view_projection.transform4(nx, ny, ndc_depth, 1.0)
    if abs(w) < EPSILON:
        raise ValueError("The point cannot be unprojected.")
    return Vec3(wx / w, wy / w, wz / w)


def ray_from_screen(x, y, inverse_view_projection, width, height):
    near = unproject(x, y, -1.0, inverse_view_projection, width, height)
    far = unproject(x, y, 1.0, inverse_view_projection, width, height)
    return Ray(near, (far - near).normalized())
