"""Versioned binary mesh format for skinned figures (``.mesh``).

Layout, all little-endian:

    magic "KSPMESH\\0"
    uint32 version, vertex_count, index_count, joint_count, index_size, metadata_length
    metadata: UTF-8 JSON ({"parts": [...], "bounds": {...}}), padded to 4 bytes
    float32 positions[3 * vertex_count]   meters, +Y up, figure facing +Z
    float32 normals[3 * vertex_count]
    uint8 joints[4 * vertex_count]        skeleton joint indices
    uint8 weights[4 * vertex_count]       per vertex, sums to exactly 255
    uint8 parts[vertex_count]             index into metadata "parts"
    padding to 4 bytes
    uint16 or uint32 indices[index_count] counter-clockwise triangles

Arrays are planar so each can be uploaded to the GPU without reshuffling.
"""

from array import array
from dataclasses import dataclass
import json
import math
import struct
import sys

MAGIC = b"KSPMESH\0"
VERSION = 1
HEADER = struct.Struct("<8s6I")
MAX_JOINTS = 256


class MeshFormatError(ValueError):
    """The mesh file is damaged, from a newer KSP, or inconsistent."""


@dataclass(frozen=True)
class MeshData:
    positions: array  # 'f', 3 per vertex
    normals: array  # 'f', 3 per vertex
    joints: bytes  # 4 per vertex
    weights: bytes  # 4 per vertex, summing to 255
    parts: bytes  # 1 per vertex
    indices: array  # 'H' or 'I'
    part_names: tuple
    joint_count: int

    @property
    def vertex_count(self):
        return len(self.parts)

    @property
    def triangle_count(self):
        return len(self.indices) // 3

    def bounds(self):
        p = self.positions
        axes = [p[i::3] for i in range(3)]
        return [min(a) for a in axes], [max(a) for a in axes]


def _little_endian(values):
    if sys.byteorder == "big":
        values = array(values.typecode, values)
        values.byteswap()
    return values.tobytes()


def _from_little_endian(typecode, data):
    values = array(typecode)
    values.frombytes(data)
    if sys.byteorder == "big":
        values.byteswap()
    return values


def _pad(data):
    return data + b"\0" * (-len(data) % 4)


def validate(mesh):
    """Raise MeshFormatError unless every array is consistent."""
    n = mesh.vertex_count
    if not 0 < n:
        raise MeshFormatError("The mesh has no vertices.")
    if not 0 < mesh.joint_count <= MAX_JOINTS:
        raise MeshFormatError("The joint count must be between 1 and 256.")
    for name, values, per_vertex in (("positions", mesh.positions, 3), ("normals", mesh.normals, 3),
                                     ("joints", mesh.joints, 4), ("weights", mesh.weights, 4)):
        if len(values) != per_vertex * n:
            raise MeshFormatError("The {} array does not match the vertex count.".format(name))
    if len(mesh.indices) == 0 or len(mesh.indices) % 3:
        raise MeshFormatError("The index count must be a positive multiple of 3.")
    if max(mesh.indices) >= n:
        raise MeshFormatError("A triangle references a missing vertex.")
    if not all(math.isfinite(v) for v in mesh.positions) or not all(math.isfinite(v) for v in mesh.normals):
        raise MeshFormatError("The mesh contains non-finite coordinates.")
    if max(mesh.joints) >= mesh.joint_count:
        raise MeshFormatError("A vertex references a missing joint.")
    if not mesh.part_names or max(mesh.parts) >= len(mesh.part_names):
        raise MeshFormatError("A vertex references a missing part.")
    weights = mesh.weights
    for i in range(0, len(weights), 4):
        if weights[i] + weights[i + 1] + weights[i + 2] + weights[i + 3] != 255:
            raise MeshFormatError("Vertex {} weights do not sum to 255.".format(i // 4))


def write_mesh(mesh):
    validate(mesh)
    index_size = 2 if mesh.vertex_count <= 0xFFFF else 4
    indices = array("H" if index_size == 2 else "I", mesh.indices)
    low, high = mesh.bounds()
    metadata = _pad(json.dumps({
        "parts": list(mesh.part_names),
        "bounds": {"min": [round(v, 6) for v in low], "max": [round(v, 6) for v in high]},
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return b"".join((
        HEADER.pack(MAGIC, VERSION, mesh.vertex_count, len(indices), mesh.joint_count,
                    index_size, len(metadata)),
        metadata,
        _little_endian(array("f", mesh.positions)),
        _little_endian(array("f", mesh.normals)),
        _pad(bytes(mesh.joints) + bytes(mesh.weights) + bytes(mesh.parts)),
        _little_endian(indices),
    ))


def read_mesh(data, joint_count=None):
    """Parse and validate; ``joint_count`` checks the mesh fits its rig."""
    if len(data) < HEADER.size:
        raise MeshFormatError("The mesh file is truncated.")
    magic, version, n, index_count, joints, index_size, metadata_length = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise MeshFormatError("Not a KSP mesh file.")
    if version != VERSION:
        raise MeshFormatError("Unsupported mesh version {} (this KSP reads {}).".format(version, VERSION))
    if index_size not in (2, 4) or metadata_length % 4:
        raise MeshFormatError("The mesh header is inconsistent.")
    byte_counts = (metadata_length, 12 * n, 12 * n, 9 * n + (-9 * n % 4), index_size * index_count)
    if len(data) != HEADER.size + sum(byte_counts):
        raise MeshFormatError("The mesh file size does not match its header.")
    position = HEADER.size
    try:
        metadata = json.loads(data[position:position + metadata_length].rstrip(b"\0").decode("utf-8"))
        part_names = tuple(str(name) for name in metadata["parts"])
    except (ValueError, KeyError, TypeError) as error:
        raise MeshFormatError("The mesh metadata is damaged.") from error
    position += metadata_length
    positions = _from_little_endian("f", data[position:position + 12 * n])
    position += 12 * n
    normals = _from_little_endian("f", data[position:position + 12 * n])
    position += 12 * n
    joint_bytes = bytes(data[position:position + 4 * n])
    weights = bytes(data[position + 4 * n:position + 8 * n])
    parts = bytes(data[position + 8 * n:position + 9 * n])
    position += byte_counts[3]
    indices = _from_little_endian("H" if index_size == 2 else "I",
                                  data[position:position + index_size * index_count])
    mesh = MeshData(positions, normals, joint_bytes, weights, parts, indices, part_names, joints)
    validate(mesh)
    if joint_count is not None and joints != joint_count:
        raise MeshFormatError("The mesh expects {} joints; its rig has {}.".format(joints, joint_count))
    return mesh
