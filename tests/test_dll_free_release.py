import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from tests.release_fixtures import fake_pe64
from tools import build_windows_release


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DLLFreeReleaseTests(unittest.TestCase):
    def make_arguments(self, root, output_name="release"):
        patcher = Path(root) / "htmod.exe"
        fake_pe64(patcher)
        return SimpleNamespace(
            output=Path(root) / output_name,
            game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
            dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
            patcher_exe=patcher,
            migrations=ROOT / "modpack/migrations.json",
        )

    def assert_dll_free_schema_three(self, release, patcher):
        payload = release / "installer/payload"
        manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 3)
        self.assertNotIn("legacy_game_sha256", manifest)
        self.assertEqual(len(manifest["game_variants"]), 16)
        for record in manifest["game_variants"].values():
            self.assertNotIn("file", record)
            self.assertEqual(set(record), {"sha256", "components"})
        for record in manifest["dx8"].values():
            self.assertNotIn("file", record)
            self.assertEqual(set(record), {"sha256"})
        self.assertFalse(list(release.rglob("*.dll")))
        self.assertFalse(list(payload.glob("game-*")))
        self.assertFalse(list(payload.glob("dx8-*")))
        self.assertEqual(sha256(payload / "htmod.exe"), sha256(patcher))
        self.assertEqual(manifest["payloads"]["htmod.exe"], sha256(patcher))
        self.assertEqual(manifest["payloads"]["migrations.json"], sha256(ROOT / "modpack/migrations.json"))
        self.assertEqual((payload / "migrations.json").read_bytes(), (ROOT / "modpack/migrations.json").read_bytes())
        self.assertNotIn("test-base", [path.relative_to(release).as_posix() for path in release.rglob("*")])
        return manifest

    def test_build_emits_schema_three_artifacts_and_no_dll_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.make_arguments(temporary)
            build_windows_release.build(args)
            self.assert_dll_free_schema_three(args.output, args.patcher_exe)

    def test_variant_generation_is_in_memory_and_has_no_payload_file_fields(self):
        game = build_windows_release.default_test_base_file(Path("game/sys/game.dll"))
        dx8 = build_windows_release.default_test_base_file(Path("sys/dx8.dll"))
        variants = build_windows_release.build_game_variants(game)
        dx8_records = build_windows_release.build_dx8_payloads(dx8)
        self.assertEqual(len(variants), 16)
        self.assertTrue(all(set(record) == {"sha256", "components"} for record in variants.values()))
        self.assertTrue(all(set(record) == {"sha256"} for record in dx8_records.values()))

    def test_deterministic_release_directories_use_identical_patcher_and_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_args = self.make_arguments(root, "first")
            build_windows_release.build(first_args)
            second_args = SimpleNamespace(**vars(first_args))
            second_args.output = root / "second"
            build_windows_release.build(second_args)
            self.assert_dll_free_schema_three(first_args.output, first_args.patcher_exe)
            self.assert_dll_free_schema_three(second_args.output, first_args.patcher_exe)
            first = {
                path.relative_to(first_args.output).as_posix(): path.read_bytes()
                for path in first_args.output.rglob("*") if path.is_file()
            }
            second = {
                path.relative_to(second_args.output).as_posix(): path.read_bytes()
                for path in second_args.output.rglob("*") if path.is_file()
            }
            self.assertEqual(first, second)

    def test_builder_rejects_non_64bit_patcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.make_arguments(temporary)
            data = bytearray(args.patcher_exe.read_bytes())
            struct.pack_into("<H", data, 0x84, 0x014C)
            args.patcher_exe.write_bytes(data)
            with self.assertRaisesRegex(Exception, "64-bit|AMD64"):
                build_windows_release.build(args)
            self.assertFalse(args.output.exists())


if __name__ == "__main__":
    unittest.main()
