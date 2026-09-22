"""Read one armature and the meshes it deforms from a .blend file.

Modifiers are not evaluated; subdivision or mirror must be applied in Blender first.
"""

from ..core.math3d import Mat4, Vec3
from .blendfile import BlendFile, BlendFileError
from .import_figure import FigureImportError, SourceBone, SourceFigure, SourceMesh

OB_MESH, OB_ARMATURE = 1, 25
POSITION_NAMES = ("position", "co")
CORNER_NAMES = (".corner_vert", "corner_vert")
OFFSET_NAMES = ("face_offset_indices", "poly_offset_indices")


def read_blend(path_or_bytes, name="", armature=None):
    """A :class:`SourceFigure` from a ``.blend`` file."""
    try:
        blend = BlendFile(path_or_bytes)
    except BlendFileError as error:
        raise FigureImportError(str(error)) from error
    objects = {}
    for item in blend.datablocks(b"OB"):
        objects.setdefault(item.id_name(), item)
    rig_object = _choose_armature(blend, objects, armature)
    rig_matrix = _matrix(rig_object["obmat"])
    bones = _bones(blend, rig_object, rig_matrix)
    meshes = _meshes(blend, objects, rig_object)
    if not meshes:
        raise FigureImportError(
            "No mesh in this file is deformed by the armature {!r}. Give the body mesh an "
            "Armature modifier that points at it.".format(rig_object.id_name()))
    figure = SourceFigure(bones=bones, meshes=meshes, unit_scale=1.0, up="Z")
    figure.source = {"format": "blend", "file": name, "blender_version": blend.version,
                     "armature": rig_object.id_name(), "bones": len(bones)}
    return figure


def armature_names(path_or_bytes):
    """Every armature in the file, most bones first, for a chooser."""
    try:
        blend = BlendFile(path_or_bytes)
    except BlendFileError as error:
        raise FigureImportError(str(error)) from error
    found = []
    for item in blend.datablocks(b"OB"):
        if item["type"] == OB_ARMATURE:
            found.append((len(_bone_views(blend, item)), item.id_name()))
    return [name for _, name in sorted(found, reverse=True)]


def _choose_armature(blend, objects, wanted):
    armatures = [item for item in objects.values() if item["type"] == OB_ARMATURE]
    if not armatures:
        raise FigureImportError(
            "This file has no armature, so there is nothing to pose. Rig the model first.")
    if wanted:
        for item in armatures:
            if item.id_name() == wanted:
                return item
        raise FigureImportError("This file has no armature named {!r}.".format(wanted))
    if len(armatures) == 1:
        return armatures[0]
    # Several rigs: take the one that actually deforms the most meshes.
    def score(item):
        return (sum(1 for _ in _deformed_meshes(blend, objects, item)),
                len(_bone_views(blend, item)))
    return max(armatures, key=score)


def _bone_views(blend, rig_object):
    """Every bone of an armature object, with its parent's name."""
    data = rig_object.deref("data")
    if data is None:
        return []
    found = []

    def walk(items, parent):
        for bone in items:
            name = bone.string("name")
            found.append((name, parent, bone))
            walk(blend.listbase(bone["childbase"]), name)

    walk(blend.listbase(data["bonebase"]), None)
    return found


def _bones(blend, rig_object, rig_matrix):
    bones = []
    for name, parent, bone in _bone_views(blend, rig_object):
        try:
            matrix = rig_matrix @ _matrix(bone["arm_mat"])
        except (KeyError, TypeError, ValueError) as error:
            raise FigureImportError(
                "Bone {!r} has no rest transform this reader understands.".format(name)) from error
        bones.append(SourceBone(name=name, parent=parent, matrix=matrix))
    if not bones:
        raise FigureImportError("The armature has no bones.")
    return bones


def _deformed_meshes(blend, objects, rig_object):
    name = rig_object.id_name()
    for key in sorted(objects):
        item = objects[key]
        if item["type"] != OB_MESH:
            continue
        for modifier in blend.listbase(item["modifiers"]):
            if modifier.struct.name != "ArmatureModifierData":
                continue
            target = modifier.deref("object")
            if target is not None and target.id_name() == name:
                yield item
                break


def _meshes(blend, objects, rig_object):
    meshes = []
    for item in _deformed_meshes(blend, objects, rig_object):
        mesh = item.deref("data")
        if mesh is None:
            continue
        positions = _positions(blend, mesh, _matrix(item["obmat"]))
        if not positions:
            continue
        indices = _triangles(blend, mesh, len(positions) // 3)
        if not indices:
            continue
        weights = _weights(blend, mesh, len(positions) // 3)
        meshes.append(SourceMesh(name=item.id_name(), positions=positions, normals=[],
                                 weights=weights, indices=indices))
    return meshes


def _positions(blend, mesh, object_matrix):
    """Vertex positions in world space, from either mesh layout."""
    points = []
    vertices = _views(blend, mesh, "mvert")
    if vertices:
        points = [Vec3(*vertex["co"]) for vertex in vertices]
    else:
        values = _attribute(blend, mesh, "vdata", POSITION_NAMES, "f", 3)
        points = [Vec3(values[index], values[index + 1], values[index + 2])
                  for index in range(0, len(values), 3)]
    result = []
    for point in points:
        placed = object_matrix.transform_point(point)
        result.extend((placed.x, placed.y, placed.z))
    return result


def _triangles(blend, mesh, count):
    """Triangles from loops and faces, in either layout."""
    loops = [loop["v"] for loop in _views(blend, mesh, "mloop")]
    if loops:
        faces = [(face["loopstart"], face["totloop"]) for face in _views(blend, mesh, "mpoly")]
    else:
        loops = [int(value) for value in
                 _attribute(blend, mesh, "ldata", CORNER_NAMES, "i", 1)]
        offsets = _face_offsets(blend, mesh)
        faces = [(offsets[index], offsets[index + 1] - offsets[index])
                 for index in range(len(offsets) - 1)]
    indices = []
    for start, total in faces:
        if total < 3 or start < 0 or start + total > len(loops):
            continue
        corners = loops[start:start + total]
        if any(not 0 <= corner < count for corner in corners):
            raise FigureImportError("A face points at a vertex that is not in the file.")
        for corner in range(1, total - 1):  # Fan triangulation, as the compiler does.
            indices.extend((corners[0], corners[corner], corners[corner + 1]))
    return indices


def _face_offsets(blend, mesh):
    for name in OFFSET_NAMES:
        if name in mesh:
            pointer = mesh[name]
            total = mesh["totpoly"] if "totpoly" in mesh else mesh.get("faces_num", 0)
            values = blend.raw(pointer, "i") if pointer else []
            if values:
                return [int(value) for value in values[:int(total) + 1]]
    raise FigureImportError(
        "This file stores its faces in a layout KSP cannot read yet. Export the figure as "
        "glTF Binary (.glb) instead.")


def _attribute(blend, mesh, customdata, names, code, per_item):
    """A named attribute layer's values, for newer Blender meshes."""
    if customdata not in mesh:
        raise FigureImportError(
            "This file stores its mesh in a layout KSP cannot read yet. Export the figure "
            "as glTF Binary (.glb) instead.")
    data = mesh[customdata]
    layers = blend.views(data["layers"]) if data["layers"] else []
    for layer in layers:
        if layer.string("name") in names and layer["data"]:
            values = blend.raw(layer["data"], code)
            if values:
                return list(values)
    raise FigureImportError(
        "This file has no {} data KSP can read. Export the figure as glTF Binary (.glb) "
        "instead.".format(names[0]))


def _weights(blend, mesh, count):
    """Per-vertex ``{vertex group name: weight}``."""
    groups = [group.string("name") for group in blend.listbase(mesh["vertex_group_names"])] \
        if "vertex_group_names" in mesh else []
    deforms = _views(blend, mesh, "dvert")
    weights = [dict() for _ in range(count)]
    if not groups or not deforms:
        return weights
    for vertex, deform in enumerate(deforms):
        if vertex >= count:
            break
        if not deform["totweight"]:
            continue
        for entry in blend.views(deform["dw"]):
            weight = float(entry["weight"])
            if weight <= 0.0:
                continue
            number = int(entry["def_nr"])
            if not 0 <= number < len(groups):
                continue
            name = groups[number]
            weights[vertex][name] = weights[vertex].get(name, 0.0) + weight
    return weights


def _views(blend, mesh, field):
    if field not in mesh or not mesh[field]:
        return []
    try:
        return blend.views(mesh[field])
    except (BlendFileError, KeyError, TypeError):
        return []


def _matrix(rows):
    """A Blender 4x4, which arrives as four rows of four floats."""
    values = []
    for row in rows:
        values.extend(float(value) for value in row)
    if len(values) != 16:
        raise FigureImportError("A transform in the file is damaged.")
    return Mat4(values)
