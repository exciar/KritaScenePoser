#!/usr/bin/env python3
"""Compile KSP figure assets from bodychan-bodykun.blend. Development tool; never shipped.

    py -3.14 tools/compile_assets.py [--blend FILE] [--preview preview.png]

Writes ``<figure>.rig.json`` and ``<figure>.mesh`` into
``krita_scene_poser/assets/figures/``. The .blend is read by
``tools/blendfile.py``; Blender itself is not needed.
"""

import argparse
import colorsys
import hashlib
import math
from pathlib import Path
import struct
import sys
import zlib
from array import array

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from blendfile import BlendFile  # noqa: E402
from krita_scene_poser.core.math3d import EPSILON, Mat4, Quat, Vec3, X_AXIS  # noqa: E402
from krita_scene_poser.storage.mesh_io import MeshData, write_mesh  # noqa: E402
from krita_scene_poser.storage.rig_io import RigJoint, read_rig, write_rig  # noqa: E402

SOURCE = ROOT / "bodychan-bodykun.blend"
OUTPUT = ROOT / "krita_scene_poser" / "assets" / "figures"
# (figure id, display name, armature object), confirmed visually with --preview:
# "rig" drives Body-chan; "body_kun_rig" drives Body-kun (whose upper-torso
# part happens to be named "bust").
FIGURES = (
    ("body_chan", "Body-chan", "rig"),
    ("body_kun", "Body-kun", "body_kun_rig"),
)
TARGET_HEIGHT = 1.75  # Meters for the taller figure; both share one scale.
PROVENANCE = {"creator": "vinchau", "license": "CC0", "url": "https://blendswap.com/blend/23521"}
ME_SMOOTH = 1
ME_AUTOSMOOTH = 32
# Blender is Z-up with figures facing -Y; KSP is Y-up with figures facing +Z.
TO_KSP = Quat.from_axis_angle(X_AXIS, -math.pi / 2)


def joint_map():
    """KSP joints: (name, parent, source deform bones merged into it), parents first."""
    joints = [("hips", None, ["DEF-spine", "DEF-pelvis.L", "DEF-pelvis.R"]),
              ("waist", "hips", ["DEF-spine.001"]),
              ("torso", "waist", ["DEF-spine.002"]),
              ("chest", "torso", ["DEF-spine.003"]),
              ("neck", "chest", ["DEF-spine.004", "DEF-spine.005"]),
              ("head", "neck", ["DEF-spine.006"])]
    for s in ("L", "R"):
        def bones(base, twist=False):
            return ["DEF-{}.{}".format(base, s)] + (["DEF-{}.{}.001".format(base, s)] if twist else [])
        joints += [("shoulder." + s, "chest", bones("shoulder")),
                   ("upper_arm." + s, "shoulder." + s, bones("upper_arm", True)),
                   ("forearm." + s, "upper_arm." + s, bones("forearm", True)),
                   ("hand." + s, "forearm." + s,
                    bones("hand") + ["DEF-palm.0{}.{}".format(i, s) for i in range(1, 5)])]
        for finger, source in (("thumb", "thumb"), ("index", "f_index"), ("middle", "f_middle"),
                               ("ring", "f_ring"), ("pinky", "f_pinky")):
            parent = "hand." + s
            for n in (1, 2, 3):
                name = "{}.0{}.{}".format(finger, n, s)
                joints.append((name, parent, ["DEF-{}.0{}.{}".format(source, n, s)]))
                parent = name
        joints += [("thigh." + s, "hips", bones("thigh", True)),
                   ("shin." + s, "thigh." + s, bones("shin", True)),
                   ("foot." + s, "shin." + s, bones("foot")),
                   ("toe." + s, "foot." + s, bones("toe"))]
    return joints


def matrix(view_rows):
    """Blender stores float[4][4] as columns; Mat4 is column-major too."""
    return Mat4([value for column in view_rows for value in column])


def to_ksp(point):
    return TO_KSP.rotate(point)


class Figure:
    def __init__(self, blend, figure, display_name, armature):
        self.figure, self.display_name, self.armature = figure, display_name, armature
        objects = {o.id_name(): o for o in blend.datablocks(b"OB")}
        rig_object = objects[armature]
        rig_matrix = matrix(rig_object["obmat"])
        bones = {}

        def collect(items):
            for bone in items:
                bones[bone.string("name")] = bone
                collect(blend.listbase(bone["childbase"]))
        collect(blend.listbase(rig_object.deref("data")["bonebase"]))

        self.joints = []  # (name, parent, position, rotation, tail) in unscaled KSP axes
        self.group_joint = {}
        for index, (name, parent, sources) in enumerate(joint_map()):
            missing = [s for s in sources if s not in bones]
            if missing:
                raise ValueError("{}: missing deform bones {}".format(armature, missing))
            primary = bones[sources[0]]
            world = rig_matrix @ matrix(primary["arm_mat"])
            last = bones[sources[-1]] if sources[-1].endswith(".001") else primary
            self.joints.append((
                name, parent,
                to_ksp(rig_matrix.transform_point(Vec3(*primary["arm_head"]))),
                (TO_KSP * Quat.from_matrix(world)).normalized(),
                to_ksp(rig_matrix.transform_point(Vec3(*last["arm_tail"])))))
            for source in sources:
                self.group_joint[source] = index

        self.parts, self.meshes = [], []
        for name in sorted(objects):
            obj = objects[name]
            if obj["type"] != 1:
                continue
            targets = [m.deref("object") for m in blend.listbase(obj["modifiers"])
                       if m.struct.name == "ArmatureModifierData"]
            if not any(t is not None and t.id_name() == armature for t in targets):
                continue
            self.parts.append(name)
            self.meshes.append(self._read_mesh(blend, obj, len(self.parts) - 1))

    def _read_mesh(self, blend, obj, part):
        object_matrix = matrix(obj["obmat"])
        mesh = obj.deref("data")
        positions = [to_ksp(object_matrix.transform_point(Vec3(*v["co"])))
                     for v in blend.views(mesh["mvert"])]
        loops = [loop["v"] for loop in blend.views(mesh["mloop"])]
        polys = [(p["loopstart"], p["totloop"], bool(p["flag"] & ME_SMOOTH))
                 for p in blend.views(mesh["mpoly"])]
        groups = [g.string("name") for g in blend.listbase(mesh["vertex_group_names"])]
        weights = []
        for vertex, deform in enumerate(blend.views(mesh["dvert"])):
            accumulated = {}
            for entry in (blend.views(deform["dw"]) if deform["totweight"] else []):
                if entry["weight"] <= 0.0:
                    continue
                group = groups[entry["def_nr"]]
                if group not in self.group_joint:
                    raise ValueError("{}: vertex group {} is not mapped to a KSP joint".format(
                        obj.id_name(), group))
                joint = self.group_joint[group]
                accumulated[joint] = accumulated.get(joint, 0.0) + entry["weight"]
            weights.append(accumulated)
        auto_smooth = bool(mesh["flag"] & ME_AUTOSMOOTH)
        return {"positions": positions, "loops": loops, "polys": polys, "weights": weights,
                "auto_smooth": auto_smooth, "angle": mesh["smoothresh"], "part": part}

    def raw_bounds(self):
        ys = [p.y for m in self.meshes for p in m["positions"]]
        return min(ys), max(ys)


def quantize_weights(weights, fallback):
    """Top four joints, normalized to integers summing to exactly 255."""
    items = sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:4] or [(fallback, 1.0)]
    total = sum(w for _, w in items)
    exact = [(joint, 255.0 * w / total) for joint, w in items]
    floors = [(joint, int(value)) for joint, value in exact]
    remainder = 255 - sum(v for _, v in floors)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i][1] - floors[i][1]), i))
    result = [list(pair) for pair in floors]
    for i in order[:remainder]:
        result[i][1] += 1
    while len(result) < 4:
        result.append([result[0][0], 0])
    return result


def build_mesh(figure, scale, offset):
    positions, normals, joints, weights, parts, indices = (
        array("f"), array("f"), bytearray(), bytearray(), bytearray(), array("I"))
    unweighted = 0
    for mesh in figure.meshes:
        points = [(p + offset) * scale for p in mesh["positions"]]
        face_normals, incident = [], [[] for _ in points]
        for number, (start, count, _) in enumerate(mesh["polys"]):
            corners = mesh["loops"][start:start + count]
            n = Vec3(0.0, 0.0, 0.0)
            for a, b in zip(corners, corners[1:] + corners[:1]):  # Newell's method.
                pa, pb = points[a], points[b]
                n = n + Vec3((pa.y - pb.y) * (pa.z + pb.z), (pa.z - pb.z) * (pa.x + pb.x),
                             (pa.x - pb.x) * (pa.y + pb.y))
            # Zero-area faces get no normal and do not influence neighbors.
            face_normals.append(n if n.length() >= EPSILON else None)
            for v in corners:
                incident[v].append(number)
        cos_limit = math.cos(mesh["angle"]) if mesh["auto_smooth"] else -2.0
        dominant = {}
        for w in mesh["weights"]:
            for joint, value in w.items():
                dominant[joint] = dominant.get(joint, 0.0) + value
        fallback = max(dominant, key=dominant.get) if dominant else 0
        units = [n.normalized() if n is not None else None for n in face_normals]
        lookup = {}
        for number, (start, count, smooth) in enumerate(mesh["polys"]):
            own = face_normals[number]
            own_unit = units[number]
            corner_ids = []
            for v in mesh["loops"][start:start + count]:
                total = own if own is not None else Vec3(0.0, 0.0, 0.0)
                if smooth:
                    for other in incident[v]:
                        if other == number or not mesh["polys"][other][2] or units[other] is None:
                            continue
                        if own_unit is None or own_unit.dot(units[other]) >= cos_limit:
                            total = total + face_normals[other]
                normal = total.normalized() if total.length() >= EPSILON else Vec3(0.0, 1.0, 0.0)
                key = (v, round(normal.x, 4), round(normal.y, 4), round(normal.z, 4))
                if key not in lookup:
                    lookup[key] = len(parts)
                    positions.extend(points[v])
                    normals.extend(normal)
                    if not mesh["weights"][v]:
                        unweighted += 1
                    for joint, value in quantize_weights(mesh["weights"][v], fallback):
                        joints.append(joint)
                        weights.append(value)
                    parts.append(mesh["part"])
                corner_ids.append(lookup[key])
            for i in range(1, len(corner_ids) - 1):
                indices.extend((corner_ids[0], corner_ids[i], corner_ids[i + 1]))
    # Interleave the per-vertex joint/weight pairs into planar arrays.
    return MeshData(positions, normals, bytes(joints), bytes(weights), bytes(parts), indices,
                    tuple(figure.parts), len(figure.joints)), unweighted


def rig_joints(figure, scale, offset):
    return [RigJoint(name, parent, (position + offset) * scale, rotation, (tail + offset) * scale)
            for name, parent, position, rotation, tail in figure.joints]


def write_png(path, width, height, pixels):
    rows = b"".join(b"\0" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(
        ">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b""))


def render_preview(path, figures, panel=(300, 600)):
    """Front and side orthographic views of each figure, with its joints overlaid."""
    width, height = panel
    views = [("front", lambda p: (p.x, p.y, p.z)), ("side", lambda p: (-p.z, p.y, p.x))]
    image = bytearray(b"\xf2" * (width * len(views) * len(figures) * height * 3))
    stride = width * len(views) * len(figures)
    light = Vec3(-0.4, 0.6, 0.7).normalized()
    for f_index, (mesh, rig) in enumerate(figures):
        for v_index, (_, project) in enumerate(views):
            x0 = (f_index * len(views) + v_index) * width
            depth = [-1e9] * (width * height)
            pixel_scale = (height - 20) / 1.9

            def to_screen(p):
                x, y, z = project(p)
                return x0 + width / 2 + x * pixel_scale, height - 10 - y * pixel_scale, z
            p = mesh.positions
            screen = [to_screen(Vec3(p[i], p[i + 1], p[i + 2])) for i in range(0, len(p), 3)]
            for t in range(0, len(mesh.indices), 3):
                a, b, c = (screen[mesh.indices[t + k]] for k in range(3))
                area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if abs(area) < 1e-12:
                    continue
                i0 = mesh.indices[t]
                n = Vec3(*mesh.normals[i0 * 3:i0 * 3 + 3])
                hue = (mesh.parts[i0] * 0.137) % 1.0
                shade = 0.35 + 0.65 * max(0.0, Vec3(*project(n)).dot(light))
                color = [int(255 * c * shade) for c in colorsys.hsv_to_rgb(hue, 0.25, 0.95)]
                xmin, xmax = max(int(min(a[0], b[0], c[0])), x0), min(int(max(a[0], b[0], c[0])) + 1, x0 + width)
                ymin, ymax = max(int(min(a[1], b[1], c[1])), 0), min(int(max(a[1], b[1], c[1])) + 1, height)
                for y in range(ymin, ymax):
                    for x in range(xmin, xmax):
                        px, py = x + 0.5, y + 0.5
                        w0 = ((b[0] - px) * (c[1] - py) - (b[1] - py) * (c[0] - px)) / area
                        w1 = ((c[0] - px) * (a[1] - py) - (c[1] - py) * (a[0] - px)) / area
                        w2 = 1.0 - w0 - w1
                        if w0 < 0 or w1 < 0 or w2 < 0:
                            continue
                        z = w0 * a[2] + w1 * b[2] + w2 * c[2]
                        slot = y * width + (x - x0)
                        if z > depth[slot]:
                            depth[slot] = z
                            image[(y * stride + x) * 3:(y * stride + x) * 3 + 3] = bytes(color)
            for joint in rig.joints:
                sx, sy, _ = to_screen(joint.position)
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        x, y = int(sx) + dx, int(sy) + dy
                        if x0 <= x < x0 + width and 0 <= y < height:
                            image[(y * stride + x) * 3:(y * stride + x) * 3 + 3] = b"\xd0\x20\x20"
    write_png(path, stride, height, image)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blend", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--preview", type=Path, help="write a PNG preview of both figures")
    args = parser.parse_args(argv)
    raw = args.blend.read_bytes()
    blend = BlendFile(raw)
    figures = [Figure(blend, *entry) for entry in FIGURES]
    heights = [top - bottom for bottom, top in (f.raw_bounds() for f in figures)]
    scale = TARGET_HEIGHT / max(heights)
    args.output.mkdir(parents=True, exist_ok=True)
    compiled = []
    for figure in figures:
        bottom, _ = figure.raw_bounds()
        offset = Vec3(0.0, -bottom, 0.0)  # Stand the figure on the ground plane y = 0.
        joints = rig_joints(figure, scale, offset)
        source = dict(PROVENANCE, file=args.blend.name, sha256=hashlib.sha256(raw).hexdigest(),
                      armature=figure.armature, tool="tools/compile_assets.py",
                      scale=round(scale, 9), ground_offset=round(-bottom, 9))
        rig_text = write_rig(figure.figure, figure.display_name, joints, source)
        mesh, unweighted = build_mesh(figure, scale, offset)
        (args.output / (figure.figure + ".rig.json")).write_text(rig_text, encoding="utf-8", newline="\n")
        (args.output / (figure.figure + ".mesh")).write_bytes(write_mesh(mesh))
        low, high = mesh.bounds()
        print("{}: {} joints, {} parts, {} vertices, {} triangles, height {:.3f} m, "
              "{} unweighted source vertices".format(
                  figure.figure, len(joints), len(figure.parts), mesh.vertex_count,
                  mesh.triangle_count, high[1] - low[1], unweighted))
        compiled.append((mesh, read_rig(rig_text)))
    if args.preview:
        render_preview(args.preview, compiled)
        print("Preview written to", args.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
