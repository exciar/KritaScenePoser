"""Read a skinned figure from glTF 2.0 binary (.glb and .vrm).

The file is untrusted, so every offset is checked against the data before it is read.
"""

import json
import struct

from ..core.math3d import Mat4
from .import_figure import FigureImportError, SourceBone, SourceFigure, SourceMesh

MAGIC = 0x46546C67  # "glTF"
JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942
MAX_JSON = 32 * 1024 * 1024
COMPONENTS = {  # componentType: (struct code, size in bytes, maximum value)
    5120: ("b", 1, 127.0), 5121: ("B", 1, 255.0), 5122: ("h", 2, 32767.0),
    5123: ("H", 2, 65535.0), 5125: ("I", 4, 0.0), 5126: ("f", 4, 0.0),
}
COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
UNSUPPORTED = {
    "KHR_draco_mesh_compression": "It uses Draco compression. Export it again with "
                                  "compression turned off.",
    "EXT_meshopt_compression": "It uses meshopt compression. Export it again with "
                              "compression turned off.",
}
TRIANGLES = 4


def read_glb(data, name=""):
    """A :class:`SourceFigure` from ``.glb``/``.vrm`` bytes."""
    document, binary = _chunks(data)
    _check_support(document)
    buffers = _buffers(document, binary)
    nodes = document.get("nodes") or []
    if not isinstance(nodes, list):
        raise FigureImportError("The file's node list is damaged.")
    skin = _skin(document)
    bones, bone_names = _bones(document, nodes, skin, buffers)
    meshes = _meshes(document, nodes, skin, buffers, bone_names)
    if not meshes:
        raise FigureImportError(
            "No mesh in the file is attached to the skeleton. Export the body mesh "
            "together with its armature.")
    figure = SourceFigure(bones=bones, meshes=meshes, unit_scale=1.0, up="Y")
    figure.source = {"format": "glb", "generator": _text(document.get("asset", {}).get(
        "generator", "")), "file": name, "bones": len(bones), "vrm": bool(_vrm_humanoid(document))}
    return figure


def read_glb_pose(data, name=""):
    """The bind skeleton and the pose the file was saved in.

    Returns ``(bones, posed, source)``: bind-pose bones, each bone's posed world
    matrix by name, and where the data came from. An exporter writes the current
    pose into the node transforms and the bind pose into the inverse bind
    matrices, so the difference between the two is the pose to copy.
    """
    document, binary = _chunks(data)
    _check_support(document)
    buffers = _buffers(document, binary)
    nodes = document.get("nodes") or []
    if not isinstance(nodes, list):
        raise FigureImportError("The file's node list is damaged.")
    skin = _skin(document)
    if not isinstance(skin.get("inverseBindMatrices"), int):
        raise FigureImportError(
            "This file has no bind matrices, so KSP cannot tell the pose from the rest "
            "position. Export it again from Blender together with its armature.")
    bones, bone_names = _bones(document, nodes, skin, buffers)
    world, _ = _world_matrices(nodes)
    posed = {bone_names[index]: world.get(index, Mat4.identity())
             for index in skin["joints"] if isinstance(index, int) and index in bone_names}
    source = {"format": "glb", "file": name, "bones": len(bones),
              "generator": _text(document.get("asset", {}).get("generator", ""))}
    return bones, posed, source


def custom_map_from_vrm(data):
    """A bone map from a VRM humanoid table, or ``None`` for a plain glTF."""
    document, _ = _chunks(data)
    humanoid = _vrm_humanoid(document)
    if not humanoid:
        return None
    nodes = document.get("nodes") or []
    from .import_figure import SCHEMES
    vrm_table = SCHEMES["VRM humanoid"]
    by_vrm_name = {vrm.lower(): ksp for ksp, vrm in vrm_table.items()}
    mapping = {}
    for vrm_name, node_index in humanoid.items():
        ksp = by_vrm_name.get(str(vrm_name).lower())
        if ksp is None or not isinstance(node_index, int):
            continue
        if 0 <= node_index < len(nodes):
            mapping[ksp] = _node_name(nodes[node_index], node_index)
    return mapping or None


def _chunks(data):
    """The JSON document and the binary chunk of a GLB container."""
    if not isinstance(data, (bytes, bytearray)):
        raise FigureImportError("The file could not be read as bytes.")
    if len(data) < 20:
        raise FigureImportError("The file is too short to be a .glb file.")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != MAGIC:
        raise FigureImportError(
            "This is not a .glb file. A .gltf file with separate data is not supported yet; "
            "export it as glTF Binary.")
    if version != 2:
        raise FigureImportError("This is glTF version {}; KSP reads version 2.".format(version))
    if length > len(data):
        raise FigureImportError("The file is truncated.")
    document, binary, offset = None, b"", 12
    while offset + 8 <= min(length, len(data)):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        start = offset + 8
        end = start + chunk_length
        if end > len(data):
            raise FigureImportError("The file is truncated inside a chunk.")
        if chunk_type == JSON_CHUNK and document is None:
            if chunk_length > MAX_JSON:
                raise FigureImportError("The file's description is unreasonably large.")
            try:
                document = json.loads(bytes(data[start:end]).decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise FigureImportError("The file's description is damaged.") from error
        elif chunk_type == BIN_CHUNK and not binary:
            binary = bytes(data[start:end])
        offset = end + (-end % 4)
    if not isinstance(document, dict):
        raise FigureImportError("The file has no glTF description.")
    return document, binary


def _check_support(document):
    for extension in (document.get("extensionsRequired") or []):
        message = UNSUPPORTED.get(extension)
        if message:
            raise FigureImportError("KSP cannot read this file. " + message)


def _buffers(document, binary):
    buffers = []
    for index, buffer in enumerate(document.get("buffers") or []):
        uri = buffer.get("uri") if isinstance(buffer, dict) else None
        if uri is None:
            buffers.append(binary)
        elif isinstance(uri, str) and uri.startswith("data:"):
            buffers.append(_data_uri(uri))
        else:
            raise FigureImportError(
                "The file keeps its data in a separate file. Export it as glTF Binary "
                "(.glb), which holds everything in one file.")
    return buffers


def _data_uri(uri):
    import base64
    marker = ";base64,"
    if marker not in uri:
        raise FigureImportError("The file uses a data link KSP cannot read.")
    try:
        return base64.b64decode(uri.split(marker, 1)[1])
    except (ValueError, TypeError) as error:
        raise FigureImportError("The file's embedded data is damaged.") from error


def _skin(document):
    skins = document.get("skins") or []
    if not skins:
        raise FigureImportError(
            "The file has no skin, so nothing is attached to a skeleton. Export the mesh "
            "with its armature and skinning turned on.")
    skin = skins[0]
    if not isinstance(skin, dict) or not isinstance(skin.get("joints"), list):
        raise FigureImportError("The file's skin is damaged.")
    return skin


def _node_name(node, index):
    name = node.get("name") if isinstance(node, dict) else None
    return _text(name) or "bone {}".format(index)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _node_matrix(node):
    """A node's own transform, from a matrix or from translation/rotation/scale."""
    if not isinstance(node, dict):
        return Mat4.identity()
    matrix = node.get("matrix")
    if isinstance(matrix, list) and len(matrix) == 16 and all(
            isinstance(value, (int, float)) for value in matrix):
        return Mat4(matrix)
    from ..core.math3d import Quat, Vec3
    translation = node.get("translation") or [0.0, 0.0, 0.0]
    rotation = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    scale = node.get("scale") or [1.0, 1.0, 1.0]
    try:
        place = Vec3(float(translation[0]), float(translation[1]), float(translation[2]))
        # glTF stores a quaternion as x, y, z, w; KSP keeps w first.
        turn = Quat(float(rotation[3]), float(rotation[0]), float(rotation[1]),
                    float(rotation[2]))
        size = Vec3(float(scale[0]), float(scale[1]), float(scale[2]))
    except (TypeError, ValueError, IndexError) as error:
        raise FigureImportError("A node's transform is damaged.") from error
    if turn.length() < 1e-9:
        turn = Quat(1.0, 0.0, 0.0, 0.0)
    return Mat4.from_trs(place, turn.normalized(), size)


def _world_matrices(nodes):
    """Every node's world transform, and its parent, from the scene hierarchy."""
    parents = {}
    for index, node in enumerate(nodes):
        for child in (node.get("children") or []) if isinstance(node, dict) else []:
            if isinstance(child, int) and 0 <= child < len(nodes) and child not in parents:
                parents[child] = index
    world, visiting = {}, set()

    def resolve(index):
        if index in world:
            return world[index]
        if index in visiting:  # A cycle: treat this node as a root.
            return _node_matrix(nodes[index])
        visiting.add(index)
        local = _node_matrix(nodes[index])
        parent = parents.get(index)
        world[index] = local if parent is None else resolve(parent) @ local
        visiting.discard(index)
        return world[index]

    for index in range(len(nodes)):
        resolve(index)
    return world, parents


def _bones(document, nodes, skin, buffers):
    joints = [index for index in skin["joints"] if isinstance(index, int)]
    if not joints:
        raise FigureImportError("The file's skin lists no bones.")
    for index in joints:
        if not 0 <= index < len(nodes):
            raise FigureImportError("The skin points at a bone that is not in the file.")
    world, parents = _world_matrices(nodes)
    binds = _inverse_binds(document, skin, buffers, len(joints))
    names, bones = {}, []
    for slot, index in enumerate(joints):
        names[index] = _unique(_node_name(nodes[index], index), names.values())
    for slot, index in enumerate(joints):
        try:
            matrix = binds[slot].inverse() if binds else world.get(index, Mat4.identity())
        except (ValueError, ZeroDivisionError, OverflowError) as error:
            raise FigureImportError(
                "Bone {!r} has a bind matrix KSP cannot invert.".format(names[index])) from error
        parent = parents.get(index)
        while parent is not None and parent not in names:
            parent = parents.get(parent)
        bones.append(SourceBone(name=names[index],
                                parent=names.get(parent) if parent is not None else None,
                                matrix=matrix))
    return bones, names


def _unique(name, taken):
    if name not in taken:
        return name
    for suffix in range(2, 1000):
        candidate = "{} {}".format(name, suffix)
        if candidate not in taken:
            return candidate
    raise FigureImportError("The file has too many bones with the same name.")


def _inverse_binds(document, skin, buffers, count):
    accessor = skin.get("inverseBindMatrices")
    if not isinstance(accessor, int):
        return []
    values = _accessor(document, buffers, accessor, want="MAT4")
    if len(values) < count * 16:
        raise FigureImportError("The file's bind matrices are incomplete.")
    matrices = []
    for index in range(count):
        try:
            matrices.append(Mat4(values[index * 16:index * 16 + 16]))
        except (ValueError, TypeError) as error:
            raise FigureImportError("The file's bind matrices are damaged.") from error
    return matrices


def _meshes(document, nodes, skin, buffers, bone_names):
    """Every skinned mesh primitive, as one source mesh each."""
    joints = [index for index in skin["joints"] if isinstance(index, int)]
    order = [bone_names[index] for index in joints]
    meshes = []
    for node_index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("mesh"), int):
            continue
        if not isinstance(node.get("skin"), int):
            continue  # Not skinned: furniture, props, or a stray cube.
        mesh = (document.get("meshes") or [])[node["mesh"]] \
            if 0 <= node["mesh"] < len(document.get("meshes") or []) else None
        if not isinstance(mesh, dict):
            continue
        label = _text(mesh.get("name")) or _node_name(node, node_index)
        for number, primitive in enumerate(mesh.get("primitives") or []):
            part = _primitive(document, buffers, primitive, order, label, number)
            if part is not None:
                meshes.append(part)
    return meshes


def _primitive(document, buffers, primitive, order, label, number):
    if not isinstance(primitive, dict):
        return None
    if primitive.get("mode", TRIANGLES) != TRIANGLES:
        return None  # Lines and points cannot be posed.
    for extension in (primitive.get("extensions") or {}):
        message = UNSUPPORTED.get(extension)
        if message:
            raise FigureImportError("KSP cannot read this file. " + message)
    attributes = primitive.get("attributes")
    if not isinstance(attributes, dict) or "POSITION" not in attributes:
        return None
    positions = _accessor(document, buffers, attributes["POSITION"], want="VEC3")
    count = len(positions) // 3
    if count == 0:
        return None
    normals = _accessor(document, buffers, attributes["NORMAL"], want="VEC3") \
        if isinstance(attributes.get("NORMAL"), int) else []
    if len(normals) != len(positions):
        normals = []
    indices = _indices(document, buffers, primitive, count)
    weights = _weights(document, buffers, attributes, order, count)
    name = label if number == 0 else "{} {}".format(label, number + 1)
    return SourceMesh(name=name, positions=positions, normals=normals, weights=weights,
                      indices=indices)


def _indices(document, buffers, primitive, count):
    accessor = primitive.get("indices")
    if isinstance(accessor, int):
        values = [int(value) for value in _accessor(document, buffers, accessor, want="SCALAR")]
    else:
        values = list(range(count))
    if len(values) % 3:
        values = values[:len(values) - len(values) % 3]
    for value in values:
        if not 0 <= value < count:
            raise FigureImportError("A triangle points at a vertex that is not there.")
    return values


def _weights(document, buffers, attributes, order, count):
    result = [dict() for _ in range(count)]
    for set_number in range(8):
        joint_key = "JOINTS_{}".format(set_number)
        weight_key = "WEIGHTS_{}".format(set_number)
        if not isinstance(attributes.get(joint_key), int):
            break
        joint_values = _accessor(document, buffers, attributes[joint_key], want="VEC4", raw=True)
        weight_values = _accessor(document, buffers, attributes[weight_key], want="VEC4") \
            if isinstance(attributes.get(weight_key), int) else []
        if len(joint_values) < count * 4 or len(weight_values) < count * 4:
            continue
        for vertex in range(count):
            for slot in range(4):
                weight = weight_values[vertex * 4 + slot]
                if weight <= 0.0:
                    continue
                joint = int(joint_values[vertex * 4 + slot])
                if not 0 <= joint < len(order):
                    raise FigureImportError("A vertex is weighted to a bone that is not there.")
                name = order[joint]
                result[vertex][name] = result[vertex].get(name, 0.0) + float(weight)
    return result


def _accessor(document, buffers, index, want=None, raw=False):
    """An accessor's values as a flat list of numbers."""
    accessors = document.get("accessors") or []
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        raise FigureImportError("The file points at data that is not there.")
    accessor = accessors[index]
    if not isinstance(accessor, dict):
        raise FigureImportError("The file's data description is damaged.")
    kind = accessor.get("type")
    if want is not None and kind != want:
        raise FigureImportError("The file's data is the wrong shape ({} for {}).".format(
            kind, want))
    per_item = COUNTS.get(kind)
    component = COMPONENTS.get(accessor.get("componentType"))
    count = accessor.get("count")
    if per_item is None or component is None or not isinstance(count, int) or count < 0:
        raise FigureImportError("The file's data description is damaged.")
    code, size, maximum = component
    values = [0.0] * (count * per_item)
    view_index = accessor.get("bufferView")
    if isinstance(view_index, int):
        values = _read_view(document, buffers, view_index, accessor, code, size, per_item, count)
    _sparse(document, buffers, accessor, values, code, size, per_item)
    if accessor.get("normalized") and maximum and not raw:
        values = [value / maximum for value in values]
    return values


def _read_view(document, buffers, view_index, accessor, code, size, per_item, count):
    views = document.get("bufferViews") or []
    if not 0 <= view_index < len(views) or not isinstance(views[view_index], dict):
        raise FigureImportError("The file points at data that is not there.")
    view = views[view_index]
    buffer_index = view.get("buffer", 0)
    if not isinstance(buffer_index, int) or not 0 <= buffer_index < len(buffers):
        raise FigureImportError("The file points at a buffer that is not there.")
    data = buffers[buffer_index]
    base = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    stride = int(view.get("byteStride", 0)) or size * per_item
    if base < 0 or stride <= 0:
        raise FigureImportError("The file's data layout is damaged.")
    needed = base + stride * (count - 1) + size * per_item if count else base
    if needed > len(data) or base + int(view.get("byteLength", 0)) > len(data) + 1:
        raise FigureImportError("The file's data runs past the end of the file.")
    unpack = struct.Struct("<" + code * per_item).unpack_from
    values = []
    for item in range(count):
        values.extend(unpack(data, base + item * stride))
    return values


def _sparse(document, buffers, accessor, values, code, size, per_item):
    """Apply an accessor's sparse overrides, which replace some of its values."""
    sparse = accessor.get("sparse")
    if not isinstance(sparse, dict):
        return
    count = sparse.get("count")
    indices = sparse.get("indices")
    source = sparse.get("values")
    if not isinstance(count, int) or count <= 0 or not isinstance(indices, dict) \
            or not isinstance(source, dict):
        raise FigureImportError("The file's sparse data is damaged.")
    index_component = COMPONENTS.get(indices.get("componentType"))
    if index_component is None:
        raise FigureImportError("The file's sparse data is damaged.")
    positions = _read_view(document, buffers, indices.get("bufferView"),
                           {"byteOffset": indices.get("byteOffset", 0)},
                           index_component[0], index_component[1], 1, count)
    replacements = _read_view(document, buffers, source.get("bufferView"),
                              {"byteOffset": source.get("byteOffset", 0)},
                              code, size, per_item, count)
    for slot, position in enumerate(positions):
        target = int(position)
        if not 0 <= target * per_item + per_item <= len(values):
            raise FigureImportError("The file's sparse data points outside its own data.")
        values[target * per_item:target * per_item + per_item] = \
            replacements[slot * per_item:slot * per_item + per_item]


def _vrm_humanoid(document):
    """``{VRM bone name: node index}`` from either VRM version, or ``{}``."""
    extensions = document.get("extensions") or {}
    if not isinstance(extensions, dict):
        return {}
    legacy = ((extensions.get("VRM") or {}).get("humanoid") or {}).get("humanBones")
    if isinstance(legacy, list):
        return {entry.get("bone"): entry.get("node") for entry in legacy
                if isinstance(entry, dict)}
    modern = ((extensions.get("VRMC_vrm") or {}).get("humanoid") or {}).get("humanBones")
    if isinstance(modern, dict):
        return {name: (entry or {}).get("node") for name, entry in modern.items()
                if isinstance(entry, dict)}
    return {}
