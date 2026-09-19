"""The .blend reader against a small synthetic file built here."""

import gzip
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from blendfile import BlendFile, BlendFileError  # noqa: E402


def sdna(names, types, lengths, structs):
    def strings(values):
        data = b"".join(v.encode("ascii") + b"\0" for v in values)
        return data + b"\0" * (-len(data) % 4)
    body = b"SDNA" + b"NAME" + struct.pack("<i", len(names)) + strings(names)
    body += b"TYPE" + struct.pack("<i", len(types)) + strings(types)
    tlen = struct.pack("<%dh" % len(lengths), *lengths)
    body += b"TLEN" + tlen + b"\0" * (-len(tlen) % 4)
    body += b"STRC" + struct.pack("<i", len(structs))
    for type_index, fields in structs:
        body += struct.pack("<hh", type_index, len(fields))
        for field_type, field_name in fields:
            body += struct.pack("<hh", field_type, field_name)
    return body


def block(code, address, sdna_index, count, data):
    return struct.pack("<4siQii", code, len(data), address, sdna_index, count) + data


# Types: 0 char, 1 int, 2 float, 3 ListBase, 4 Header, 5 Item, 6 Holder.
TYPES = ["char", "int", "float", "ListBase", "Header", "Item", "Holder"]
LENGTHS = [1, 4, 4, 16, 24, 8 + 24 + 12 + 16 + 4, 16]
NAMES = ["*first", "*last", "*next", "*prev", "name[8]", "header", "co[3]",
         "mat[2][2]", "count", "items"]
STRUCTS = [
    (3, [(1, 0), (1, 1)]),  # ListBase {*first, *last}
    (4, [(1, 2), (1, 3), (0, 4)]),  # Header {*next, *prev, char name[8]}
    (5, [(0, 4), (4, 5), (2, 6), (2, 7), (1, 8)]),  # Item {name[8], Header header, co[3], mat[2][2], count}
    (6, [(3, 9)]),  # Holder {ListBase items}
]


def item(name, next_address, co, mat, count):
    header = struct.pack("<QQ8s", next_address, 0, b"hdr")
    return (name.encode().ljust(8, b"\0") + header + struct.pack("<3f", *co)
            + struct.pack("<4f", *mat) + struct.pack("<i", count))


def synthetic_blend():
    """Holder -> linked list of two Items; the Item's 'next' sits inside its header
    at offset 8, so the file deliberately places a plain Header list too."""
    first, second, holder = 0x1000, 0x2000, 0x3000
    header_a, header_b = 0x4000, 0x5000
    data = b"BLENDER-v304"
    data += block(b"DATA", first, 2, 1, item("one", second, (1, 2, 3), (1, 2, 3, 4), 7))
    data += block(b"DATA", second, 2, 1, item("two", 0, (4, 5, 6), (5, 6, 7, 8), 9))
    data += block(b"HO\0\0", holder, 3, 1, struct.pack("<QQ", header_a, header_b))
    data += block(b"DATA", header_a, 1, 1, struct.pack("<QQ8s", header_b, 0, b"a"))
    data += block(b"DATA", header_b, 1, 1, struct.pack("<QQ8s", 0, header_a, b"b"))
    data += block(b"DNA1", 0, 0, 1, sdna(NAMES, TYPES, LENGTHS, STRUCTS))
    data += block(b"ENDB", 0, 0, 0, b"")
    return data, (first, second, holder)


class BlendFileTests(unittest.TestCase):
    def setUp(self):
        self.data, (self.first, self.second, self.holder) = synthetic_blend()
        self.blend = BlendFile(self.data)

    def test_header_and_catalogue(self):
        self.assertEqual((self.blend.version, self.blend.pointer_size), (304, 8))
        item = self.blend.sdna.by_name["Item"]
        self.assertEqual([item.fields[n].offset for n in ("name", "header", "co", "mat", "count")],
                         [0, 8, 32, 44, 60])
        self.assertTrue(self.blend.sdna.by_name["Header"].fields["next"].pointer)

    def test_fields_decode_by_name_including_nested_and_multidimensional(self):
        one = self.blend.view(self.first)
        self.assertEqual(one.string("name"), "one")
        self.assertEqual(one["co"], [1.0, 2.0, 3.0])
        self.assertEqual(one["mat"], [[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(one["count"], 7)
        self.assertEqual(one["header"].string("name"), "hdr")
        self.assertEqual(one["header"]["next"], self.second)
        self.assertEqual(self.blend.view(one["header"]["next"])["count"], 9)
        with self.assertRaisesRegex(KeyError, "no field"):
            one["missing"]

    def test_listbase_walk_and_block_lookup(self):
        holder = next(self.blend.datablocks(b"HO"))
        names = [h.string("name") for h in self.blend.listbase(holder["items"])]
        self.assertEqual(names, ["a", "b"])
        self.assertIsNone(self.blend.view(0))
        block, offset = self.blend.block_at(self.first + 12)
        self.assertEqual((block.address, offset), (self.first, 12))
        self.assertEqual(self.blend.raw(self.first + 32, "f")[:3], [1.0, 2.0, 3.0])

    def test_compressed_files_and_rejections(self):
        self.assertEqual(BlendFile(gzip.compress(self.data)).version, 304)
        for bad, message in ((b"NOTBLEND" + self.data[8:], "Not a Blender"),
                             (b"BLENDER17-01v0500" + self.data[12:], "newer large-header"),
                             (b"BLENDER?v304" + self.data[12:], "Unrecognized")):
            with self.subTest(message=message), self.assertRaisesRegex(BlendFileError, message):
                BlendFile(bad)
        without_dna = self.data.split(b"DNA1")[0] + block(b"ENDB", 0, 0, 0, b"")
        with self.assertRaisesRegex(BlendFileError, "SDNA"):
            BlendFile(without_dna)


if __name__ == "__main__":
    unittest.main()
