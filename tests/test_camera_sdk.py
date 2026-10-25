import hashlib
import importlib.util
import json
import math
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


def matmul(left, right):
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(4)) for column in range(4))
        for row in range(4)
    )


def translation(x, y, z):
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (x, y, z, 1.0),
    )


def rotation_x(degrees):
    angle = degrees * 3.141592653589793 / 180.0
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, cosine, sine, 0.0),
        (0.0, -sine, cosine, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def rotation_y(degrees):
    angle = degrees * 3.141592653589793 / 180.0
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        (cosine, 0.0, -sine, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (sine, 0.0, cosine, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


class CabMatrixTests(unittest.TestCase):
    def assertMatrixAlmostEqual(self, actual, expected):
        for actual_row, expected_row in zip(actual, expected):
            for actual_value, expected_value in zip(actual_row, expected_row):
                self.assertAlmostEqual(actual_value, expected_value, places=9)

    def test_fixed_eye_transform_preserves_position_and_combined_orientation(self):
        base = matmul(
            translation(1200.0, 40.0, -300.0),
            matmul(rotation_y(13.0), rotation_x(-7.0)),
        )
        eye = base[3][:3]
        pitch = rotation_x(80.0)
        yaw = rotation_y(25.0)
        current = matmul(matmul(base, pitch), yaw)
        old_single_euler = matmul(base, matmul(pitch, yaw))
        self.assertMatrixAlmostEqual(current, old_single_euler)
        self.assertNotEqual(current[3][:3], eye)

        fixed = matmul(
            matmul(matmul(matmul(base, translation(*(-value for value in eye))), yaw), pitch),
            translation(*eye),
        )
        self.assertEqual(fixed[3][:3], eye)
        expected = matmul(matmul(base, yaw), pitch)
        self.assertMatrixAlmostEqual(fixed[:3], expected[:3])


class CameraStructureTests(unittest.TestCase):
    def test_cab_helper_saves_eye_and_rotates_about_it_with_yaw_then_pitch(self):
        module = camera_module()
        blob, labels = module.build_helpers(BASE_VA)
        self.assertIn("htcam_clamp80", labels)
        cab = blob[:labels["htcam_orbit"] - BASE_VA]
        edi_call = b"\xff\xd7"
        self.assertEqual(cab.count(edi_call), 2)
        self.assertEqual(cab.count(IAT_CALL), 2)
        first_translate = cab.index(edi_call)
        yaw_rotate = cab.index(IAT_CALL)
        pitch_rotate = cab.index(IAT_CALL, yaw_rotate + 1)
        second_translate = cab.index(edi_call, first_translate + 1)
        self.assertLess(first_translate, yaw_rotate)
        self.assertLess(yaw_rotate, pitch_rotate)
        self.assertLess(pitch_rotate, second_translate)

        for offset in (0x88, 0x8C, 0x90):
            eye_load = b"\x8b\x84\x24" + struct.pack("<I", offset)
            self.assertLess(cab.index(eye_load), first_translate)
        self.assertEqual(cab.count(b"\x35\x00\x00\x00\x80"), 3)
        self.assertLess(cab.index(bytes.fromhex("8d 54 24 10")), first_translate)
        self.assertLess(cab.index(bytes.fromhex("8d 54 24 44")), second_translate)

        clamp_call = cab.index(b"\xe8")
        rel = struct.unpack_from("<i", cab, clamp_call + 1)[0]
        self.assertEqual(BASE_VA + clamp_call + 5 + rel, labels["htcam_clamp80"])
        self.assertLess(clamp_call, yaw_rotate)
        saved_pitch = bytes.fromhex("89 84 24 20 00 00 00")
        restored_pitch = bytes.fromhex("8b 84 24 20 00 00 00")
        self.assertLess(cab.index(saved_pitch), yaw_rotate)
        self.assertLess(yaw_rotate, cab.index(restored_pitch))
        self.assertLess(cab.index(restored_pitch), pitch_rotate)
        yaw = bytes.fromhex("89 84 24 14 02 00 00")
        pitch = bytes.fromhex("89 84 24 10 02 00 00")
        self.assertLess(cab.index(yaw), yaw_rotate)
        self.assertLess(cab.index(restored_pitch), cab.index(pitch))
        self.assertLess(cab.index(pitch), pitch_rotate)

        self.assertEqual(
            cab[-5:],
            b"\xe9" + struct.pack("<i", module.CAB_RESUME - (BASE_VA + len(cab) - 5 + 5)),
        )
        self.assertEqual(module.CAB_RESUME, 0x10033D9D)
        self.assertEqual(len(labels["cab_rotate_relocs"]), 2)
        self.assertEqual(labels["cab_rotate_relocs"], sorted(labels["cab_rotate_relocs"]))
        for offset in labels["cab_rotate_relocs"]:
            self.assertEqual(cab[offset - 2:offset + 4], IAT_CALL)

    def test_cab_clamp_is_sign_aware_80_degrees_and_orbit_clamp_is_unchanged(self):
        module = camera_module()
        blob, labels = module.build_helpers(BASE_VA)
        self.assertEqual(struct.unpack("<f", struct.pack("<I", module.CAB_PITCH_HI))[0], 80.0)
        self.assertEqual(struct.unpack("<f", struct.pack("<I", module.CAB_PITCH_LO))[0], -80.0)
        c80 = blob[labels["htcam_clamp80"] - BASE_VA:labels["htcam_clamp40"] - BASE_VA]
        c40 = blob[labels["htcam_clamp40"] - BASE_VA:labels["htcam_rst_cam2"] - BASE_VA]
        negative_guard = b"\xb9" + struct.pack("<I", module.CAB_PITCH_LO) + b"\x39\xc8\x76\x02"
        positive_guard = b"\xb9" + struct.pack("<I", module.CAB_PITCH_HI) + b"\x39\xc8\x7e\x02"
        self.assertIn(b"\x85\xc0\x79\x0c", c80)
        self.assertIn(negative_guard, c80)
        self.assertIn(positive_guard, c80)
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
        _helpers, labels = camera_module().build_helpers(
            patched.image_base + section.virtual_address
        )
        entries = tuple(0x3000 | offset for offset in labels["cab_rotate_relocs"])
        self.assertEqual(struct.unpack_from("<IIHH", patched.data, block),
                         (section.virtual_address, 12, *entries))

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
