import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "tests"))

from htmod.errors import PEError, StateError
from htmod.manifest import load_release
from htmod.patch import PatchRunner, PatchState
from htmod.pe import PEImage
from htmod.runner import load_binary_mods
from test_pe import make_pe

BASE_VA = 0x100FB000
IAT_CALL = bytes.fromhex("ff 15 18 b2 0a 10")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pristine_game():
    candidates = []
    if os.environ.get("HTMOD_TEST_BASE"):
        candidates.append(Path(os.environ["HTMOD_TEST_BASE"]) / "game/sys/game.dll")
    candidates.append(ROOT / "test-base/game/sys/game.dll")
    if ROOT.parent.name == ".worktrees":
        candidates.append(ROOT.parent.parent / "test-base/game/sys/game.dll")
    path = next((path for path in candidates if path.is_file()), None)
    if path is None:
        raise unittest.SkipTest("pinned test-base game.dll is unavailable")
    return path.read_bytes()


def camera_module():
    path = ROOT / "modpack/mods/camera/patch.py"
    spec = importlib.util.spec_from_file_location("camera_sdk_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def camera_mod():
    return load_binary_mods(load_release(ROOT), ["camera"])[0]


def directory(image, index):
    return struct.unpack_from("<II", image.data, image.optional_offset + 96 + index * 8)


class CameraStructureTests(unittest.TestCase):
    def test_helper_preserves_rotation_clamps_resume_and_relocation_contract(self):
        module = camera_module()
        blob, labels = module.build_helpers(BASE_VA)
        cab = blob[:labels["htcam_orbit"] - BASE_VA]
        self.assertEqual(cab.count(IAT_CALL), 2)
        first = cab.index(IAT_CALL)
        second = cab.index(IAT_CALL, first + 1)
        pitch = bytes.fromhex("89 84 24 10 02 00 00")
        yaw = bytes.fromhex("89 8c 24 14 02 00 00")
        self.assertLess(cab.index(pitch), first)
        self.assertLess(first, cab.index(yaw))
        self.assertLess(cab.index(yaw), second)
        rel = struct.unpack_from("<i", cab, 2)[0]
        self.assertEqual(BASE_VA + 1 + 5 + rel, labels["htcam_clamp35"])
        self.assertEqual(
            cab[-5:],
            b"\xe9" + struct.pack("<i", module.CAB_RESUME - (BASE_VA + len(cab) - 5 + 5)),
        )
        self.assertEqual(module.CAB_RESUME, 0x10033D9D)
        self.assertEqual(len(labels["cab_rotate_relocs"]), 2)
        for offset in labels["cab_rotate_relocs"]:
            self.assertEqual(cab[offset - 2:offset + 4], IAT_CALL)

        c35 = blob[labels["htcam_clamp35"] - BASE_VA:labels["htcam_clamp40"] - BASE_VA]
        c40 = blob[labels["htcam_clamp40"] - BASE_VA:labels["htcam_rst_cam2"] - BASE_VA]
        negative_guard = b"\xb9" + struct.pack("<I", module.CAB_PITCH_LO) + b"\x39\xc8\x76\x02"
        self.assertIn(negative_guard, c35)
        self.assertEqual(c40, bytes.fromhex(
            "8b86d0030000b90000204239c87e0389c8c3"
            "b9000020c139c87d0289c8c3"
        ))
        self.assertEqual(module.ORBIT_POS, 0x33708)
        self.assertEqual(module.ORIENT_POS, 0x3386F)
        self.assertEqual(module.ORBIT_POS_RESUME, 0x1003370F)
        self.assertEqual(module.ORIENT_POS_RESUME, 0x10033876)


class PESectionTests(unittest.TestCase):
    def test_unaligned_eof_original_image_size_and_tail_round_trip(self):
        data = make_pe() + b"xyz"
        struct.pack_into("<I", data, 0x98 + 56, 0x12345678)
        original = data[:]
        image = PEImage.parse(data)
        section = image.append_section(".odd", b"abcde", 0xE0000020)
        self.assertEqual(section.virtual_size, 5)
        self.assertEqual(section.raw_size, 0x200)
        self.assertEqual(section.raw_offset, 0x600)
        self.assertEqual(len(image.data), 0x800)
        self.assertEqual(image.data[section.raw_offset + 5:section.raw_offset + 0x200], bytes(0x200 - 5))
        self.assertEqual(struct.unpack_from("<I", image.data, image.optional_offset + 56)[0], 0x4000)

        reparsed = PEImage.parse(image.data)
        before = reparsed.data[:]
        with self.assertRaises(PEError):
            reparsed.remove_section(".odd")
        self.assertEqual(reparsed.data, before)

        image.remove_section(".odd")
        self.assertEqual(image.data, original)
        self.assertEqual(struct.unpack_from("<I", image.data, image.optional_offset + 56)[0], 0x12345678)

    def test_size_of_image_uses_section_alignment_and_restores_original(self):
        data = make_pe()
        struct.pack_into("<II", data, 0x98 + 32, 0x200, 0x200)
        original_size = 0x3456
        struct.pack_into("<I", data, 0x98 + 56, original_size)
        image = PEImage.parse(data)
        image.append_section(".odd", b"abcde", 0xE0000020)
        self.assertEqual(struct.unpack_from("<I", image.data, image.optional_offset + 56)[0], 0x2400)
        image.remove_section(".odd")
        self.assertEqual(struct.unpack_from("<I", image.data, image.optional_offset + 56)[0], original_size)

    def test_relocation_blocks_are_dword_padded_and_round_trip(self):
        for offsets, expected_size in (((0x024,), 12), ((0x024, 0x04C), 12), ((0x024, 0x04C, 0x080), 16)):
            with self.subTest(offsets=offsets):
                data = make_pe()
                struct.pack_into("<II", data, 0x98 + 96 + 5 * 8, 0x2000, 0)
                image = PEImage.parse(data)
                image.append_highlow_relocations(0x1000, offsets)
                reloc_rva, reloc_size = directory(image, 5)
                self.assertEqual(reloc_size, expected_size)
                block = image.rva_to_offset(reloc_rva, expected_size)
                expected_entries = tuple(0x3000 | offset for offset in offsets)
                actual = struct.unpack_from("<II" + "H" * len(offsets), image.data, block)
                self.assertEqual(actual, (0x1000, expected_size, *expected_entries))
                if len(offsets) % 2:
                    self.assertEqual(struct.unpack_from("<H", image.data, block + expected_size - 2)[0], 0)
                image.remove_highlow_relocations(0x1000, offsets)
                self.assertEqual(directory(image, 5), (0x2000, 0))
                self.assertEqual(image.data[block:block + expected_size], bytes(expected_size))

    def test_foreign_appended_padding_is_refused_without_mutation(self):
        image = PEImage.parse(make_pe() + b"x")
        section = image.append_section(".odd", b"abc", 0xE0000020)
        image.data[section.raw_offset - 1] = 0xFF
        before = image.data[:]
        with self.assertRaises(PEError):
            image.remove_section(".odd")
        self.assertEqual(image.data, before)


class CameraRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = pristine_game()
        cls.golden = json.loads((ROOT / "tests/golden/v3.json").read_text(encoding="utf-8"))

    def test_all_sixteen_goldens_and_reverse_restore(self):
        for name, record in self.golden["game_variants"].items():
            with self.subTest(variant=name):
                mods = load_binary_mods(load_release(ROOT), record["components"])
                images = {"game/sys/game.dll": bytearray(self.game)}
                runner = PatchRunner(images)
                if mods:
                    runner.apply(mods)
                    self.assertEqual(runner.check(mods), {mod.id: PatchState.PATCHED for mod in mods})
                self.assertEqual(sha256(images["game/sys/game.dll"]), record["sha256"])
                if mods:
                    runner.restore(mods)
                self.assertEqual(images["game/sys/game.dll"], self.game)

    def test_section_relocation_padding_and_truncation_round_trip(self):
        original = PEImage.parse(self.game)
        original_directory = directory(original, 5)
        original_image_size = struct.unpack_from("<I", original.data, original.optional_offset + 56)[0]
        original_raw_end = max(section.raw_offset + section.raw_size for section in original.sections)
        images = {"game/sys/game.dll": bytearray(self.game)}
        mod = camera_mod()
        runner = PatchRunner(images)
        runner.apply([mod])

        patched = PEImage.parse(images["game/sys/game.dll"])
        section = patched.section(".htcam")
        self.assertEqual(section.virtual_size, 0x1000)
        self.assertEqual(section.raw_size, 0x1000)
        self.assertEqual(section.flags, 0xE0000020)
        self.assertEqual(patched.sections[-1], section)
        self.assertEqual(directory(patched, 5)[1], original_directory[1] + 12)
        self.assertNotEqual(struct.unpack_from("<I", patched.data, patched.optional_offset + 56)[0], original_image_size)
        self.assertEqual(patched.data[original_raw_end:section.raw_offset], bytes(section.raw_offset - original_raw_end))
        self.assertEqual(len(patched.data), section.raw_offset + section.raw_size)
        reloc_rva, reloc_size = directory(patched, 5)
        block = patched.rva_to_offset(reloc_rva + reloc_size - 12, 12)
        self.assertEqual(struct.unpack_from("<IIHH", patched.data, block),
                         (section.virtual_address, 12, 0x3024, 0x304C))

        runner.restore([mod])
        restored = PEImage.parse(images["game/sys/game.dll"])
        self.assertEqual(images["game/sys/game.dll"], self.game)
        self.assertEqual(directory(restored, 5), original_directory)
        self.assertEqual(struct.unpack_from("<I", restored.data, restored.optional_offset + 56)[0], original_image_size)
        self.assertEqual(len(restored.data), len(self.game))

    def test_foreign_operands_and_later_data_are_refused_without_mutation(self):
        mod = camera_mod()
        for offset in (0x32D33, 0x33708, 0x35C5C, 0x35E19):
            with self.subTest(foreign=offset):
                mutated = bytearray(self.game)
                mutated[offset] ^= 0xFF
                before = mutated[:]
                self.assertEqual(mod.inspect_bytes(mutated), PatchState.FOREIGN)
                with self.assertRaises(StateError):
                    PatchRunner({mod.target: mutated}).apply([mod])
                self.assertEqual(mutated, before)

        patched = bytearray(self.game)
        runner = PatchRunner({mod.target: patched})
        runner.apply([mod])
        for mutation in ("trailing", "reloc"):
            with self.subTest(mutation=mutation):
                candidate = patched[:]
                if mutation == "trailing":
                    candidate += b"later data"
                else:
                    image = PEImage.parse(candidate)
                    reloc_rva, reloc_size = directory(image, 5)
                    block = image.rva_to_offset(reloc_rva + reloc_size - 12, 12)
                    candidate[block + 8] ^= 0xFF
                before = candidate[:]
                self.assertEqual(mod.inspect_bytes(candidate), PatchState.FOREIGN)
                with self.assertRaises(StateError):
                    PatchRunner({mod.target: candidate}).restore([mod])
                self.assertEqual(candidate, before)


if __name__ == "__main__":
    unittest.main()
