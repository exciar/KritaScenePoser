"""Line-art settings and helpers (pure Python).

Lines come from image-space edges in G-buffers. Four line types
can each be switched on or off:
- **outline:** the figure's silhouette against empty space;
- **contours:** depth jumps inside the figure, such as an arm in front of the body;
- **creases:** sharp folds, where the surface normal turns more than the crease angle;
- **seams:** boundaries between the mannequin's parts (its segment lines).

Widths are in output pixels: document pixels for layers, display pixels in
the docker viewport.
"""

from dataclasses import asdict, dataclass, fields, replace
import json
import math
import re

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
LIMITS = {
    "outline_width": (0.5, 20.0),
    "inner_width": (0.5, 20.0),
    "crease_angle": (5.0, 175.0),
    "depth_sensitivity": (0.0, 1.0),
}
TOGGLES = ("outline", "contours", "creases", "seams")
DEPTH_MARGIN = 0.3  # Meters around joints, covering heads, hands, and feet.


@dataclass(frozen=True)
class LineArtSettings:
    color: str = "#000000"
    outline_width: float = 3.0
    inner_width: float = 1.5
    crease_angle: float = 50.0  # Degrees between neighboring surface normals.
    depth_sensitivity: float = 0.5  # 0 finds only big depth jumps; 1 finds small ones.
    outline: bool = True
    contours: bool = True
    creases: bool = True
    seams: bool = True

    def validated(self):
        """A copy with numbers clamped to their limits and a valid color."""
        values = {}
        for name, (low, high) in LIMITS.items():
            value = getattr(self, name)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            values[name] = min(high, max(low, float(value))) if ok else getattr(LineArtSettings, name)
        for name in TOGGLES:
            values[name] = bool(getattr(self, name))
        color = self.color if isinstance(self.color, str) and HEX_COLOR.match(self.color) else "#000000"
        values["color"] = color.lower()
        return replace(self, **values)

    def to_json(self):
        return json.dumps(asdict(self.validated()), sort_keys=True)

    @classmethod
    def from_json(cls, text):
        """Settings from stored JSON; anything unreadable falls back to defaults."""
        try:
            data = json.loads(text) if text else {}
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        known = {field.name for field in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known}).validated()

    def rgba(self):
        value = self.validated().color
        return tuple(int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5)) + (1.0,)

    def depth_threshold(self):
        """Relative depth jump that counts as a contour: 20 % at 0, 0.5 % at 1."""
        return 0.2 * 0.025 ** self.validated().depth_sensitivity


@dataclass(frozen=True)
class LineUniforms:
    """Edge-shader inputs for one render."""
    outline_radius: float  # Render pixels.
    inner_radius: float
    depth_threshold: float
    crease_cos: float
    enabled: tuple  # outline, contours, creases, seams as 0.0 or 1.0
    rgba: tuple


def line_uniforms(settings, scale=1.0):
    """Uniforms for a render whose pixels are ``scale`` times the output's.

    A line of width w straddles its edge, so each side samples at radius w/2.
    """
    s = settings.validated()
    return LineUniforms(
        outline_radius=max(0.5, s.outline_width * scale / 2.0),
        inner_radius=max(0.5, s.inner_width * scale / 2.0),
        depth_threshold=s.depth_threshold(),
        crease_cos=math.cos(math.radians(s.crease_angle)),
        enabled=tuple(1.0 if getattr(s, name) else 0.0 for name in TOGGLES),
        rgba=s.rgba())


def depth_range(points, camera, margin=DEPTH_MARGIN):
    """``(near, range)`` of view depth covering ``points`` plus a margin.

    Depth is the distance along the camera's forward axis from the eye; the
    G-buffer stores it normalized to this range.
    """
    eye, forward = camera.eye(), camera.forward()
    depths = [(p - eye).dot(forward) for p in points] or [camera.distance]
    near, far = min(depths) - margin, max(depths) + margin
    if not camera.orthographic:
        near = max(near, 1e-3)
    return near, max(far - near, 1e-3)
