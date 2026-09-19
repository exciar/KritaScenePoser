"""Minimal reader for Blender .blend files. Development tool; never shipped.

Supports the classic header (``BLENDER`` + pointer size + endianness + a
three-digit version, e.g. ``BLENDER-v304``), plain or zstd/gzip compressed.
Decodes struct instances by field name using the file's own SDNA catalogue,
so field offsets are never hard-coded. It reads what KSP's asset compiler
needs; it is not a general Blender API and never writes files.
"""

import bisect
import gzip
import re
import struct

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
GZIP_MAGIC = b"\x1f\x8b"
PRIMITIVES = {
    "char": "b", "uchar": "B", "int8_t": "b", "uint8_t": "B",
    "short": "h", "ushort": "H", "int16_t": "h", "uint16_t": "H",
    "int": "i", "uint": "I", "int32_t": "i", "uint32_t": "I",
    "int64_t": "q", "uint64_t": "Q", "long": "i", "ulong": "I",
    "float": "f", "double": "d",
}
FIELD_NAME = re.compile(r"^(\(?\**)([A-Za-z0-9_]+)\)?(\(\))?((?:\[\d+\])*)$")


class BlendFileError(ValueError):
    """The file is not a .blend file this reader supports."""


def _decompress(raw):
    if raw.startswith(ZSTD_MAGIC):
        try:
            from compression import zstd  # Python 3.14+ standard library.
        except ImportError as error:
            raise BlendFileError("This .blend file is zstd-compressed; use Python 3.14 or newer.") from error
        return zstd.decompress(raw)
    if raw.startswith(GZIP_MAGIC):
        return gzip.decompress(raw)
    return raw


class Field:
    __slots__ = ("name", "type", "pointer", "dims", "offset", "size", "function")

    def __init__(self, type_name, declaration, offset, sdna):
        match = FIELD_NAME.match(declaration)
        if not match:
            raise BlendFileError("Unsupported SDNA field declaration: " + declaration)
        stars, self.name, function, dims = match.groups()
        self.type = type_name
        self.pointer = "*" in stars
        self.function = bool(function)
        self.dims = tuple(int(n) for n in re.findall(r"\[(\d+)\]", dims))
        count = 1
        for n in self.dims:
            count *= n
        element = sdna.pointer_size if self.pointer or self.function else sdna.type_length(type_name)
        self.offset, self.size = offset, element * count


class Struct:
    def __init__(self, name, fields, size):
        self.name, self.size = name, size
        self.fields = {field.name: field for field in fields}


class SDNA:
    def __init__(self, data, pointer_size, endian):
        self.pointer_size, self.endian = pointer_size, endian
        position = 0

        def expect(tag):
            nonlocal position
            if data[position:position + 4] != tag:
                raise BlendFileError("Malformed SDNA: expected {!r}".format(tag))
            position += 4

        def count():
            nonlocal position
            (value,) = struct.unpack_from(endian + "i", data, position)
            position += 4
            return value

        def strings(total):
            nonlocal position
            result = []
            for _ in range(total):
                end = data.index(b"\0", position)
                result.append(data[position:end].decode("ascii"))
                position = end + 1
            return result

        def align():
            nonlocal position
            position = (position + 3) & ~3

        expect(b"SDNA")
        expect(b"NAME")
        names = strings(count())
        align()
        expect(b"TYPE")
        self.types = strings(count())
        align()
        expect(b"TLEN")
        self.lengths = list(struct.unpack_from(endian + "%dh" % len(self.types), data, position))
        position += 2 * len(self.types)
        align()
        expect(b"STRC")
        self.structs = []
        self.by_name = {}
        for _ in range(count()):
            type_index, field_count = struct.unpack_from(endian + "hh", data, position)
            position += 4
            fields, offset = [], 0
            for _ in range(field_count):
                field_type, field_name = struct.unpack_from(endian + "hh", data, position)
                position += 4
                field = Field(self.types[field_type], names[field_name], offset, self)
                fields.append(field)
                offset += field.size
            definition = Struct(self.types[type_index], fields, self.lengths[type_index])
            self.structs.append(definition)
            self.by_name[definition.name] = definition

    def type_length(self, name):
        return self.lengths[self.types.index(name)]


class Block:
    __slots__ = ("code", "address", "sdna_index", "count", "data")

    def __init__(self, code, address, sdna_index, count, data):
        self.code, self.address, self.sdna_index = code, address, sdna_index
        self.count, self.data = count, data


class View:
    """One struct instance; ``view["field"]`` decodes a field by name."""

    __slots__ = ("file", "struct", "data", "offset")

    def __init__(self, blend, definition, data, offset=0):
        self.file, self.struct, self.data, self.offset = blend, definition, data, offset

    def __contains__(self, name):
        return name in self.struct.fields

    def __getitem__(self, name):
        field = self.struct.fields.get(name)
        if field is None:
            raise KeyError("{} has no field {!r}".format(self.struct.name, name))
        return self.file._decode(field, self.data, self.offset + field.offset)

    def get(self, name, default=None):
        return self[name] if name in self.struct.fields else default

    def string(self, name):
        raw = self[name]
        return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace")

    def id_name(self):
        """Datablock name without its two-letter type code."""
        return self["id"].string("name")[2:]

    def deref(self, name):
        """Follow a pointer field to the struct it addresses, or None."""
        return self.file.view(self[name])


class BlendFile:
    def __init__(self, path_or_bytes):
        raw = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
        data = _decompress(bytes(raw))
        if not data.startswith(b"BLENDER"):
            raise BlendFileError("Not a Blender file.")
        if data[7:9].isdigit():
            raise BlendFileError(
                "This .blend uses the newer large-header format (Blender 4.x/5.x "
                "saves). Supported: files like BLENDER-v304.")
        pointer_code, endian_code, version = data[7:8], data[8:9], data[9:12]
        self.pointer_size = {b"_": 4, b"-": 8}.get(pointer_code)
        self.endian = {b"v": "<", b"V": ">"}.get(endian_code)
        if self.pointer_size is None or self.endian is None or not version.isdigit():
            raise BlendFileError("Unrecognized .blend header: {!r}".format(data[:12]))
        self.version = int(version)
        pointer_format = "Q" if self.pointer_size == 8 else "I"
        header = struct.Struct(self.endian + "4si" + pointer_format + "ii")
        self._pointer = struct.Struct(self.endian + pointer_format)
        self.blocks, position, dna = [], 12, None
        while position + header.size <= len(data):
            code, size, address, sdna_index, count = header.unpack_from(data, position)
            position += header.size
            if code == b"ENDB":
                break
            block = Block(code, address, sdna_index, count, data[position:position + size])
            position += size
            if code == b"DNA1":
                dna = block
            else:
                self.blocks.append(block)
        if dna is None:
            raise BlendFileError("The file has no SDNA catalogue.")
        self.sdna = SDNA(dna.data, self.pointer_size, self.endian)
        self._by_address = sorted((b.address, b) for b in self.blocks if b.address)
        self._addresses = [address for address, _ in self._by_address]

    def block_at(self, address):
        """The block containing ``address`` and the offset within it, or (None, 0)."""
        if not address:
            return None, 0
        index = bisect.bisect_right(self._addresses, address) - 1
        if index >= 0:
            start, block = self._by_address[index]
            if address - start < max(len(block.data), 1):
                return block, address - start
        return None, 0

    def struct_of(self, block):
        return self.sdna.structs[block.sdna_index]

    def view(self, address):
        block, offset = self.block_at(address)
        if block is None:
            return None
        return View(self, self.struct_of(block), block.data, offset)

    def views(self, address):
        """Every struct instance in the block at ``address`` (an array)."""
        block, _ = self.block_at(address)
        if block is None:
            return []
        definition = self.struct_of(block)
        return [View(self, definition, block.data, i * definition.size) for i in range(block.count)]

    def listbase(self, listbase_view):
        """Walk a ListBase (``first``/``next`` linked list).

        Every list element begins with its ``next`` pointer, sometimes inside
        an embedded header such as ``ModifierData``, so it is read at offset 0.
        """
        seen, address = set(), listbase_view["first"]
        while address and address not in seen:
            seen.add(address)
            item = self.view(address)
            if item is None:
                return
            yield item
            address = self._pointer.unpack_from(item.data, item.offset)[0]

    def datablocks(self, code):
        """ID datablocks by two-letter code, e.g. b"OB", b"ME", b"AR"."""
        code = code.ljust(4, b"\0")
        for block in self.blocks:
            if block.code == code:
                yield View(self, self.struct_of(block), block.data)

    def raw(self, address, element_format):
        """Unpack a raw array block (e.g. floats) addressed by ``address``."""
        block, offset = self.block_at(address)
        if block is None:
            return []
        size = struct.calcsize(self.endian + element_format)
        count = (len(block.data) - offset) // size
        return list(struct.unpack_from(self.endian + element_format * count, block.data, offset))

    def _decode(self, field, data, offset):
        if field.pointer or field.function:
            count = 1
            for n in field.dims:
                count *= n
            values = [self._pointer.unpack_from(data, offset + i * self.pointer_size)[0]
                      for i in range(count)]
            return values[0] if not field.dims else values
        code = PRIMITIVES.get(field.type)
        if code is None:
            definition = self.sdna.by_name.get(field.type)
            if definition is None:
                return bytes(data[offset:offset + field.size])
            if not field.dims:
                return View(self, definition, data, offset)
            count = field.size // definition.size
            return [View(self, definition, data, offset + i * definition.size) for i in range(count)]
        if not field.dims:
            return struct.unpack_from(self.endian + code, data, offset)[0]
        if field.type == "char" and len(field.dims) == 1:
            return bytes(data[offset:offset + field.size])
        count = field.size // struct.calcsize(code)
        values = list(struct.unpack_from(self.endian + code * count, data, offset))
        for dimension in reversed(field.dims[1:]):
            values = [values[i:i + dimension] for i in range(0, len(values), dimension)]
        return values
