"""Body shape: per-joint length and girth factors applied to the rest figure."""

from dataclasses import asdict, dataclass, fields, replace
import json
import math

from .math3d import X_AXIS, Y_AXIS, Z_AXIS

# Every control is a multiplier around 1.0. The bounds keep a figure that can
# still be posed: a limb never reaches zero length, and IK never divides by it.
LIMITS = {
    "height": (0.7, 1.35),
    "head_size": (0.7, 1.4),
    "neck_length": (0.6, 1.6),
    "torso_length": (0.8, 1.25),
    "arm_length": (0.75, 1.3),
    "leg_length": (0.75, 1.3),
    "hand_size": (0.7, 1.4),
    "foot_size": (0.7, 1.4),
    "shoulder_width": (0.6, 1.6),
    "chest": (0.7, 1.5),
    "waist": (0.6, 1.6),
    "hips": (0.7, 1.5),
    "arm_thickness": (0.7, 1.5),
    "leg_thickness": (0.7, 1.5),
    "build": (0.7, 1.4),
}

# Which control drives each joint. A joint not named here keeps its shape and
# still follows its parent, so an unknown rig degrades to the default body.
LENGTHS = {
    "neck": "neck_length",
    "waist": "torso_length", "torso": "torso_length", "chest": "torso_length",
    "upper_arm": "arm_length", "forearm": "arm_length",
    "thigh": "leg_length", "shin": "leg_length",
    "shoulder": "shoulder_width",
}
GIRTHS = {
    "hips": "hips", "waist": "waist", "torso": "waist", "chest": "chest",
    "upper_arm": "arm_thickness", "forearm": "arm_thickness",
    "thigh": "leg_thickness", "shin": "leg_thickness",
}
# Controls that resize a part in every direction at once.
UNIFORM = {
    "head": "head_size",
    "hand": "hand_size", "thumb": "hand_size", "index": "hand_size",
    "middle": "hand_size", "ring": "hand_size", "pinky": "hand_size",
    "foot": "foot_size", "toe": "foot_size",
}
BUILD_JOINTS = frozenset(GIRTHS)  # The general build control thickens these.
# The order and wording the docker shows, in two groups.
CONTROLS = (
    ("Proportions", (
        ("height", "Height"),
        ("head_size", "Head size"),
        ("neck_length", "Neck length"),
        ("torso_length", "Torso length"),
        ("arm_length", "Arm length"),
        ("leg_length", "Leg length"),
        ("hand_size", "Hand size"),
        ("foot_size", "Foot size"),
        ("shoulder_width", "Shoulder width"),
    )),
    ("Build", (
        ("chest", "Chest"),
        ("waist", "Waist"),
        ("hips", "Hips"),
        ("arm_thickness", "Arm thickness"),
        ("leg_thickness", "Leg thickness"),
        ("build", "Overall build"),
    )),
)
SIDES = (".L", ".R", "_L", "_R", ".l", ".r", "_l", "_r")


@dataclass(frozen=True)
class BodyShape:
    height: float = 1.0
    head_size: float = 1.0
    neck_length: float = 1.0
    torso_length: float = 1.0
    arm_length: float = 1.0
    leg_length: float = 1.0
    hand_size: float = 1.0
    foot_size: float = 1.0
    shoulder_width: float = 1.0
    chest: float = 1.0
    waist: float = 1.0
    hips: float = 1.0
    arm_thickness: float = 1.0
    leg_thickness: float = 1.0
    build: float = 1.0

    def validated(self):
        values = {}
        for name, (low, high) in LIMITS.items():
            value = getattr(self, name)
            ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value))
            values[name] = min(high, max(low, float(value))) if ok else 1.0
        return replace(self, **values)

    def is_default(self):
        return self.validated() == BodyShape()

    def to_json(self):
        return json.dumps(asdict(self.validated()), sort_keys=True)

    @classmethod
    def from_json(cls, text):
        """A shape from stored JSON; anything unreadable falls back to default."""
        try:
            data = json.loads(text) if text else {}
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        known = {field.name for field in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known}).validated()


def base_name(joint_name):
    """The joint's name without a side suffix or a finger segment number."""
    name = joint_name
    for suffix in SIDES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name.split(".")[0]


def joint_factors(shape, joint_name):
    """``(length, girth)`` multipliers for one joint, height included."""
    shape = shape.validated()
    name = base_name(joint_name)
    length = girth = shape.height
    if name in UNIFORM:
        factor = getattr(shape, UNIFORM[name])
        return length * factor, girth * factor
    if name in LENGTHS:
        length *= getattr(shape, LENGTHS[name])
    if name in GIRTHS:
        girth *= getattr(shape, GIRTHS[name])
    if name in BUILD_JOINTS:
        girth *= shape.build
    return length, girth


def joint_matrices(skeleton, shape):
    """Per-joint rest transforms for ``shape``.

    Returns ``(matrices, positions, normal_matrices)``. A matrix is 12 floats:
    three rows of an affine transform in world rest space, mapping a rest point
    to its shaped place. ``positions`` holds each joint's new rest position, and
    the normal matrices are the inverse transposes, nine floats each.
    """
    rest = skeleton.rest_transforms
    linear, inverse, positions = [], [], []
    for index, joint in enumerate(skeleton.joints):
        length, girth = joint_factors(shape, joint.name)
        rotation = rest[index].rotation
        # R * diag(girth, length, girth) * R^-1: scale along the bone and across it.
        matrix = _scale_in_frame(rotation, girth, length)
        linear.append(matrix)
        inverse.append(_scale_in_frame(rotation, 1.0 / girth, 1.0 / length))
        parent = joint.parent
        if parent < 0:
            # The root is scaled about the ground, so a taller figure still stands on it.
            base = rest[index].position
            positions.append((base.x * shape.height, base.y * shape.height,
                              base.z * shape.height))
        else:
            offset = rest[index].position - rest[parent].position
            moved = _apply3(linear[parent], (offset.x, offset.y, offset.z))
            origin = positions[parent]
            positions.append((origin[0] + moved[0], origin[1] + moved[1], origin[2] + moved[2]))
    matrices = []
    for index in range(len(skeleton.joints)):
        m, p, q = linear[index], rest[index].position, positions[index]
        moved = _apply3(m, (p.x, p.y, p.z))
        matrices.append((m[0], m[1], m[2], q[0] - moved[0],
                         m[3], m[4], m[5], q[1] - moved[1],
                         m[6], m[7], m[8], q[2] - moved[2]))
    return tuple(matrices), tuple(positions), tuple(inverse)


def deform(positions, normals, joints, weights, matrices, normal_matrices):
    """Blend the shape transforms with the skin weights.

    Takes and returns flat sequences, so this runs without building objects
    for every vertex; a figure has tens of thousands of them.
    """
    count = len(positions) // 3
    new_positions = [0.0] * len(positions)
    new_normals = [0.0] * len(normals)
    for vertex in range(count):
        base = vertex * 4
        # Blend the matrices this vertex is bound to, weighted as the skin is.
        a = b = c = d = e = f = g = h = i = j = k = m = 0.0
        na = nb = nc = nd = ne = nf = ng = nh = ni = 0.0
        for slot in range(4):
            weight = weights[base + slot]
            if not weight:
                continue
            w = weight / 255.0
            t = matrices[joints[base + slot]]
            n = normal_matrices[joints[base + slot]]
            a += w * t[0]; b += w * t[1]; c += w * t[2]; d += w * t[3]
            e += w * t[4]; f += w * t[5]; g += w * t[6]; h += w * t[7]
            i += w * t[8]; j += w * t[9]; k += w * t[10]; m += w * t[11]
            na += w * n[0]; nb += w * n[1]; nc += w * n[2]
            nd += w * n[3]; ne += w * n[4]; nf += w * n[5]
            ng += w * n[6]; nh += w * n[7]; ni += w * n[8]
        p = vertex * 3
        x, y, z = positions[p], positions[p + 1], positions[p + 2]
        new_positions[p] = a * x + b * y + c * z + d
        new_positions[p + 1] = e * x + f * y + g * z + h
        new_positions[p + 2] = i * x + j * y + k * z + m
        # The inverse transpose keeps normals square to the new surface.
        nx, ny, nz = normals[p], normals[p + 1], normals[p + 2]
        ox = na * nx + nd * ny + ng * nz
        oy = nb * nx + ne * ny + nh * nz
        oz = nc * nx + nf * ny + ni * nz
        scale = math.sqrt(ox * ox + oy * oy + oz * oz)
        if scale < 1e-12:
            ox, oy, oz, scale = nx, ny, nz, 1.0
        new_normals[p] = ox / scale
        new_normals[p + 1] = oy / scale
        new_normals[p + 2] = oz / scale
    return new_positions, new_normals


def ground_offset(positions):
    """How far to lift the figure so its lowest point rests on y = 0."""
    lowest = min(positions[1::3]) if positions else 0.0
    return -lowest


def _scale_in_frame(rotation, across, along):
    """``R * diag(across, along, across) * R^-1`` as nine floats, row major."""
    rx = rotation.rotate(X_AXIS)
    ry = rotation.rotate(Y_AXIS)
    rz = rotation.rotate(Z_AXIS)
    columns = ((rx.x, rx.y, rx.z), (ry.x, ry.y, ry.z), (rz.x, rz.y, rz.z))
    factors = (across, along, across)
    result = [0.0] * 9
    for row in range(3):
        for column in range(3):
            total = 0.0
            for axis in range(3):
                total += columns[axis][row] * factors[axis] * columns[axis][column]
            result[row * 3 + column] = total
    return tuple(result)


def _apply3(matrix, point):
    x, y, z = point
    return (matrix[0] * x + matrix[1] * y + matrix[2] * z,
            matrix[3] * x + matrix[4] * y + matrix[5] * z,
            matrix[6] * x + matrix[7] * y + matrix[8] * z)
