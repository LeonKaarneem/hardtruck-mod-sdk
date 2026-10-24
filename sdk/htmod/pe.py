from dataclasses import dataclass
import struct

from .errors import PEError


@dataclass(frozen=True)
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int

    @property
    def virtual_span(self):
        return max(self.virtual_size, self.raw_size)


class PEImage:
    def __init__(self, data, image_base, sections):
        self.data = data
        self.image_base = image_base
        self.sections = tuple(sections)

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
        image_base = struct.unpack_from("<I", data, optional + 28)[0]
        table = optional + optional_size
        require(table, count * 40, "section table")
        sections = []
        for index in range(count):
            offset = table + index * 40
            name = bytes(data[offset:offset + 8]).rstrip(b"\0").decode("ascii", "replace")
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
                "<IIII", data, offset + 8
            )
            if raw_size:
                require(raw_offset, raw_size, f"section '{name}' raw data")
            sections.append(Section(name, virtual_address, virtual_size, raw_offset, raw_size))
        return cls(data, image_base, sections)

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
