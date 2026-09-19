"""Mesh and rig file formats: round trips and rejection of damaged files."""

from array import array
import json
import math
import unittest

from krita_scene_poser.core.math3d import IDENTITY, Quat, Vec3, Z_AXIS
from krita_scene_poser.storage.mesh_io import (
    HEADER, MeshData, MeshFormatError, read_mesh, write_mesh,
)
from krita_scene_poser.storage.rig_io import RigFormatError, RigJoint, read_rig, write_rig


def small_mesh(**changes):
    values = dict(
        positions=array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]),
        normals=array("f", [0, 0, 1] * 4),
        joints=bytes([0, 1, 0, 0] * 4), weights=bytes([200, 55, 0, 0] * 4),
        parts=bytes([0, 0, 1, 1]), indices=array("I", [0, 1, 2, 0, 2, 3]),
        part_names=("torso", "arm"), joint_count=2)
    values.update(changes)
    return MeshData(**values)


def rig_joints():
    tilted = Quat.from_axis_angle(Z_AXIS, 0.4)
    return [RigJoint("hips", None, Vec3(0, 1, 0), IDENTITY, Vec3(0, 1.2, 0)),
            RigJoint("thigh.L", "hips", Vec3(0.1, 0.95, 0), tilted, Vec3(0.1, 0.5, 0)),
            RigJoint("thigh.R", "hips", Vec3(-0.1, 0.95, 0), tilted.conjugate(), Vec3(-0.1, 0.5, 0))]


class MeshFormatTests(unittest.TestCase):
    def test_round_trip_preserves_every_array(self):
        mesh = small_mesh()
        loaded = read_mesh(write_mesh(mesh), joint_count=2)
        for name in ("positions", "normals", "joints", "weights", "parts", "part_names"):
            self.assertEqual(list(getattr(loaded, name)), list(getattr(mesh, name)), name)
        self.assertEqual(loaded.joint_count, 2)
        self.assertEqual(list(loaded.indices), list(mesh.indices))
        self.assertEqual(loaded.indices.typecode, "H")  # Small meshes use 16-bit indices.
        self.assertEqual((loaded.vertex_count, loaded.triangle_count), (4, 2))

    def test_inconsistent_meshes_are_never_written(self):
        for label, changes in (
                ("index out of range", {"indices": array("I", [0, 1, 4])}),
                ("weights", {"weights": bytes([200, 54, 0, 0] * 4)}),
                ("missing joint", {"joints": bytes([0, 2, 0, 0] * 4)}),
                ("missing part", {"parts": bytes([0, 0, 1, 2])}),
                ("not finite", {"positions": array("f", [math.nan] + [0] * 11)}),
                ("normals", {"normals": array("f", [0, 0, 1] * 3)}),
                ("triangles", {"indices": array("I", [0, 1])})):
            with self.subTest(label), self.assertRaises(MeshFormatError):
                write_mesh(small_mesh(**changes))

    def test_damaged_or_foreign_files_are_rejected(self):
        data = write_mesh(small_mesh())
        header = list(HEADER.unpack_from(data))
        newer = HEADER.pack(header[0], 2, *header[2:]) + data[HEADER.size:]
        for label, damaged in (("magic", b"NOTAMESH" + data[8:]), ("version", newer),
                               ("truncated", data[:-2]), ("short", data[:10]),
                               ("rig mismatch", None)):
            with self.subTest(label), self.assertRaises(MeshFormatError):
                if damaged is None:
                    read_mesh(data, joint_count=3)
                else:
                    read_mesh(damaged)


class RigFormatTests(unittest.TestCase):
    def test_round_trip_builds_a_skeleton_matching_the_file(self):
        text = write_rig("test", "Test Figure", rig_joints(), {"license": "CC0"})
        rig = read_rig(text)
        self.assertEqual((rig.figure, rig.display_name, rig.source), ("test", "Test Figure", {"license": "CC0"}))
        rest = rig.skeleton.transforms(rig.skeleton.rest_pose())
        for joint, transform in zip(rig_joints(), rest):
            self.assertTrue(transform.position.is_close(joint.position, 1e-6))
            self.assertTrue(transform.rotation.is_close(joint.rotation, 1e-7))
        self.assertEqual(rig.skeleton.mirror_indices, (0, 2, 1))
        self.assertEqual(text, write_rig("test", "Test Figure", rig_joints(), {"license": "CC0"}))

    def test_invalid_rigs_are_rejected(self):
        good = json.loads(write_rig("test", "Test", rig_joints(), {}))

        def variant(change):
            data = json.loads(json.dumps(good))
            change(data)
            return json.dumps(data)
        cases = {
            "not json": "{",
            "format": variant(lambda d: d.update(format="other")),
            "version": variant(lambda d: d.update(version=2)),
            "no joints": variant(lambda d: d.update(joints=[])),
            "parent order": variant(lambda d: d["joints"].reverse()),
            "duplicate": variant(lambda d: d["joints"][2].update(name="thigh.L")),
            "unknown parent": variant(lambda d: d["joints"][1].update(parent="spine")),
            "second root": variant(lambda d: d["joints"][1].update(parent=None)),
            "quaternion": variant(lambda d: d["joints"][1].update(rotation=[2, 0, 0, 0])),
            "position": variant(lambda d: d["joints"][1].update(position=[0, "1", 0])),
            "boolean": variant(lambda d: d["joints"][1].update(tail=[True, 0, 0])),
            "display name": variant(lambda d: d.update(display_name="")),
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(RigFormatError):
                read_rig(text)

    def test_unknown_keys_are_ignored_within_a_version(self):
        data = json.loads(write_rig("test", "Test", rig_joints(), {}))
        data["future_option"] = {"anything": 1}
        data["joints"][0]["limits"] = {"x": [-1, 1]}
        self.assertEqual(len(read_rig(json.dumps(data)).joints), 3)


if __name__ == "__main__":
    unittest.main()
