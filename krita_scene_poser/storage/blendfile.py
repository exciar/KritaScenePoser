"""Minimal reader for Blender .blend files.

Two container layouts are read:

- the **classic header**, ``BLENDER`` plus pointer size, endianness and a
  three-digit version (``BLENDER-v304``), with 20- or 24-byte block headers;
- the **large header** that newer Blender versions write
  (``BLENDER17-01v0500``), whose blocks carry 64-bit sizes and counts.

Struct instances are decoded by field name through the file's own SDNA
catalogue, so field offsets are never hard-coded and one reader copes with
many Blender versions. This module never writes files.

Compression: plain and gzip always; zstd only where Python has
``compression.zstd`` (3.14 and newer). Krita ships Python 3.13, so a
zstd-compressed file must be saved again with compression turned off.
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
            # Krita ships Python 3.13, which cannot unpack zstd. Tell the user
            # what to do in Blender rather than what is missing in Python.
            raise BlendFileError(
                "This .blend file is compressed. In Blender, use File > Save As and turn "
                "off Compress, then import the file again.") from error
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
            header, position = self._large_header(data)
        else:
            header, position = self._classic_header(data)
        pointer_format = "Q" if self.pointer_size == 8 else "I"
        self._pointer = struct.Struct(self.endian + pointer_format)
        self.blocks, dna = [], None
        while position + header.size <= len(data):
            fields = header.unpack_from(data, position)
            if self.large:
                code, sdna_index, address, size, count = fields
            else:
                code, size, address, sdna_index, count = fields
            position += header.size
            if code.startswith(b"ENDB"):
                break
            if size < 0 or position + size > len(data):
                raise BlendFileError("The .blend file is truncated inside a block.")
            block = Block(code[:4], address, sdna_index, count,
                          data[position:position + size])
            position += size
            if block.code.startswith(b"DNA1"):
                dna = block
            else:
                self.blocks.append(block)
        if dna is None:
            raise BlendFileError("The file has no SDNA catalogue.")
        self.sdna = SDNA(dna.data, self.pointer_size, self.endian)
        self._by_address = sorted((b.address, b) for b in self.blocks if b.address)
        self._addresses = [address for address, _ in self._by_address]

    def _classic_header(self, data):
        """``BLENDER-v304``: 12-byte header, then 20- or 24-byte block headers."""
        pointer_code, endian_code, version = data[7:8], data[8:9], data[9:12]
        self.pointer_size = {b"_": 4, b"-": 8}.get(pointer_code)
        self.endian = {b"v": "<", b"V": ">"}.get(endian_code)
        if self.pointer_size is None or self.endian is None or not version.isdigit():
            raise BlendFileError("Unrecognized .blend header: {!r}".format(data[:12]))
        self.version = int(version)
        self.large = False
        pointer_format = "Q" if self.pointer_size == 8 else "I"
        return struct.Struct(self.endian + "4si" + pointer_format + "ii"), 12

    def _large_header(self, data):
        """``BLENDER17-01v0500``: a longer header and 64-bit block headers.

        The digits after ``BLENDER`` give the header's own length, so the
        file says where its first block starts. Blocks then carry the code,
        the SDNA index, the old pointer, and 64-bit size and count.
        """
        try:
            header_size = int(data[7:9])
        except ValueError as error:
            raise BlendFileError("Unrecognized .blend header: {!r}".format(data[:20])) from error
        if not 12 <= header_size <= 64 or len(data) < header_size:
            raise BlendFileError("Unrecognized .blend header: {!r}".format(data[:20]))
        body = data[9:header_size]
        pointer_code = b"-" if b"-" in body else b"_" if b"_" in body else None
        endian_code = b"v" if b"v" in body else b"V" if b"V" in body else None
        self.pointer_size = {b"_": 4, b"-": 8}.get(pointer_code)
        self.endian = {b"v": "<", b"V": ">"}.get(endian_code)
        if self.pointer_size is None or self.endian is None:
            raise BlendFileError("Unrecognized .blend header: {!r}".format(data[:header_size]))
        digits = bytes(value for value in body if 48 <= value <= 57)
        self.version = int(digits[-4:] or b"0") if digits else 0
        self.large = True
        pointer_format = "Q" if self.pointer_size == 8 else "I"
        return struct.Struct(self.endian + "4si" + pointer_format + "qq"), header_size

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
