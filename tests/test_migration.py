import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod.migration import (
    MigrationError,
    apply_splices,
    generate_splices,
    load_catalog,
    normalize_to_pristine,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SpliceTests(unittest.TestCase):
    def test_variable_length_replacement_insertion_and_deletion(self):
        source = b"zero-one-two-three-four"
        pristine = b"zero-1-two-THREE--four-tail"
        changes = generate_splices(source, pristine)
        self.assertTrue(any(len(change["original_hex"]) != len(change["replacement_hex"]) for change in changes))
        self.assertEqual(apply_splices(source, changes, len(pristine)), pristine)

    def test_generated_splices_round_trip_boundaries_and_empty_images(self):
        cases = (
            (b"", b""),
            (b"", b"inserted"),
            (b"deleted", b""),
            (b"abc", b"abc"),
            (b"abc", b"zabc"),
            (b"abc", b"abcz"),
            (b"abc", b"zabcz"),
            (b"abcdef", b"abef"),
            (bytes(range(256)), bytes(range(128)) + b"middle" + bytes(range(128, 256))),
        )
        for source, pristine in cases:
            with self.subTest(source=source, pristine=pristine):
                changes = generate_splices(source, pristine)
                self.assertEqual(apply_splices(source, changes, len(pristine)), pristine)

    def test_large_repetitive_binary_generation_is_compact(self):
        source = bytearray(bytes(range(256)) * 4096)
        pristine = bytearray(source)
        pristine[1000:1004] = b"changed"
        pristine[500000:500010] = b"variable-length"
        pristine.extend(b"tail")
        source = bytes(source)
        pristine = bytes(pristine)
        changes = generate_splices(source, pristine)
        self.assertEqual(apply_splices(source, changes, len(pristine)), pristine)
        self.assertLess(sum(len(change["original_hex"]) + len(change["replacement_hex"]) for change in changes), 10000)

    def test_original_bytes_are_an_exact_guard(self):
        changes = [{"offset": 1, "original_hex": "6263", "replacement_hex": "58"}]
        with self.assertRaisesRegex(MigrationError, "original bytes"):
            apply_splices(b"abbd", changes, 3)

    def test_unknown_source_is_refused(self):
        pristine = b"pristine"
        catalog = self.make_catalog(pristine, {})
        with self.assertRaisesRegex(MigrationError, "unknown source SHA-256"):
            normalize_to_pristine(b"unknown", "game/sys/game.dll", catalog)

    def test_corrupt_source_and_wrong_pristine_hash_are_refused(self):
        pristine = b"pristine"
        source = b"corrupt"
        changes = generate_splices(source, pristine)
        catalog = self.make_catalog(pristine, {digest(source): (source, changes)})
        damaged = bytearray(source)
        damaged[0] ^= 1
        with self.assertRaisesRegex(MigrationError, "unknown source SHA-256"):
            normalize_to_pristine(bytes(damaged), "game/sys/game.dll", catalog)
        catalog["targets"]["game/sys/game.dll"]["sources"][digest(source)]["length"] += 1
        with self.assertRaisesRegex(MigrationError, "source length"):
            normalize_to_pristine(source, "game/sys/game.dll", catalog)
        catalog["targets"]["game/sys/game.dll"]["sources"][digest(source)]["length"] -= 1
        catalog["targets"]["game/sys/game.dll"]["pristine_sha256"] = "0" * 64
        with self.assertRaisesRegex(MigrationError, "pristine SHA-256"):
            normalize_to_pristine(source, "game/sys/game.dll", catalog)

    def test_overlap_unsorted_bounds_and_final_length_are_refused(self):
        invalid = (
            ([{"offset": 2, "original_hex": "63", "replacement_hex": ""},
              {"offset": 1, "original_hex": "62", "replacement_hex": ""}], "sorted"),
            ([{"offset": 0, "original_hex": "6162", "replacement_hex": ""},
              {"offset": 1, "original_hex": "62", "replacement_hex": ""}], "overlap"),
            ([{"offset": -1, "original_hex": "", "replacement_hex": "61"}], "offset"),
            ([{"offset": 4, "original_hex": "61", "replacement_hex": ""}], "bounds"),
            ([{"offset": 0, "original_hex": "61", "replacement_hex": "62"}], "final length"),
        )
        for changes, message in invalid:
            with self.subTest(message=message):
                with self.assertRaisesRegex(MigrationError, message):
                    apply_splices(b"abc", changes, 2)

    def test_schema_rejects_malformed_values_and_unknown_keys(self):
        valid_change = {"offset": 0, "original_hex": "61", "replacement_hex": "62"}
        invalid_changes = (
            {**valid_change, "offset": True},
            {**valid_change, "original_hex": "A"},
            {**valid_change, "replacement_hex": "0"},
            {**valid_change, "extra": None},
            {"offset": 0, "original_hex": "", "replacement_hex": ""},
        )
        for change in invalid_changes:
            with self.subTest(change=change):
                with self.assertRaises(MigrationError):
                    apply_splices(b"a", [change], 1)
        for final_length in (-1, True, 1.5, "1"):
            with self.subTest(final_length=final_length):
                with self.assertRaises(MigrationError):
                    apply_splices(b"a", [], final_length)

    def test_catalog_loader_rejects_bad_json_schema_hashes_and_duplicate_identity(self):
        pristine = b"pristine"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "migrations.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "JSON"):
                load_catalog(path)
            for mutate in (
                lambda value: value.update(schema_version=2),
                lambda value: value.update(extra={}),
                lambda value: value["targets"]["game/sys/game.dll"].update(pristine_sha256="X" * 64),
                lambda value: value["targets"]["game/sys/game.dll"].update(pristine_length=True),
                lambda value: value["targets"]["game/sys/game.dll"]["sources"].update({"0" * 64: {"length": 1, "changes": []}}),
            ):
                value = self.make_catalog(pristine, {})
                mutate(value)
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(mutate=mutate):
                    with self.assertRaises(MigrationError):
                        load_catalog(path)

    def test_catalog_loader_enforces_compact_size_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "migrations.json"
            path.write_text(json.dumps(self.make_catalog(b"pristine", {})), encoding="utf-8")
            with path.open("a", encoding="utf-8") as output:
                output.write(" " * (512 * 1024))
            with self.assertRaisesRegex(MigrationError, "size budget"):
                load_catalog(path)

    def make_catalog(self, pristine, sources):
        source_records = {}
        for source_hash, (source, changes) in sources.items():
            source_records[source_hash] = {"length": len(source), "changes": changes}
        if digest(pristine) not in source_records:
            source_records[digest(pristine)] = {"length": len(pristine), "changes": []}
        return {
            "schema_version": 1,
            "targets": {
                "game/sys/game.dll": {
                    "pristine_length": len(pristine),
                    "pristine_sha256": digest(pristine),
                    "sources": source_records,
                }
            },
        }


class RealCatalogAndStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from htmod.manifest import load_release
        from tests.legacy_variants import (
            FIXED_EYE_GAME_SHA256,
            LEGACY_GAME_SHA256,
            write_fixed_eye_variant,
            write_legacy_variant,
        )
        from tools import build_windows_release

        cls.release = load_release(ROOT)
        cls.golden = json.loads((ROOT / "tests/golden/v3.json").read_text(encoding="utf-8"))
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        cls.payload = root / "payload"
        work = root / "work"
        cls.payload.mkdir()
        work.mkdir()
        game_dll = build_windows_release.default_test_base_file(Path("game/sys/game.dll"))
        dx8_dll = build_windows_release.default_test_base_file(Path("sys/dx8.dll"))
        build_windows_release.build_game_variants(game_dll, cls.payload, work)
        build_windows_release.build_dx8_payloads(dx8_dll, cls.payload, work)
        cls.game_sources = []
        for name in cls.golden["game_variants"]:
            cls.game_sources.append((f"current-{name}", (cls.payload / f"game-{name}.dll").read_bytes()))
        for name in LEGACY_GAME_SHA256:
            path = root / f"legacy-{name}.dll"
            write_legacy_variant(cls.payload, name, path)
            cls.game_sources.append((f"legacy-{name}", path.read_bytes()))
        for name in FIXED_EYE_GAME_SHA256:
            path = root / f"fixed-eye-{name}.dll"
            write_fixed_eye_variant(cls.payload, name, path)
            cls.game_sources.append((f"fixed-eye-{name}", path.read_bytes()))
        cls.dx8_sources = (
            ("stock", (cls.payload / "dx8-stock.dll").read_bytes()),
            ("vsync", (cls.payload / "dx8-vsync.dll").read_bytes()),
        )
        cls.catalog_path = ROOT / "modpack/migrations.json"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.game = self.root / "game dir"
        self.output = self.root / "output dir"
        (self.game / "game/sys").mkdir(parents=True)
        (self.game / "sys").mkdir(parents=True)

    def write_game_source(self, data):
        (self.game / "game/sys/game.dll").write_bytes(data)

    def write_dx8_source(self, data):
        (self.game / "sys/dx8.dll").write_bytes(data)

    def expectations(self, name):
        return {"game/sys/game.dll": self.golden["game_variants"][name]["sha256"]}

    def test_catalog_is_compact_and_reconstructs_every_source_lineage(self):
        self.assertLessEqual(self.catalog_path.stat().st_size, 512 * 1024)
        catalog = load_catalog(self.catalog_path)
        unique_game_sources = {digest(data): data for _, data in self.game_sources}
        game = catalog["targets"]["game/sys/game.dll"]
        self.assertEqual(set(game["sources"]), set(unique_game_sources))
        pristine = (self.payload / "game-0000.dll").read_bytes()
        for source_hash, source in unique_game_sources.items():
            with self.subTest(source=source_hash):
                self.assertEqual(
                    game["sources"][source_hash],
                    {"length": len(source), "changes": generate_splices(source, pristine)},
                )
        for lineage, source in self.game_sources:
            with self.subTest(lineage=lineage):
                self.assertEqual(normalize_to_pristine(source, "game/sys/game.dll", catalog), pristine)
        pristine_dx8 = (self.payload / "dx8-stock.dll").read_bytes()
        for lineage, source in self.dx8_sources:
            with self.subTest(lineage=lineage):
                self.assertEqual(normalize_to_pristine(source, "sys/dx8.dll", catalog), pristine_dx8)

    def test_every_game_source_by_every_selection_stages_expected_current_bytes(self):
        from htmod.cli import stage

        for lineage, source in self.game_sources:
            self.write_game_source(source)
            for name, variant in self.golden["game_variants"].items():
                with self.subTest(lineage=lineage, selection=name):
                    selected = list(variant["components"])
                    if "ui_keyboard" in selected:
                        selected.append("config")
                    if int(name, 2) & 1:
                        selected.reverse()
                    stage(
                        self.release,
                        self.game,
                        self.output,
                        self.catalog_path,
                        selected,
                        self.expectations(name),
                    )
                    expected = (self.payload / f"game-{name}.dll").read_bytes()
                    self.assertEqual((self.output / "game/sys/game.dll").read_bytes(), expected)
                    self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), source)
                    self.assertEqual([path for path in self.output.rglob("*") if path.is_file()], [self.output / "game/sys/game.dll"])

    def test_stock_and_vsync_dx8_normalize_with_and_without_mod(self):
        from htmod.cli import stage

        for lineage, source in self.dx8_sources:
            for selected, expected_name in (([], "stock"), (["dx8_vsync"], "vsync")):
                with self.subTest(lineage=lineage, selected=selected):
                    self.write_dx8_source(source)
                    stage(
                        self.release,
                        self.game,
                        self.output,
                        self.catalog_path,
                        selected,
                        {"sys/dx8.dll": self.golden["dx8"][expected_name]},
                    )
                    self.assertEqual(
                        (self.output / "sys/dx8.dll").read_bytes(),
                        (self.payload / f"dx8-{expected_name}.dll").read_bytes(),
                    )

    def test_zero_mods_and_data_only_selection_normalize_without_extra_outputs(self):
        from htmod.cli import stage

        self.write_game_source(self.game_sources[1][1])
        self.write_dx8_source(self.dx8_sources[1][1])
        expected = {
            "game/sys/game.dll": self.golden["pristine"]["game_dll"],
            "sys/dx8.dll": self.golden["pristine"]["dx8_dll"],
        }
        for selected in ([], ["config"]):
            with self.subTest(selected=selected):
                stage(self.release, self.game, self.output, self.catalog_path, selected, expected)
                self.assertEqual(digest((self.output / "game/sys/game.dll").read_bytes()), expected["game/sys/game.dll"])
                self.assertEqual(digest((self.output / "sys/dx8.dll").read_bytes()), expected["sys/dx8.dll"])
                self.assertEqual(len([path for path in self.output.rglob("*") if path.is_file()]), 2)

    def test_old_camera_is_removed_when_camera_is_deselected(self):
        from htmod.cli import stage

        lineage, source = next((name, data) for name, data in self.game_sources if name == "legacy-0001")
        self.assertNotEqual(source, (self.payload / "game-0000.dll").read_bytes())
        self.write_game_source(source)
        stage(self.release, self.game, self.output, self.catalog_path, [], self.expectations("0000"))
        self.assertEqual((self.output / "game/sys/game.dll").read_bytes(), (self.payload / "game-0000.dll").read_bytes())

    def test_cli_stages_idempotently_and_leaves_input_unchanged(self):
        from htmod import cli

        source = self.game_sources[-1][1]
        expected = self.golden["game_variants"]["1111"]["sha256"]
        self.write_game_source(source)
        arguments = [
            "--root", str(ROOT), "stage",
            "--game-dir", str(self.game), "--output-dir", str(self.output),
            "--migrations", str(self.catalog_path), "--mod", "camera", "--mod", "physics",
            "--mod", "ai", "--mod", "ui_keyboard", "--mod", "config",
            "--expect", f"game/sys/game.dll={expected}",
        ]
        self.assertEqual(cli.main(arguments), 0)
        first = (self.output / "game/sys/game.dll").read_bytes()
        self.assertEqual(digest(first), expected)
        self.write_game_source(first)
        self.assertEqual(cli.main(arguments), 0)
        self.assertEqual((self.output / "game/sys/game.dll").read_bytes(), first)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), first)

    def test_unknown_corrupt_wrong_expectation_and_invalid_selection_write_nothing(self):
        from htmod import cli
        from htmod.cli import stage

        cases = [
            (b"unknown", [], self.expectations("0000"), "unknown source"),
            (self.game_sources[1][1] + b"corrupt", [], self.expectations("0000"), "unknown source"),
            (self.game_sources[1][1], [], self.expectations("1111"), "final SHA-256"),
            (self.game_sources[1][1], ["missing"], self.expectations("0000"), "unknown mod"),
            (self.game_sources[1][1], ["config", "config"], self.expectations("0000"), "duplicate"),
        ]
        for source, selected, expected, message in cases:
            with self.subTest(message=message):
                self.write_game_source(source)
                before = (self.game / "game/sys/game.dll").read_bytes()
                with self.assertRaisesRegex(Exception, message):
                    stage(self.release, self.game, self.output, self.catalog_path, selected, expected)
                self.assertFalse(self.output.exists())
                self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), before)
        self.assertEqual(cli.main([
            "--root", str(ROOT), "stage", "--game-dir", str(self.game),
            "--output-dir", str(self.output), "--migrations", str(self.catalog_path),
            "--expect", f"game/sys/game.dll={'0' * 64}",
        ]), 1)
        self.assertFalse(self.output.exists())

    def test_unsafe_expectations_and_output_paths_are_refused(self):
        from htmod.cli import parse_expectations, stage

        self.write_game_source(self.game_sources[0][1])
        for value in ("../game.dll=" + "0" * 64, "game/sys/game.dll", "GAME/SYS/GAME.DLL=" + "0" * 64,
                      "game/sys/game.dll=" + "0" * 64 + "=extra"):
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    parse_expectations([value])
        with self.assertRaisesRegex(Exception, "duplicate expectation"):
            parse_expectations(["game/sys/game.dll=" + "0" * 64, "game/sys/game.dll=" + "1" * 64])
        with self.assertRaisesRegex(Exception, "unsafe|game directory"):
            stage(self.release, self.game, self.game / "stage", self.catalog_path, [], self.expectations("0000"))
        self.output.mkdir()
        (self.output / "game/sys").mkdir(parents=True)
        (self.output / "game/sys/game.dll").symlink_to(self.game / "game/sys/game.dll")
        with self.assertRaisesRegex(Exception, "symlink|unsafe"):
            stage(self.release, self.game, self.output, self.catalog_path, [], self.expectations("0000"))
        self.assertFalse((self.game / "stage").exists())

    def test_existing_output_survives_failed_final_verification_without_temp_files(self):
        from htmod.cli import stage

        self.write_game_source(self.game_sources[0][1])
        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_bytes(b"keep")
        with self.assertRaisesRegex(Exception, "final SHA-256"):
            stage(self.release, self.game, self.output, self.catalog_path, [], self.expectations("1111"))
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_symlinked_output_root_is_refused(self):
        from htmod.cli import stage

        self.write_game_source(self.game_sources[0][1])
        real_output = self.root / "real-output"
        real_output.mkdir()
        linked_output = self.root / "linked-output"
        linked_output.symlink_to(real_output, target_is_directory=True)
        with self.assertRaisesRegex(Exception, "symlink|unsafe"):
            stage(self.release, self.game, linked_output, self.catalog_path, [], self.expectations("0000"))
        self.assertEqual(list(real_output.iterdir()), [])

    def test_atomic_outputs_restore_all_existing_files_after_replace_failure(self):
        from htmod import cli

        self.write_game_source(self.game_sources[0][1])
        self.write_dx8_source(self.dx8_sources[0][1])
        expected = {
            "game/sys/game.dll": self.golden["pristine"]["game_dll"],
            "sys/dx8.dll": self.golden["pristine"]["dx8_dll"],
        }
        for target in expected:
            path = self.output / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old-" + target.encode())
        original_replace = os.replace
        failed = False

        def fail_second_stage(source, destination):
            nonlocal failed
            if (".htmod-stage-" in str(source) and str(destination).endswith("sys/dx8.dll")
                    and not failed):
                failed = True
                raise OSError("injected replace failure")
            return original_replace(source, destination)

        with mock.patch.object(cli.os, "replace", side_effect=fail_second_stage):
            with self.assertRaisesRegex(OSError, "injected"):
                cli.stage(self.release, self.game, self.output, self.catalog_path, [], expected)
        self.assertTrue(failed)
        for target in expected:
            self.assertEqual((self.output / target).read_bytes(), b"old-" + target.encode())
        self.assertFalse(list(self.output.rglob(".htmod-*")))

    def test_frozen_root_discovers_bundled_release(self):
        from htmod import cli

        with mock.patch.object(cli.sys, "frozen", True, create=True), \
                mock.patch.object(cli.sys, "_MEIPASS", str(ROOT), create=True):
            self.assertEqual(cli.default_root(), ROOT)


if __name__ == "__main__":
    unittest.main()
