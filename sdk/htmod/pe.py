from dataclasses import dataclass
import struct

from .errors import PEError


def align(value, alignment):
    if alignment <= 0 or alignment & (alignment - 1):
        raise PEError(f"invalid PE alignment {alignment:#x}")
    return (value + alignment - 1) & ~(alignment - 1)


@dataclass(frozen=True)
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    flags: int = 0

    @property
    def virtual_span(self):
        return max(self.virtual_size, self.raw_size)


class PEImage:
    def __init__(self, data, image_base, sections, coff, optional_offset, optional_size,
                 section_table, file_alignment, section_alignment):
        self.data = data
        self.image_base = image_base
        self.sections = tuple(sections)
        self.coff = coff
        self.optional_offset = optional_offset
        self.optional_size = optional_size
        self.section_table = section_table
        self.file_alignment = file_alignment
        self.section_alignment = section_alignment
        self._section_undo = {}

    @classmethod
    def parse(cls, data):
        if not isinstance(data, (bytes, bytearray)):
            raise PEError("PE input must be bytes or bytearray")
        data = data if isinstance(data, bytearray) else bytearray(data)

        def require(offset, size, description):
            if offset < 0 or size < 0 or offset + size > len(data):
                raise PEError(f"truncated PE {description}")

        require(0, 0x40, "DOS header")
        if data[:2] != b"MZ":
            raise PEError("not a PE file (missing MZ)")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        require(pe_offset, 24, "signature/COFF header")
        if data[pe_offset:pe_offset + 4] != b"PE\0\0":
            raise PEError("not a PE file (missing PE signature)")
        coff = pe_offset + 4
        machine, count, _, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", data, coff)
        if machine != 0x14C:
            raise PEError(f"unexpected PE machine {machine:#x} (want i386)")
        if count > 96:
            raise PEError(f"unreasonable PE section count {count}")
        optional = coff + 20
        require(optional, optional_size, "optional header")
        require(optional, 32, "PE32 optional header")
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic == 0x20B:
            raise PEError("64-bit PE is not supported")
        if magic != 0x10B:
            raise PEError(f"unexpected optional header magic {magic:#x} (want PE32)")
        require(optional, 64, "PE32 alignment fields")
        image_base = struct.unpack_from("<I", data, optional + 28)[0]
        section_alignment, file_alignment = struct.unpack_from("<II", data, optional + 32)
        align(0, section_alignment)
        align(0, file_alignment)
        table = optional + optional_size
        require(table, count * 40, "section table")
        sections = []
        for index in range(count):
            offset = table + index * 40
            name = bytes(data[offset:offset + 8]).rstrip(b"\0").decode("ascii", "replace")
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
                "<IIII", data, offset + 8
            )
            flags = struct.unpack_from("<I", data, offset + 36)[0]
            if raw_size:
                require(raw_offset, raw_size, f"section '{name}' raw data")
            sections.append(Section(name, virtual_address, virtual_size, raw_offset, raw_size, flags))
        return cls(data, image_base, sections, coff, optional, optional_size, table,
                   file_alignment, section_alignment)

    def section(self, name):
        for section in self.sections:
            if section.name == name:
                return section
        raise PEError(f"no {name} section")

    def rva_to_offset(self, rva, size=1):
        if rva < 0 or size < 0:
            return None
        for section in self.sections:
            delta = rva - section.virtual_address
            if 0 <= delta <= section.virtual_span and delta + size <= section.raw_size:
                offset = section.raw_offset + delta
                if offset + size <= len(self.data):
                    return offset
        return None

    def va_to_offset(self, va, size=1):
        if va < self.image_base:
            return None
        return self.rva_to_offset(va - self.image_base, size)

    def raw_to_rva(self, offset, size=1):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            return None
        for section in self.sections:
            delta = offset - section.raw_offset
            if 0 <= delta and delta + size <= section.raw_size:
                return section.virtual_address + delta
        return None

    def raw_to_va(self, offset, size=1):
        rva = self.raw_to_rva(offset, size)
        return None if rva is None else self.image_base + rva

    def next_section_layout(self, raw_size):
        if raw_size <= 0:
            raise PEError("new section raw size must be positive")
        last_va_end = 0
        for section in self.sections:
            last_va_end = max(
                last_va_end,
                section.virtual_address + align(section.virtual_span, self.section_alignment),
            )
        return align(last_va_end, self.section_alignment), align(len(self.data), self.file_alignment)

    def append_section(self, name, payload, flags):
        if not name or len(name.encode("ascii")) > 8:
            raise PEError("new section name must contain 1..8 ASCII bytes")
        if any(section.name == name for section in self.sections):
            raise PEError(f"section '{name}' already exists")
        if len(self.sections) >= 16:
            raise PEError("no room for another section table entry")
        payload = bytes(payload)
        virtual_address, raw_offset = self.next_section_layout(len(payload))
        entry = self.section_table + 40 * len(self.sections)
        if entry + 40 > len(self.data):
            raise PEError("section table would overflow file")
        if any(self.data[entry:entry + 40]):
            raise PEError("section table destination is not empty")
        previous_raw_end = max(
            (item.raw_offset + item.raw_size for item in self.sections),
            default=0,
        )
        if previous_raw_end > len(self.data):
            raise PEError("original EOF precedes section data; refusing append")
        undo = (
            len(self.data),
            struct.unpack_from("<I", self.data, self.optional_offset + 56)[0],
            bytes(self.data[previous_raw_end:]),
        )
        raw_size = align(len(payload), self.file_alignment)
        if raw_offset > len(self.data):
            self.data += bytes(raw_offset - len(self.data))
        self.data[previous_raw_end:raw_offset] = bytes(raw_offset - previous_raw_end)
        self.data += payload + bytes(raw_size - len(payload))
        struct.pack_into(
            "<8sIIIIIIHHI", self.data, entry, name.encode("ascii").ljust(8, b"\0"),
            len(payload), virtual_address, raw_size, raw_offset, 0, 0, 0, 0, flags,
        )
        struct.pack_into("<H", self.data, self.coff + 2, len(self.sections) + 1)
        image_end = virtual_address + align(len(payload), self.section_alignment)
        struct.pack_into("<I", self.data, self.optional_offset + 56, align(image_end, 0x1000))
        section = Section(name, virtual_address, len(payload), raw_offset, raw_size, flags)
        self.sections += (section,)
        self._section_undo[name] = undo
        return section

    def remove_section(self, name, original_eof=None, original_image_size=None):
        if not self.sections or self.sections[-1].name != name:
            raise PEError(f"last section is not '{name}'; refusing removal")
        index = len(self.sections) - 1
        section = self.sections[-1]
        if len(self.data) != section.raw_offset + section.raw_size:
            raise PEError("later file data follows appended section; refusing truncation")
        previous_raw_end = max(
            (item.raw_offset + item.raw_size for item in self.sections[:-1]),
            default=0,
        )
        if previous_raw_end > section.raw_offset or any(
            self.data[previous_raw_end:section.raw_offset]
        ):
            raise PEError("foreign data in appended-section padding; refusing truncation")

        undo = self._section_undo.get(name)
        if undo is not None:
            original_eof, original_image_size, original_tail = undo
            if section.raw_offset != align(original_eof, self.file_alignment):
                raise PEError("appended section no longer matches original EOF; refusing")
            if original_eof != previous_raw_end + len(original_tail):
                raise PEError("saved section undo metadata is inconsistent")
        else:
            if original_eof is None or original_image_size is None:
                raise PEError("original section metadata required after reparse; refusing removal")
            if previous_raw_end != original_eof:
                raise PEError("cannot restore unrecorded original tail data; refusing removal")
            if section.raw_offset != align(original_eof, self.file_alignment):
                raise PEError("appended section no longer matches original EOF; refusing")
            original_tail = b""

        expected_image = align(
            section.virtual_address + align(section.virtual_size, self.section_alignment),
            0x1000,
        )
        current_image = struct.unpack_from("<I", self.data, self.optional_offset + 56)[0]
        entry = self.section_table + 40 * index
        self.data[entry:entry + 40] = bytes(40)
        struct.pack_into("<H", self.data, self.coff + 2, index)
        if current_image == expected_image:
            struct.pack_into("<I", self.data, self.optional_offset + 56, original_image_size)
        del self.data[section.raw_offset:]
        if original_tail:
            self.data[previous_raw_end:previous_raw_end + len(original_tail)] = original_tail
        del self.data[original_eof:]
        self.sections = self.sections[:-1]
        self._section_undo.pop(name, None)

    def data_directory(self, index):
        end = 96 + (index + 1) * 8
        if index < 0 or end > self.optional_size:
            raise PEError(f"data directory {index} is outside the optional header")
        return struct.unpack_from("<II", self.data, self.optional_offset + 96 + index * 8)

    def _set_directory_size(self, index, size):
        struct.pack_into("<I", self.data, self.optional_offset + 96 + index * 8 + 4, size)

    def append_highlow_relocations(self, page_rva, offsets):
        offsets = tuple(offsets)
        if not offsets or any(offset < 0 or offset > 0xFFF for offset in offsets):
            raise PEError("invalid HIGHLOW relocation offset list")
        size = 8 + 2 * len(offsets)
        directory_rva, directory_size = self.data_directory(5)
        block = self.rva_to_offset(directory_rva + directory_size, size)
        if block is None:
            raise PEError("no mapped room for HIGHLOW relocation block")
        if any(self.data[block:block + size]):
            raise PEError("relocation destination is not empty or has later data")
        entries = [0x3000 | offset for offset in offsets]
        struct.pack_into("<II" + "H" * len(entries), self.data, block,
                         page_rva, size, *entries)
        self._set_directory_size(5, directory_size + size)

    def remove_highlow_relocations(self, page_rva, offsets):
        offsets = tuple(offsets)
        size = 8 + 2 * len(offsets)
        directory_rva, directory_size = self.data_directory(5)
        if directory_size < size:
            raise PEError("relocation directory is smaller than owned block")
        block = self.rva_to_offset(directory_rva + directory_size - size, size)
        if block is None:
            raise PEError("owned relocation block is not mapped")
        entries = [0x3000 | offset for offset in offsets]
        expected = struct.pack("<II" + "H" * len(entries), page_rva, size, *entries)
        if bytes(self.data[block:block + size]) != expected:
            raise PEError("final relocation block is foreign or has later data; refusing")
        self.data[block:block + size] = bytes(size)
        self._set_directory_size(5, directory_size - size)
