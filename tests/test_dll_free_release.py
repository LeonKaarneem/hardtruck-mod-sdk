import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
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

    def test_rebuild_discards_stale_output_and_matches_a_fresh_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.make_arguments(root, "release")
            build_windows_release.build(args)
            stale = (
                args.output / "stale-top-level.txt",
                args.output / "installer/stale-installer.txt",
                args.output / "installer/payload/stale-payload.txt",
            )
            for path in stale:
                path.write_bytes(b"stale")

            fresh_args = SimpleNamespace(**vars(args))
            fresh_args.output = root / "fresh"
            build_windows_release.build(fresh_args)
            build_windows_release.build(args)

            for path in stale:
                self.assertFalse(path.exists(), path)
            self.assert_dll_free_schema_three(args.output, args.patcher_exe)
            rebuilt = {
                path.relative_to(args.output).as_posix(): path.read_bytes()
                for path in args.output.rglob("*") if path.is_file()
            }
            fresh = {
                path.relative_to(fresh_args.output).as_posix(): path.read_bytes()
                for path in fresh_args.output.rglob("*") if path.is_file()
            }
            self.assertEqual(rebuilt, fresh)

    def test_validation_failure_leaves_existing_output_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.make_arguments(temporary)
            build_windows_release.build(args)
            before = {
                path.relative_to(args.output).as_posix(): path.read_bytes()
                for path in args.output.rglob("*") if path.is_file()
            }
            args.patcher_exe.write_bytes(b"invalid")

            with self.assertRaisesRegex(Exception, "64-bit|AMD64|PE"):
                build_windows_release.build(args)

            after = {
                path.relative_to(args.output).as_posix(): path.read_bytes()
                for path in args.output.rglob("*") if path.is_file()
            }
            self.assertEqual(after, before)

    def custom_catalog(self, args, mutate=None):
        catalog = json.loads((ROOT / "modpack/migrations.json").read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(catalog)
        path = args.output.parent / "custom-migrations.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        args.migrations = path

    def assert_catalog_rejected(self, mutate, message):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.make_arguments(temporary)
            self.custom_catalog(args, mutate)
            with self.assertRaisesRegex(Exception, message):
                build_windows_release.build(args)
            self.assertFalse(args.output.exists())

    def test_custom_catalog_is_accepted_when_complete_and_matching(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.make_arguments(temporary)
            self.custom_catalog(args)
            build_windows_release.build(args)
            payload = args.output / "installer/payload"
            self.assertEqual((payload / "migrations.json").read_bytes(), args.migrations.read_bytes())
            manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["payloads"]["migrations.json"], sha256(args.migrations))

    def test_custom_catalog_requires_both_canonical_targets(self):
        self.assert_catalog_rejected(
            lambda catalog: catalog["targets"].pop("sys/dx8.dll"),
            "canonical targets",
        )
        self.assert_catalog_rejected(
            lambda catalog: catalog["targets"].update({"other.dll": catalog["targets"]["sys/dx8.dll"]}),
            "canonical targets",
        )

    def test_custom_catalog_pristine_must_match_build_inputs(self):
        for target in ("game/sys/game.dll", "sys/dx8.dll"):
            for field in ("pristine_length", "pristine_sha256"):
                with self.subTest(target=target, field=field):
                    def mutate(catalog, target=target, field=field):
                        record = catalog["targets"][target]
                        old_hash = record["pristine_sha256"]
                        if field == "pristine_length":
                            record[field] += 1
                            record["sources"][old_hash]["length"] += 1
                        else:
                            record[field] = "0" * 64
                            record["sources"][record[field]] = record["sources"].pop(old_hash)
                    self.assert_catalog_rejected(mutate, "pristine.*match|match.*pristine")

    def test_custom_catalog_requires_every_supported_source_lineage(self):
        from tests.estonian_variants import ESTONIAN_DX8_SHA256, ESTONIAN_GAME_SHA256

        release = build_windows_release.load_release(ROOT)
        variants = build_windows_release.build_game_variants(
            build_windows_release.default_test_base_file(Path("game/sys/game.dll"))
        )
        cases = (
            ("current", variants["1111"]["sha256"]),
            ("historical", release.legacy_game_sha256[1]),
            ("estonian-game", ESTONIAN_GAME_SHA256),
            ("dx8-stock", build_windows_release.PRISTINE_DX8_SHA256),
            ("dx8-vsync", json.loads((ROOT / "tests/golden/v3.json").read_text())["dx8"]["vsync"]),
            ("estonian-dx8", ESTONIAN_DX8_SHA256),
        )
        for lineage, source_hash in cases:
            with self.subTest(lineage=lineage):
                def mutate(catalog, source_hash=source_hash):
                    for target in catalog["targets"].values():
                        target["sources"].pop(source_hash, None)
                self.assert_catalog_rejected(mutate, "source coverage|missing.*source")

    @unittest.skipUnless(os.environ.get("HTMOD_RELEASE_ZIP"), "release ZIP not supplied")
    def test_zip_has_no_dll_payloads_or_known_complete_dll_binaries(self):
        from tests.estonian_variants import ESTONIAN_DX8_SHA256, ESTONIAN_GAME_SHA256
        from tests.release_fixtures import current_binary_images

        release = build_windows_release.load_release(ROOT)
        game, dx8 = current_binary_images()
        known_hashes = {hashlib.sha256(data).hexdigest() for data in game.values()}
        known_hashes.update(hashlib.sha256(data).hexdigest() for data in dx8.values())
        known_hashes.update(release.legacy_game_sha256)
        known_hashes.update((ESTONIAN_GAME_SHA256, ESTONIAN_DX8_SHA256))
        with zipfile.ZipFile(os.environ["HTMOD_RELEASE_ZIP"]) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertFalse([name for name in names if name.lower().endswith(".dll")])
            self.assertFalse([
                name for name in names
                if any(part in {"tests", "research", "test-base", "__pycache__"} for part in Path(name).parts)
            ])
            for info in archive.infolist():
                if info.is_dir():
                    continue
                data = archive.read(info)
                self.assertNotIn(hashlib.sha256(data).hexdigest(), known_hashes, info.filename)

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
