import struct
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod.errors import PEError, SignatureError
from htmod.pe import PEImage
from htmod.patch import Signature, find_unique


def make_pe(sections=None, image_base=0x10000000):
    sections = sections or [
        (".text", 0x1000, 0x180, 0x200, 0x200),
        (".rdata", 0x2000, 0x180, 0x400, 0x100),
    ]
    data = bytearray(0x200 + sum(section[4] for section in sections))
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x14C, len(sections), 0, 0, 0, 0xE0, 0)
    struct.pack_into("<H", data, 0x98, 0x10B)
    struct.pack_into("<I", data, 0x98 + 28, image_base)
    struct.pack_into("<II", data, 0x98 + 32, 0x1000, 0x200)
    table = 0x98 + 0xE0
    for index, (name, vaddr, vsize, rawoff, rawsize) in enumerate(sections):
        offset = table + index * 40
        data[offset:offset + 8] = name.encode("ascii").ljust(8, b"\0")
        struct.pack_into("<IIII", data, offset + 8, vsize, vaddr, rawsize, rawoff)
    return data


class PETests(unittest.TestCase):
    def test_parse_and_raw_rva_va_mapping(self):
        image = PEImage.parse(make_pe())
        self.assertEqual(image.image_base, 0x10000000)
        self.assertEqual(image.section(".text").raw_offset, 0x200)
        self.assertEqual(image.rva_to_offset(0x1010), 0x210)
        self.assertEqual(image.va_to_offset(0x10001010), 0x210)
        self.assertEqual(image.raw_to_rva(0x210), 0x1010)
        self.assertEqual(image.raw_to_va(0x210), 0x10001010)

    def test_virtual_tail_and_cross_section_reads_are_not_file_mapped(self):
        image = PEImage.parse(make_pe())
        self.assertIsNone(image.rva_to_offset(0x2180))
        self.assertIsNone(image.rva_to_offset(0x11FF, size=2))
        self.assertIsNone(image.raw_to_rva(0x3FF, size=2))
        self.assertIsNone(image.va_to_offset(0x0FFFFFFF))

    def test_malformed_headers_raise_bounded_pe_errors(self):
        cases = []
        cases.append(b"")
        cases.append(b"MZ" + bytes(100))
        for mutation in ("pe_offset", "signature", "coff", "optional", "magic", "sections"):
            data = make_pe()
            if mutation == "pe_offset":
                struct.pack_into("<I", data, 0x3C, len(data) - 2)
            elif mutation == "signature":
                data[0x80:0x84] = b"NOPE"
            elif mutation == "coff":
                struct.pack_into("<H", data, 0x84 + 2, 0xFFFF)
            elif mutation == "optional":
                struct.pack_into("<H", data, 0x84 + 16, 0xFFFF)
            elif mutation == "magic":
                struct.pack_into("<H", data, 0x98, 0x20B)
            else:
                struct.pack_into("<I", data, 0x178 + 16, 0x200)
                struct.pack_into("<I", data, 0x178 + 20, len(data) - 1)
            cases.append(data)
        for data in cases:
            with self.subTest(size=len(data)):
                with self.assertRaises(PEError):
                    PEImage.parse(data)

    def test_mapping_rejects_negative_and_oversized_ranges(self):
        image = PEImage.parse(make_pe())
        for function, value in (
            (image.rva_to_offset, -1),
            (image.va_to_offset, -1),
            (image.raw_to_rva, -1),
        ):
            with self.subTest(function=function.__name__):
                self.assertIsNone(function(value))
        self.assertIsNone(image.rva_to_offset(0x1000, size=0x1000))


class SignatureTests(unittest.TestCase):
    def test_unique_bounded_signature_supports_wildcards(self):
        data = b"xxABC??DEFyyABC12DEFzz"
        signature = Signature(b"ABC??DEF", b"\xff" * 3 + b"\0" * 2 + b"\xff" * 3)
        self.assertEqual(find_unique(data, signature, 2, 11), 2)

    def test_ambiguous_and_missing_signatures_are_refused(self):
        with self.assertRaisesRegex(SignatureError, "2 matches"):
            find_unique(b"ABCABC", Signature(b"ABC"))
        with self.assertRaisesRegex(SignatureError, "no matches"):
            find_unique(b"ABC", Signature(b"XYZ"))

    def test_signature_bounds_and_mask_are_validated(self):
        with self.assertRaises(SignatureError):
            Signature(b"ABC", b"\xff")
        with self.assertRaises(SignatureError):
            find_unique(b"ABC", Signature(b""))
        with self.assertRaises(SignatureError):
            find_unique(b"ABC", Signature(b"ABC"), -1, 3)
        with self.assertRaises(SignatureError):
            find_unique(b"ABC", Signature(b"ABC"), 0, 4)
        with self.assertRaises(SignatureError):
            find_unique(b"ABC", Signature(b"ABC"), 2, 1)


if __name__ == "__main__":
    unittest.main()
