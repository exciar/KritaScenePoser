"""Output size and quality for created layers (pure Python).

A layer is normally the document's size, but it can also be a size the user
picks, anchored in the document. Either way the figure may be rendered larger
and scaled down, which smooths edges and hairlines that a single sample
leaves ragged.

Sizes are in document pixels, and the same caps as the pixel transfer apply to
the *rendered* image, so a supersampled render is refused before anything is
allocated rather than failing on the GPU.
"""

from dataclasses import asdict, dataclass, fields, replace
import json
import math

MAX_DIMENSION = 4096
MAX_PIXELS = 16_777_216
MODES = ("document", "custom")
ANCHORS = ("center", "topleft")
QUALITIES = (1, 2, 4)
LIMITS = {"width": (1, MAX_DIMENSION), "height": (1, MAX_DIMENSION)}


class OutputSizeError(ValueError):
    """The requested output cannot be rendered within KSP's limits."""


@dataclass(frozen=True)
class OutputPlan:
    """What one export will do, in pixels."""
    width: int  # Layer size.
    height: int
    render_width: int  # What the GPU draws, before scaling down.
    render_height: int
    origin: tuple  # Where the layer's pixels start in the document.
    supersample: int

    @property
    def aspect(self):
        return self.width / self.height

    @property
    def scaled(self):
        return self.supersample != 1

    def describe(self):
        text = "{} × {}".format(self.width, self.height)
        return text + (" at {}× quality".format(self.supersample) if self.scaled else "")


@dataclass(frozen=True)
class OutputSettings:
    mode: str = "document"  # "document" follows the canvas; "custom" uses width/height.
    width: int = 1024
    height: int = 1536
    anchor: str = "center"  # Where a custom size sits in the document.
    supersample: int = 1  # Render this many times larger, then scale down.

    def validated(self):
        values = {
            "mode": self.mode if self.mode in MODES else "document",
            "anchor": self.anchor if self.anchor in ANCHORS else "center",
            "supersample": self.supersample if self.supersample in QUALITIES else 1,
        }
        for name in ("width", "height"):
            low, high = LIMITS[name]
            value = getattr(self, name)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and \
                math.isfinite(value)
            values[name] = min(high, max(low, int(value))) if ok else getattr(OutputSettings, name)
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

    def resolve(self, document_width, document_height):
        """The plan for a document of this size, or raise ``OutputSizeError``."""
        settings = self.validated()
        if settings.mode == "document":
            width, height = int(document_width), int(document_height)
        else:
            width, height = settings.width, settings.height
        if width <= 0 or height <= 0:
            raise OutputSizeError("The output size must be at least one pixel.")
        render_width = width * settings.supersample
        render_height = height * settings.supersample
        if (render_width > MAX_DIMENSION or render_height > MAX_DIMENSION
                or render_width * render_height > MAX_PIXELS):
            raise OutputSizeError(
                "{} × {} at {}× quality renders {} × {} pixels, over KSP's "
                "limit of {} on a side and {} in total. Lower the quality or the size."
                .format(width, height, settings.supersample, render_width, render_height,
                        MAX_DIMENSION, MAX_PIXELS))
        if settings.anchor == "topleft" or settings.mode == "document":
            origin = (0, 0)
        else:  # Centered, which may hang outside the canvas; Krita keeps those pixels.
            origin = ((int(document_width) - width) // 2, (int(document_height) - height) // 2)
        return OutputPlan(width, height, render_width, render_height, origin,
                          settings.supersample)
