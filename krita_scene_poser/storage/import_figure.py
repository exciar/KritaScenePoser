"""Turn an imported model into a KSP figure (pure Python).

Readers for each file format produce a :class:`SourceFigure`: bones with rest
matrices, and meshes with positions, normals, and per-bone weights. This
module maps those bones onto KSP's own joints, converts axes and units, and
writes the same ``ksp-rig`` and ``KSPMESH`` data the shipped figures use. A
figure imported this way is an ordinary figure everywhere else in KSP.

Imported files are treated as hostile: every count is capped, every index is
checked, and every failure raises :class:`FigureImportError` with a sentence a
user can act on.
"""

from array import array
from dataclasses import dataclass, field
import json
import math
import re

from ..core.math3d import Mat4, Quat, Vec3
from .mesh_io import MeshData, validate
from .rig_io import RigData, RigJoint, build_skeleton

MAX_BONES = 4096
MAX_VERTICES = 150_000
MAX_TRIANGLES = 300_000
MAX_PARTS = 254  # The line-art buffers keep a part id in 8 bits.
MAX_JOINTS = 64  # The skinning shader's budget.
HUMAN_HEIGHT = 1.75  # Used only when a model has no believable size of its own.
MAP_SUFFIX = ".ksp-map.json"


class FigureImportError(ValueError):
    """A model cannot be turned into a KSP figure."""


@dataclass
class SourceBone:
    name: str
    parent: object = None  # Parent bone name, or None for a root.
    matrix: object = None  # Mat4: the bone's rest transform in world space.


@dataclass
class SourceMesh:
    name: str
    positions: list = field(default_factory=list)  # x, y, z per vertex.
    normals: list = field(default_factory=list)  # Empty means "work them out".
    weights: list = field(default_factory=list)  # Per vertex: {bone name: weight}.
    indices: list = field(default_factory=list)  # Triangles.


@dataclass
class SourceFigure:
    bones: list = field(default_factory=list)
    meshes: list = field(default_factory=list)
    unit_scale: float = 1.0  # Multiply positions by this to get meters.
    up: str = "Y"  # "Y" like glTF, or "Z" like Blender.
    source: dict = field(default_factory=dict)  # Provenance for the rig file.


# KSP's joints, and the names each convention uses for them. ------------------

def _limb_names(pattern, left, right):
    return {"{}.L".format(pattern): left, "{}.R".format(pattern): right}


def _sided(table):
    """Expand a table written for the left side onto both sides."""
    result = {}
    for ksp, source in table.items():
        result[ksp + ".L"] = source.format(side="L", Side="Left", side_lower="left")
        result[ksp + ".R"] = source.format(side="R", Side="Right", side_lower="right")
    return result


FINGERS = (("thumb", "thumb", "Thumb", "Thumb"), ("index", "f_index", "Index", "Index"),
           ("middle", "f_middle", "Middle", "Middle"), ("ring", "f_ring", "Ring", "Ring"),
           ("pinky", "f_pinky", "Pinky", "Little"))


def _rigify(prefix=""):
    table = {"hips": prefix + "spine", "waist": prefix + "spine.001",
             "torso": prefix + "spine.002", "chest": prefix + "spine.003",
             "neck": prefix + "spine.004", "head": prefix + "spine.006"}
    sided = {"shoulder": prefix + "shoulder.{side}", "upper_arm": prefix + "upper_arm.{side}",
             "forearm": prefix + "forearm.{side}", "hand": prefix + "hand.{side}",
             "thigh": prefix + "thigh.{side}", "shin": prefix + "shin.{side}",
             "foot": prefix + "foot.{side}", "toe": prefix + "toe.{side}"}
    for ksp, rigify_name, _, _ in FINGERS:
        for segment in (1, 2, 3):
            sided["{}.0{}".format(ksp, segment)] = "{}{}.0{}.{{side}}".format(
                prefix, rigify_name, segment)
    table.update(_sided(sided))
    return table


def _mixamo():
    table = {"hips": "Hips", "waist": "Spine", "torso": "Spine1", "chest": "Spine2",
             "neck": "Neck", "head": "Head"}
    sided = {"shoulder": "{Side}Shoulder", "upper_arm": "{Side}Arm", "forearm": "{Side}ForeArm",
             "hand": "{Side}Hand", "thigh": "{Side}UpLeg", "shin": "{Side}Leg",
             "foot": "{Side}Foot", "toe": "{Side}ToeBase"}
    for ksp, _, mixamo_name, _ in FINGERS:
        for segment in (1, 2, 3):
            sided["{}.0{}".format(ksp, segment)] = "{{Side}}Hand{}{}".format(mixamo_name, segment)
    table.update(_sided(sided))
    return table


def _vrm():
    table = {"hips": "hips", "waist": "spine", "torso": "chest", "chest": "upperChest",
             "neck": "neck", "head": "head"}
    sided = {"shoulder": "{side_lower}Shoulder", "upper_arm": "{side_lower}UpperArm",
             "forearm": "{side_lower}LowerArm", "hand": "{side_lower}Hand",
             "thigh": "{side_lower}UpperLeg", "shin": "{side_lower}LowerLeg",
             "foot": "{side_lower}Foot", "toe": "{side_lower}Toes"}
    for ksp, _, _, vrm_name in FINGERS:
        parts = ("Proximal", "Intermediate", "Distal")
        for segment, part in enumerate(parts, start=1):
            sided["{}.0{}".format(ksp, segment)] = "{{side_lower}}{}{}".format(vrm_name, part)
    table.update(_sided(sided))
    return table


SCHEMES = {
    "Rigify (DEF- bones)": _rigify("DEF-"),
    "Rigify or Blender metarig": _rigify(),
    "Mixamo": _mixamo(),
    "VRM humanoid": _vrm(),
}
KSP_JOINTS = tuple(_rigify().keys())  # The 52 joints, in parents-first order.
PARENTS = {}


# Each joint's parent: the centre chain, then a limb chain on each side.
CENTRE = (("hips", None), ("waist", "hips"), ("torso", "waist"), ("chest", "torso"),
          ("neck", "chest"), ("head", "neck"))
LIMBS = (("shoulder", "chest"), ("upper_arm", "shoulder"), ("forearm", "upper_arm"),
         ("hand", "forearm"), ("thigh", "hips"), ("shin", "thigh"), ("foot", "shin"),
         ("toe", "foot"))
CENTRE_JOINTS = frozenset(name for name, _ in CENTRE)


def _build_parents():
    PARENTS.update(CENTRE)
    for side in ("L", "R"):
        def sided(name):
            return name if name in CENTRE_JOINTS else "{}.{}".format(name, side)
        for name, parent in LIMBS:
            PARENTS[sided(name)] = sided(parent)
        for finger, _, _, _ in FINGERS:
            parent = sided("hand")
            for segment in (1, 2, 3):
                name = "{}.0{}.{}".format(finger, segment, side)
                PARENTS[name] = parent
                parent = name


_build_parents()


def normalized_name(name):
    """A bone name reduced for comparison: no namespace, case, or separators."""
    text = str(name)
    if ":" in text:
        text = text.split(":")[-1]  # mixamorig:Hips -> Hips
    return re.sub(r"[^a-z0-9]", "", text.lower())


def map_bones(bone_names, custom=None):
    """Match source bones to KSP joints.

    Returns ``(mapping, scheme, missing)``: KSP joint name to source bone name,
    the naming scheme that fitted best, and the joints nothing matched.
    """
    available = {}
    for name in bone_names:
        available.setdefault(normalized_name(name), name)
    best, best_scheme = {}, "none"
    for scheme, table in SCHEMES.items():
        mapping = {}
        for ksp, source in table.items():
            match = available.get(normalized_name(source))
            if match is not None:
                mapping[ksp] = match
        if len(mapping) > len(best):
            best, best_scheme = mapping, scheme
    if custom:
        for ksp, source in custom.items():
            if ksp not in PARENTS:
                continue
            for candidate in (source if isinstance(source, (list, tuple)) else [source]):
                match = available.get(normalized_name(candidate))
                if match is not None:
                    best[ksp] = match
                    break
        best_scheme = "your own map" if best_scheme == "none" else best_scheme + " plus your map"
    missing = tuple(name for name in KSP_JOINTS if name not in best)
    return best, best_scheme, missing


def read_map_file(text):
    """A ``.ksp-map.json`` file: KSP joint names to your bone names."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise FigureImportError("The bone map file is not valid JSON.") from error
    if not isinstance(data, dict):
        raise FigureImportError("The bone map file must be an object of joint names.")
    result = {}
    for key, value in data.items():
        if isinstance(value, str) or (isinstance(value, (list, tuple))
                                      and all(isinstance(item, str) for item in value)):
            result[str(key)] = value
    if not result:
        raise FigureImportError("The bone map file has no usable entries.")
    return result


# Conversion ------------------------------------------------------------------


def convert(source, figure_id, display_name, custom_map=None):
    """Build ``(RigData, MeshData, report)`` from a source figure."""
    _check_source(source)
    bones = {bone.name: bone for bone in source.bones}
    mapping, scheme, missing = map_bones(bones.keys(), custom_map)
    if "hips" not in mapping:
        raise FigureImportError(
            "KSP could not find the hips bone. Add a {} file that names your bones, as "
            "docs/custom-figures.md explains.".format(MAP_SUFFIX))
    joints = [name for name in KSP_JOINTS if name in mapping]
    if len(joints) > MAX_JOINTS:
        raise FigureImportError("This rig maps to {} joints; KSP allows {}.".format(
            len(joints), MAX_JOINTS))
    order = {name: index for index, name in enumerate(joints)}

    # Every source bone follows the nearest mapped bone above it, so a twist or
    # a helper bone moves with the joint it belongs to instead of being lost.
    owner = {}
    for bone in source.bones:
        owner[bone.name] = _nearest_mapped(bone, bones, mapping)

    to_ksp = _axis_rotation(source.up)
    scale = _unit_scale(source)
    places, rotations = {}, {}
    for name in joints:
        matrix = bones[mapping[name]].matrix
        if matrix is None:
            raise FigureImportError("Bone {!r} has no rest position.".format(mapping[name]))
        try:
            places[name] = to_ksp.rotate(_translation(matrix)) * scale
            rotations[name] = (to_ksp * _rotation(matrix)).normalized()
        except (ValueError, ZeroDivisionError, OverflowError) as error:
            # A flattened or damaged bone matrix cannot describe a rest pose.
            raise FigureImportError(
                "Bone {!r} has a rest transform KSP cannot use: {}.".format(
                    mapping[name], error)) from error
        if not all(math.isfinite(value) for value in places[name]):
            raise FigureImportError("Bone {!r} has a position that is not a number.".format(
                mapping[name]))

    positions, normals, weights, parts, indices, part_names = _gather(
        source, owner, order, to_ksp, scale)
    # A model authored in its own units is brought to human size, once its real
    # height is known. A believable height is left exactly as the artist made it.
    extra = guess_scale(max(positions[1::3]) - min(positions[1::3]) if positions else 0.0)
    if extra != 1.0:
        positions = [value * extra for value in positions]
        places = {name: place * extra for name, place in places.items()}
    lift = -min(positions[1::3]) if positions else 0.0
    turn = _facing(places, positions)
    for index in range(0, len(positions), 3):
        x, y, z = positions[index], positions[index + 1], positions[index + 2]
        positions[index], positions[index + 2] = (x, z) if turn == 1 else (-x, -z)
        positions[index + 1] = y + lift
    if turn == -1:
        for index in range(0, len(normals), 3):
            normals[index], normals[index + 2] = -normals[index], -normals[index + 2]

    rig_joints = []
    for name in joints:
        place = places[name]
        rotation = rotations[name]
        if turn == -1:
            place = Vec3(-place.x, place.y, -place.z)
            rotation = (Quat.from_axis_angle(Vec3(0.0, 1.0, 0.0), math.pi) * rotation).normalized()
        place = Vec3(place.x, place.y + lift, place.z)
        rig_joints.append(RigJoint(
            name=name, parent=_parent_of(name, order),
            position=place, rotation=rotation,
            tail=place + rotation.rotate(Vec3(0.0, 1.0, 0.0)) * _bone_length(name, places, turn)))

    mesh = MeshData(positions=array("f", positions), normals=array("f", normals),
                    joints=bytes(weights[0]), weights=bytes(weights[1]), parts=bytes(parts),
                    indices=array("I" if len(positions) // 3 > 0xFFFF else "H", indices),
                    part_names=tuple(part_names), joint_count=len(joints))
    validate(mesh)
    rig = RigData(figure=figure_id, display_name=display_name, joints=tuple(rig_joints),
                  source=dict(source.source), skeleton=build_skeleton(rig_joints))
    report = {
        "scheme": scheme,
        "joints": len(joints),
        "missing": missing,
        "vertices": mesh.vertex_count,
        "triangles": mesh.triangle_count,
        "parts": len(part_names),
        "height": round(max(positions[1::3]) if positions else 0.0, 3),
        "unweighted": weights[2],
        "facing_flipped": turn == -1,
        "rescaled": round(extra, 4),
    }
    return rig, mesh, report


def describe(report, display_name):
    """One sentence about an import, for the status line."""
    text = "Imported {} with {} joints from {} names: {} vertices, {} parts.".format(
        display_name, report["joints"], report["scheme"], report["vertices"], report["parts"])
    if report["missing"]:
        text += " {} KSP joints had no bone, including {}.".format(
            len(report["missing"]), ", ".join(report["missing"][:3]))
    if report["unweighted"]:
        text += " {} vertices had no weights and follow the hips.".format(report["unweighted"])
    if report.get("rescaled", 1.0) != 1.0:
        text += " It was scaled by {:.3g} to a human height of {} m.".format(
            report["rescaled"], report["height"])
    if report.get("facing_flipped"):
        text += " It was turned to face the camera."
    return text


def _check_source(source):
    if not source.bones:
        raise FigureImportError("The file has no skeleton. KSP needs a rigged figure.")
    if len(source.bones) > MAX_BONES:
        raise FigureImportError("The file has {} bones; KSP reads up to {}.".format(
            len(source.bones), MAX_BONES))
    if not source.meshes:
        raise FigureImportError("The file has no mesh attached to its skeleton.")
    vertices = sum(len(mesh.positions) // 3 for mesh in source.meshes)
    triangles = sum(len(mesh.indices) // 3 for mesh in source.meshes)
    if vertices == 0 or triangles == 0:
        raise FigureImportError("The file's meshes are empty.")
    if vertices > MAX_VERTICES:
        raise FigureImportError(
            "This model has {:,} vertices; KSP reads up to {:,}. Decimate it first."
            .format(vertices, MAX_VERTICES))
    if triangles > MAX_TRIANGLES:
        raise FigureImportError(
            "This model has {:,} triangles; KSP reads up to {:,}.".format(
                triangles, MAX_TRIANGLES))
    if len(source.meshes) > MAX_PARTS:
        raise FigureImportError("This model has {} parts; KSP reads up to {}.".format(
            len(source.meshes), MAX_PARTS))


def _nearest_mapped(bone, bones, mapping):
    """The mapped KSP joint this source bone belongs to."""
    targets = {source: ksp for ksp, source in mapping.items()}
    seen = set()
    current = bone
    while current is not None and current.name not in seen:
        seen.add(current.name)
        if current.name in targets:
            return targets[current.name]
        current = bones.get(current.parent)
    return "hips"  # Anything unattached rides the root rather than vanishing.


def _axis_rotation(up):
    """Rotate the source's up axis onto KSP's +Y."""
    if str(up).upper().startswith("Z"):
        return Quat.from_axis_angle(Vec3(1.0, 0.0, 0.0), -math.pi / 2)
    return Quat(1.0, 0.0, 0.0, 0.0)


def _unit_scale(source):
    scale = source.unit_scale if source.unit_scale else 1.0
    if not (math.isfinite(scale) and scale > 0.0):
        raise FigureImportError("The file's unit scale is not usable.")
    return scale


def _translation(matrix):
    return Vec3(matrix.m[12], matrix.m[13], matrix.m[14])


def _rotation(matrix):
    return Quat.from_matrix(matrix).normalized()


def _parent_of(name, order):
    """The nearest ancestor that is also a mapped joint."""
    parent = PARENTS.get(name)
    while parent is not None and parent not in order:
        parent = PARENTS.get(parent)
    return parent


def _bone_length(name, places, turn):
    """A tail one bone long, so picking capsules have something to work with."""
    children = [child for child, parent in PARENTS.items()
                if parent == name and child in places]
    if children:
        return max(0.01, min((places[child] - places[name]).length() for child in children))
    return 0.05


def _facing(places, positions):
    """1 if the figure already faces +Z, -1 if it faces the other way.

    Toes point forward, so they settle it. Without toes the figure is left
    as it came in, because guessing would be worse than a visible mistake.
    """
    for side in ("L", "R"):
        toe, foot = places.get("toe." + side), places.get("foot." + side)
        if toe is not None and foot is not None:
            forward = toe.z - foot.z
            if abs(forward) > 1e-4:
                return 1 if forward > 0 else -1
    return 1


def _gather(source, owner, order, to_ksp, scale):
    """Positions, normals, weights, parts, and triangles in KSP's layout."""
    positions, normals, parts, indices = [], [], [], []
    joint_bytes, weight_bytes = bytearray(), bytearray()
    part_names, unweighted, base = [], 0, 0
    complete_normals = True
    for mesh in source.meshes:
        count = len(mesh.positions) // 3
        if count == 0 or not mesh.indices:
            continue
        part = len(part_names)
        part_names.append(mesh.name or "part {}".format(part + 1))
        has_normals = bool(mesh.normals) and len(mesh.normals) == len(mesh.positions)
        complete_normals = complete_normals and has_normals
        for vertex in range(count):
            point = to_ksp.rotate(Vec3(*mesh.positions[vertex * 3:vertex * 3 + 3])) * scale
            positions.extend((point.x, point.y, point.z))
            if has_normals:
                direction = to_ksp.rotate(Vec3(*mesh.normals[vertex * 3:vertex * 3 + 3]))
                length = direction.length()
                direction = direction / length if length > 1e-9 else Vec3(0.0, 1.0, 0.0)
                normals.extend((direction.x, direction.y, direction.z))
            else:
                normals.extend((0.0, 1.0, 0.0))  # Replaced below from the triangles.
            influences = mesh.weights[vertex] if vertex < len(mesh.weights) else {}
            packed = {}
            for bone, weight in influences.items():
                if weight <= 0.0:
                    continue
                joint = order.get(owner.get(bone, "hips"), 0)
                packed[joint] = packed.get(joint, 0.0) + float(weight)
            if not packed:
                unweighted += 1
                packed = {0: 1.0}
            slots = quantize_weights(packed)
            for joint, weight in slots:
                joint_bytes.append(joint)
                weight_bytes.append(weight)
            parts.append(part)
        for index in mesh.indices:
            if not 0 <= index < count:
                raise FigureImportError("A triangle points at a vertex that is not there.")
            indices.append(base + index)
        base += count
    if not complete_normals:
        # One mesh without normals is enough to work them all out from the faces.
        positions, normals, joint_bytes, weight_bytes, parts = _build_normals(
            positions, indices, joint_bytes, weight_bytes, parts)
    return positions, normals, (joint_bytes, weight_bytes, unweighted), parts, indices, part_names


def quantize_weights(influences):
    """The four strongest influences as ``(joint, 0-255)``, summing to 255."""
    items = sorted(influences.items(), key=lambda item: (-item[1], item[0]))[:4]
    total = sum(weight for _, weight in items)
    if total <= 0.0:
        return [(items[0][0] if items else 0, 255), (0, 0), (0, 0), (0, 0)]
    shares = [(joint, weight / total * 255.0) for joint, weight in items]
    result = [(joint, int(math.floor(value))) for joint, value in shares]
    remainder = 255 - sum(value for _, value in result)
    # Largest remainder first, so the weights add up to exactly 255.
    for index in sorted(range(len(shares)),
                        key=lambda i: -(shares[i][1] - math.floor(shares[i][1])))[:remainder]:
        result[index] = (result[index][0], result[index][1] + 1)
    while len(result) < 4:
        result.append((result[0][0], 0))
    return result


SMOOTH_ANGLE = math.radians(40.0)  # Steeper than this counts as a hard edge.


def _build_normals(positions, indices, joint_bytes, weight_bytes, parts):
    """Vertex normals for a file that brought none, keeping hard edges.

    Faces that meet at a shallow angle share a smooth normal. Where they meet
    sharply the vertex is split, so a panel edge stays crisp instead of being
    rounded away. This is what the shipped figures' compiler does, and line art
    depends on it: creases are found from the normals.
    """
    faces = []
    incident = [[] for _ in range(len(positions) // 3)]
    for triangle in range(0, len(indices), 3):
        a, b, c = indices[triangle], indices[triangle + 1], indices[triangle + 2]
        ax, ay, az = positions[a * 3:a * 3 + 3]
        bx, by, bz = positions[b * 3:b * 3 + 3]
        cx, cy, cz = positions[c * 3:c * 3 + 3]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        # The cross product's length is twice the area, which weights the blend.
        normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        faces.append(normal)
        face = triangle // 3
        for vertex in (a, b, c):
            incident[vertex].append(face)

    limit = math.cos(SMOOTH_ANGLE)
    new_positions, new_normals = list(positions), [0.0] * len(positions)
    new_joints, new_weights, new_parts = bytearray(joint_bytes), bytearray(weight_bytes), \
        bytearray(parts)
    copies = {}  # (vertex, group) -> vertex to use
    for vertex, face_list in enumerate(incident):
        groups = []  # Each group: [direction, [faces]]
        for face in face_list:
            normal = _unit(faces[face])
            for group in groups:
                if _dot(group[0], normal) >= limit:
                    group[1].append(face)
                    group[0] = _unit(_add(group[0], normal))
                    break
            else:
                groups.append([normal, [face]])
        for number, (_, group_faces) in enumerate(groups):
            total = (0.0, 0.0, 0.0)
            for face in group_faces:
                total = _add(total, faces[face])
            direction = _unit(total)
            if number == 0:
                target = vertex
            else:  # A hard edge: this vertex needs its own copy with its own normal.
                target = len(new_positions) // 3
                new_positions.extend(positions[vertex * 3:vertex * 3 + 3])
                new_normals.extend((0.0, 0.0, 0.0))
                new_joints.extend(joint_bytes[vertex * 4:vertex * 4 + 4])
                new_weights.extend(weight_bytes[vertex * 4:vertex * 4 + 4])
                new_parts.append(parts[vertex])
            new_normals[target * 3:target * 3 + 3] = direction
            for face in group_faces:
                copies[(vertex, face)] = target
    for position in range(len(indices)):
        vertex = indices[position]
        indices[position] = copies.get((vertex, position // 3), vertex)
    return new_positions, new_normals, new_joints, new_weights, new_parts


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(vector):
    length = math.sqrt(_dot(vector, vector))
    if length < 1e-12:
        return (0.0, 1.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def guess_scale(height):
    """A scale that brings an unbelievable model back to human size."""
    if not math.isfinite(height) or height <= 0.0:
        return 1.0
    if 0.3 <= height <= 4.0:
        return 1.0  # Believable already; keep the size the model was authored at.
    return HUMAN_HEIGHT / height
