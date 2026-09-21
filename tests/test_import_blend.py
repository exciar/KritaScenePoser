"""Importing .blend files: the reader, both container layouts, and conversion."""

import os
import struct
import unittest

from krita_scene_poser.storage.blendfile import BlendFile, BlendFileError, _decompress
from krita_scene_poser.storage.import_blend import armature_names, read_blend
from krita_scene_poser.storage.import_figure import FigureImportError, convert
from krita_scene_poser.storage.mesh_io import validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "bodychan-bodykun.blend")
HAS_SOURCE = os.path.isfile(SOURCE)


def to_large_header(data):
    """Rewrite a classic .blend as the large-header layout newer Blender writes.

    The bytes Blender 5 writes are not available here, so the same file is
    re-encoded into that layout: a longer header, and block headers carrying
    the code, the SDNA index, the old pointer, and 64-bit size and count. It
    proves the reader's second path against real Blender data.
    """
    if not data.startswith(b"BLENDER"):
        raise ValueError("not a classic .blend")
    pointer_code, endian_code = data[7:8], data[8:9]
    pointer = "Q" if pointer_code == b"-" else "I"
    old = struct.Struct("<4si" + pointer + "ii")
    new = struct.Struct("<4si" + pointer + "qq")
    header = b"BLENDER17-01" + endian_code + b"0500"  # 17 bytes, as Blender 5 writes.
    assert len(header) == 17
    out = bytearray(header)
    position = 12
    while position + old.size <= len(data):
        code, size, address, sdna_index, count = old.unpack_from(data, position)
        position += old.size
        payload = data[position:position + size]
        position += size
        out += new.pack(code, sdna_index, address, len(payload), count)
        out += payload
        if code.startswith(b"ENDB"):
            break
    return bytes(out)


@unittest.skipUnless(HAS_SOURCE, "the source .blend is not in this checkout")
class BlendReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SOURCE, "rb") as handle:
            cls.raw = handle.read()
        cls.plain = _decompress(cls.raw)

    def test_the_classic_layout_is_read(self):
        blend = BlendFile(self.plain)
        self.assertFalse(blend.large)
        self.assertEqual(blend.pointer_size, 8)
        self.assertGreater(len(blend.blocks), 1000)
        self.assertGreater(len(list(blend.datablocks(b"OB"))), 10)

    def test_the_large_header_layout_reads_the_same_file(self):
        classic = BlendFile(self.plain)
        large = BlendFile(to_large_header(self.plain))
        self.assertTrue(large.large)
        self.assertEqual(large.pointer_size, classic.pointer_size)
        self.assertEqual(large.version, 500)
        self.assertEqual(len(large.blocks), len(classic.blocks))
        for first, second in zip(classic.blocks[:200], large.blocks[:200]):
            self.assertEqual((first.code, first.address, first.sdna_index, first.count),
                             (second.code, second.address, second.sdna_index, second.count))
            self.assertEqual(first.data, second.data)
        self.assertEqual([item.id_name() for item in classic.datablocks(b"OB")],
                         [item.id_name() for item in large.datablocks(b"OB")])

    def test_armatures_are_listed_most_bones_first(self):
        names = armature_names(self.plain)
        self.assertIn("rig", names)
        self.assertIn("body_kun_rig", names)

    def test_a_rigify_figure_imports_and_poses(self):
        source = read_blend(self.plain, "bodychan-bodykun.blend", armature="rig")
        self.assertEqual(source.up, "Z")
        self.assertGreater(len(source.bones), 50)
        self.assertGreater(len(source.meshes), 5)
        rig, mesh, report = convert(source, "imported", "Imported")
        validate(mesh)
        self.assertEqual(report["scheme"], "Rigify (DEF- bones)")
        self.assertEqual(report["joints"], 52)
        self.assertAlmostEqual(report["height"], 1.75, places=2)
        self.assertAlmostEqual(min(mesh.positions[1::3]), 0.0, places=6)
        self.assertNotEqual(report["rescaled"], 1.0)  # The file is in its own units.
        skeleton = rig.skeleton
        # Blender is Z-up and faces -Y; the figure must end up Y-up, facing +Z.
        head = skeleton.rest_transforms[skeleton.index("head")].position
        self.assertGreater(head.y, 1.3)
        toe = skeleton.rest_transforms[skeleton.index("toe.L")].position
        foot = skeleton.rest_transforms[skeleton.index("foot.L")].position
        self.assertGreater(toe.z, foot.z)
        hand = skeleton.rest_transforms[skeleton.index("hand.L")].position
        self.assertGreater(hand.x, 0.2)

    def test_the_second_armature_gives_the_other_figure(self):
        source = read_blend(self.plain, "b.blend", armature="body_kun_rig")
        rig, mesh, report = convert(source, "kun", "Kun")
        self.assertEqual(report["joints"], 52)
        self.assertGreater(report["vertices"], 1000)
        self.assertEqual(rig.skeleton.joints[0].name, "hips")

    def test_a_missing_armature_is_named(self):
        with self.assertRaisesRegex(FigureImportError, "no armature named"):
            read_blend(self.plain, "b.blend", armature="not_here")

    def test_damaged_files_are_refused(self):
        cases = {
            "empty": b"",
            "not blender": b"just some bytes that are not a blend file at all",
            "truncated": self.plain[:4096],
            "bad header": b"BLENDER?v304" + self.plain[12:20000],
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises((FigureImportError, BlendFileError)):
                    read_blend(data, label)


class LargeHeaderTests(unittest.TestCase):
    def test_a_damaged_large_header_is_refused(self):
        for data in (b"BLENDER99", b"BLENDER17", b"BLENDER17-01x0500" + b"\0" * 32,
                     b"BLENDER00-01v0500"):
            with self.subTest(data=data[:20]):
                with self.assertRaises(BlendFileError):
                    BlendFile(data)


if __name__ == "__main__":
    unittest.main()
