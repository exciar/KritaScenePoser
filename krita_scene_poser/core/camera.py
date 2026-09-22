"""Orbit camera. Yaw 0 looks at the figure's front from +Z; positive pitch looks down."""

import math

from .math3d import Mat4, Vec3, Y_AXIS, ray_from_screen

PITCH_LIMIT = math.radians(89.0)
MIN_DISTANCE, MAX_DISTANCE = 0.05, 100.0


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class OrbitCamera:
    def __init__(self, target=Vec3(0.0, 0.9, 0.0), yaw=0.0, pitch=0.12, distance=3.0,
                 fov_y=math.radians(35.0), orthographic=False):
        self.target, self.yaw, self.pitch = target, yaw, pitch
        self.distance, self.fov_y, self.orthographic = distance, fov_y, orthographic

    def copy(self):
        return OrbitCamera(self.target, self.yaw, self.pitch, self.distance, self.fov_y,
                           self.orthographic)

    def to_dict(self):
        """Plain values for a scene file; angles are radians, like the attributes."""
        return {"target": [self.target.x, self.target.y, self.target.z],
                "yaw": self.yaw, "pitch": self.pitch, "distance": self.distance,
                "fov_y": self.fov_y, "orthographic": bool(self.orthographic)}

    @classmethod
    def from_dict(cls, data):
        """A camera from stored values; anything missing or damaged uses a default."""
        camera = cls()
        if not isinstance(data, dict):
            return camera
        target = data.get("target")
        if (isinstance(target, (list, tuple)) and len(target) == 3
                and all(_finite(value) for value in target)):
            camera.target = Vec3(*(float(value) for value in target))
        for name, low, high in (("yaw", -math.tau * 4, math.tau * 4),
                                ("pitch", -PITCH_LIMIT, PITCH_LIMIT),
                                ("distance", MIN_DISTANCE, MAX_DISTANCE),
                                ("fov_y", math.radians(1.0), math.radians(170.0))):
            value = data.get(name)
            if _finite(value):
                setattr(camera, name, min(high, max(low, float(value))))
        camera.orthographic = bool(data.get("orthographic", False))
        return camera

    def eye(self):
        horizontal = math.cos(self.pitch)
        return self.target + Vec3(math.sin(self.yaw) * horizontal, math.sin(self.pitch),
                                  math.cos(self.yaw) * horizontal) * self.distance

    def forward(self):
        return (self.target - self.eye()).normalized()

    def right(self):
        return self.forward().cross(Y_AXIS).normalized()

    def up(self):
        return self.right().cross(self.forward())

    def view(self):
        return Mat4.look_at(self.eye(), self.target, Y_AXIS)

    def projection(self, aspect):
        half_height = self.distance * math.tan(self.fov_y / 2.0)
        if self.orthographic:
            # Clip generously on both sides of the eye; ortho has no perspective.
            depth = self.distance + 50.0
            return Mat4.orthographic(-half_height * aspect, half_height * aspect,
                                     -half_height, half_height, -depth, depth)
        near = max(self.distance * 0.01, 0.001)
        return Mat4.perspective(self.fov_y, aspect, near, self.distance * 100.0 + 10.0)

    def view_projection(self, aspect):
        return self.projection(aspect) @ self.view()

    def world_per_pixel(self, point, height):
        depth = self.distance if self.orthographic else max(
            (point - self.eye()).dot(self.forward()), 1e-6)
        return 2.0 * depth * math.tan(self.fov_y / 2.0) / height

    def ray(self, x, y, width, height):
        inverse = self.view_projection(width / height).inverse()
        return ray_from_screen(x, y, inverse, width, height)

    def orbit(self, dx, dy, sensitivity=0.008):
        self.yaw -= dx * sensitivity
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + dy * sensitivity))

    def pan(self, dx, dy, height):
        """Move the view so the target plane follows the cursor."""
        step = self.world_per_pixel(self.target, height)
        self.target = self.target + (self.right() * -dx + self.up() * dy) * step

    def zoom(self, steps):
        self.distance = max(MIN_DISTANCE, min(MAX_DISTANCE, self.distance * 0.85 ** steps))

    def frame(self, points, margin=1.15):
        """Center on ``points`` and fit their bounding sphere in the view."""
        points = list(points)
        low = Vec3(*(min(p[i] for p in points) for i in range(3)))
        high = Vec3(*(max(p[i] for p in points) for i in range(3)))
        center = (low + high) * 0.5
        radius = max(max((p - center).length() for p in points), 0.01)
        self.target = center
        self.distance = max(MIN_DISTANCE, min(
            MAX_DISTANCE, radius * margin / math.sin(self.fov_y / 2.0)))
