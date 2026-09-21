"""Build small .glb files for the importer tests.

Writing the fixtures here keeps the test suite free of binary blobs, and it
documents exactly what shape of file the reader is expected to accept.
"""

import json
import struct

from krita_scene_poser.core.math3d import Mat4, Quat, Vec3

MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
FLOAT, USHORT, UBYTE = 5126, 5123, 5121

# A small figure: the spine, both legs with toes, and one arm. Positions are in
# meters, Y up, facing +Z, which is what a Blender glTF export produces.
SKELETON = (
    ("mixamorig:Hips", None, (0.0, 0.95, 0.0)),
    ("mixamorig:Spine", "mixamorig:Hips", (0.0, 1.10, 0.0)),
    ("mixamorig:Spine1", "mixamorig:Spine", (0.0, 1.25, 0.0)),
    ("mixamorig:Spine2", "mixamorig:Spine1", (0.0, 1.40, 0.0)),
    ("mixamorig:Neck", "mixamorig:Spine2", (0.0, 1.55, 0.0)),
    ("mixamorig:Head", "mixamorig:Neck", (0.0, 1.65, 0.0)),
    ("mixamorig:LeftShoulder", "mixamorig:Spine2", (0.06, 1.48, 0.0)),
    ("mixamorig:LeftArm", "mixamorig:LeftShoulder", (0.18, 1.45, 0.0)),
    ("mixamorig:LeftForeArm", "mixamorig:LeftArm", (0.45, 1.45, 0.02)),
    ("mixamorig:LeftHand", "mixamorig:LeftForeArm", (0.70, 1.45, 0.0)),
    ("mixamorig:RightShoulder", "mixamorig:Spine2", (-0.06, 1.48, 0.0)),
    ("mixamorig:RightArm", "mixamorig:RightShoulder", (-0.18, 1.45, 0.0)),
    ("mixamorig:RightForeArm", "mixamorig:RightArm", (-0.45, 1.45, 0.02)),
    ("mixamorig:RightHand", "mixamorig:RightForeArm", (-0.70, 1.45, 0.0)),
    ("mixamorig:LeftUpLeg", "mixamorig:Hips", (0.10, 0.90, 0.0)),
    ("mixamorig:LeftLeg", "mixamorig:LeftUpLeg", (0.10, 0.50, 0.0)),
    ("mixamorig:LeftFoot", "mixamorig:LeftLeg", (0.10, 0.08, 0.0)),
    ("mixamorig:LeftToeBase", "mixamorig:LeftFoot", (0.10, 0.03, 0.12)),
    ("mixamorig:RightUpLeg", "mixamorig:Hips", (-0.10, 0.90, 0.0)),
    ("mixamorig:RightLeg", "mixamorig:RightUpLeg", (-0.10, 0.50, 0.0)),
    ("mixamorig:RightFoot", "mixamorig:RightLeg", (-0.10, 0.08, 0.0)),
    ("mixamorig:RightToeBase", "mixamorig:RightFoot", (-0.10, 0.03, 0.12)),
)


def box(center, size=(0.12, 0.2, 0.1)):
    """Positions and triangles of a box, as a stand-in for a body part."""
    cx, cy, cz = center
    hx, hy, hz = (value / 2.0 for value in size)
    corners = [(cx + sx * hx, cy + sy * hy, cz + sz * hz)
               for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    faces = ((0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
             (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3))
    positions = [value for corner in corners for value in corner]
    indices = [index for face in faces for index in face]
    return positions, indices


class GlbBuilder:
    """Assembles a .glb from bone and mesh descriptions."""

    def __init__(self, skeleton=SKELETON, flip_facing=False, scale=1.0):
        self.skeleton = skeleton
        self.flip = flip_facing
        self.scale = scale
        self.binary = bytearray()
        self.views = []
        self.accessors = []

    # Buffer helpers ---------------------------------------------------------

    def _view(self, payload, stride=None):
        while len(self.binary) % 4:
            self.binary.append(0)
        offset = len(self.binary)
        self.binary.extend(payload)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if stride:
            view["byteStride"] = stride
        self.views.append(view)
        return len(self.views) - 1

    def _accessor(self, values, kind, component, normalized=False):
        code = {FLOAT: "f", USHORT: "H", UBYTE: "B"}[component]
        per_item = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}[kind]
        payload = struct.pack("<" + code * len(values), *values)
        view = self._view(payload)
        accessor = {"bufferView": view, "componentType": component,
                    "count": len(values) // per_item, "type": kind}
        if normalized:
            accessor["normalized"] = True
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    # Content ----------------------------------------------------------------

    def _place(self, point):
        x, y, z = (value * self.scale for value in point)
        return (-x, y, -z) if self.flip else (x, y, z)

    def build(self, parts=None, with_normals=True, vrm=False, extras=None):
        names = [name for name, _, _ in self.skeleton]
        index_of = {name: index for index, name in enumerate(names)}
        world = {name: Vec3(*self._place(place)) for name, _, place in self.skeleton}
        nodes = []
        for name, parent, _ in self.skeleton:
            place = world[name]
            origin = world[parent] if parent else Vec3(0.0, 0.0, 0.0)
            nodes.append({"name": name,
                          "translation": [place.x - origin.x, place.y - origin.y,
                                          place.z - origin.z],
                          "children": []})
        for index, (name, parent, _) in enumerate(self.skeleton):
            if parent:
                nodes[index_of[parent]]["children"].append(index)
        for node in nodes:
            if not node["children"]:
                node.pop("children")

        binds = []
        for name in names:
            place = world[name]
            binds.extend(Mat4.from_trs(place, Quat(1.0, 0.0, 0.0, 0.0)).inverse().m)
        bind_accessor = self._accessor(binds, "MAT4", FLOAT)

        parts = parts if parts is not None else [
            ("torso", "mixamorig:Spine1", (0.0, 1.25, 0.0), (0.3, 0.6, 0.2)),
            ("head", "mixamorig:Head", (0.0, 1.70, 0.0), (0.2, 0.25, 0.2)),
            ("left leg", "mixamorig:LeftUpLeg", (0.10, 0.60, 0.0), (0.15, 0.7, 0.15)),
            ("right leg", "mixamorig:RightUpLeg", (-0.10, 0.60, 0.0), (0.15, 0.7, 0.15)),
        ]
        primitives, meshes, mesh_nodes = [], [], []
        for label, bone, center, size in parts:
            positions, indices = box(self._place(center), size)
            normals = []
            if with_normals:
                for vertex in range(len(positions) // 3):
                    normals.extend((0.0, 1.0, 0.0))
            joint = index_of[bone]
            joints, weights = [], []
            for _ in range(len(positions) // 3):
                joints.extend((joint, 0, 0, 0))
                weights.extend((255, 0, 0, 0))
            attributes = {"POSITION": self._accessor(positions, "VEC3", FLOAT),
                          "JOINTS_0": self._accessor(joints, "VEC4", USHORT),
                          "WEIGHTS_0": self._accessor(weights, "VEC4", UBYTE, normalized=True)}
            if with_normals:
                attributes["NORMAL"] = self._accessor(normals, "VEC3", FLOAT)
            primitive = {"attributes": attributes,
                         "indices": self._accessor(indices, "SCALAR", USHORT)}
            meshes.append({"name": label, "primitives": [primitive]})
            mesh_nodes.append({"name": label + " node", "mesh": len(meshes) - 1, "skin": 0})
            primitives.append(primitive)

        root_children = [index for index, (_, parent, _) in enumerate(self.skeleton)
                         if parent is None]
        first_mesh_node = len(nodes)
        nodes.extend(mesh_nodes)
        document = {
            "asset": {"version": "2.0", "generator": "KSP test fixture"},
            "scene": 0,
            "scenes": [{"nodes": root_children + list(
                range(first_mesh_node, first_mesh_node + len(mesh_nodes)))}],
            "nodes": nodes,
            "meshes": meshes,
            "skins": [{"joints": list(range(len(names))),
                       "inverseBindMatrices": bind_accessor}],
            "accessors": self.accessors,
            "bufferViews": self.views,
            "buffers": [{"byteLength": len(self.binary)}],
        }
        if vrm:
            document["extensions"] = {"VRM": {"humanoid": {"humanBones": [
                {"bone": "hips", "node": index_of["mixamorig:Hips"]},
                {"bone": "spine", "node": index_of["mixamorig:Spine"]},
                {"bone": "head", "node": index_of["mixamorig:Head"]},
                {"bone": "leftUpperArm", "node": index_of["mixamorig:LeftArm"]},
                {"bone": "leftLowerArm", "node": index_of["mixamorig:LeftForeArm"]},
            ]}}}
        if extras:
            document.update(extras)
        return pack(document, bytes(self.binary))


def pack(document, binary):
    """Wrap a glTF document and its data in the .glb container."""
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), JSON_CHUNK) + text
    if binary:
        chunks += struct.pack("<II", len(binary), BIN_CHUNK) + binary
    return struct.pack("<III", MAGIC, 2, 12 + len(chunks)) + chunks


def simple_glb(**options):
    return GlbBuilder(**{k: v for k, v in options.items()
                         if k in ("skeleton", "flip_facing", "scale")}).build(
        **{k: v for k, v in options.items() if k in ("parts", "with_normals", "vrm", "extras")})
