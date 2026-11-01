import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import ManifestError, Selection, load_release


VALID_MOD = {
    "schema_version": 1,
    "id": "alpha",
    "version": "1.0.0",
    "kind": "data",
    "setup": {
        "label": "Alpha",
        "description": "Alpha test mod",
        "default": True,
    },
    "requires": [],
    "conflicts": [],
    "new_profile": False,
    "files": [
        {
            "source": "config.cfg",
            "destination": "game/usr/config.cfg",
            "transforms": ["strip_r_renderer"],
        }
    ],
}


def valid_binary_mod():
    mod = copy.deepcopy(VALID_MOD)
    mod.pop("files")
    mod.update({
        "kind": "binary",
        "handler": "camera",
        "target": "game/sys/game.dll",
        "source_sha256": "0" * 64,
    })
    return mod


class ReleaseFactory:
    def __init__(self, test):
        self.test = test
        self.temporary = tempfile.TemporaryDirectory()
        test.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.modpack = self.root / "modpack"
        self.modpack.mkdir()

    def write(self, release=None, mods=None):
        release = release or {
            "schema_version": 1,
            "mods": ["alpha"],
            "game_variants": {},
        }
        mods = mods or {"alpha": copy.deepcopy(VALID_MOD)}
        (self.modpack / "release.json").write_text(
            json.dumps(release), encoding="utf-8"
        )
        for mod_id, manifest in mods.items():
            directory = self.modpack / "mods" / mod_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "mod.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            for record in manifest.get("files", []):
                files_root = directory / "files"
                source = files_root / record["source"]
                if "\0" in record["source"]:
                    continue
                if not source.resolve().is_relative_to(files_root.resolve()):
                    continue
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b'uset r_renderer "dx8"\r\nuset g_console "1"\r\n')
        return self.root


class ManifestTests(unittest.TestCase):
    def factory(self):
        return ReleaseFactory(self)

    def assert_invalid(self, root, match):
        with self.assertRaisesRegex(ManifestError, match):
            load_release(root)

    def test_loads_real_release_in_stable_order(self):
        release = load_release(ROOT)
        self.assertEqual(
            list(release.mods),
            [
                "config",
                "handling_defs",
                "economy_def",
                "driver_def",
                "camera",
                "physics",
                "ai",
                "ui_keyboard",
                "frame_cap",
                "dx8_vsync",
            ],
        )
        self.assertEqual(list(release.game_variants), [f"{n:04b}" for n in range(16)])
        self.assertEqual({mod.version for mod in release.mods.values()}, {"0.3.0"})
        self.assertEqual(release.legacy_game_sha256, (
            "eb01589a2f6c05e67623a26994b0167df20536e976012f0d470ed526e54292e3",
            "30fa4d5fa1d28bb4278973eec4272d5a25886a16b020e9879a4d266ab8c7d3a2",
            "b27816cfec9d99f877db3da33081b5b059d736ba3a4aad223da9536291e6c6e9",
            "701d27b7b41b27d89e59657ec928a23fb962f0c6ffd9593a6f5358088f16ee2a",
            "b24dd16859d02ba13e022399ad3d11ac7174c734fdc0b052da854dc715c02432",
            "1d1d434f925d0cbbdfe3f63d68be5755376d6ce5a088fe1c4eef70e7c872068f",
            "a98cb455f390e426054bf092799db6185f0721e6a3781cbf3e55f472f262ab97",
            "fff9a0b9ece1110f3516f5010405b1e65f2bd11e340fc18aad60020c31877b85",
            "c4d445c785f1dca932c40248606d8a5ff8a1b2c1a061426378c52bf39cbcf438",
            "1df051a43cb033aea893bffda7b27263285b93c501b3801710d703a7c9888bc4",
            "a9cff2a548efd32b94cc447f6a58691abe208bf9bb8732cf66286780e1c98232",
            "e75ed1b0f2d2562275d2eda21bb1651047af888c993b3cea3adc99f35932e9c9",
            "b53c85e37cc700f8cccaaf3761b164384b4134d27421e7f9252a33bca39c811c",
            "93d627ff4d05e7bd5eb12ce6725fd16321fa47a601e973340cf7b2f4dc680450",
            "0c44694c264b0074f963753413b6f52b2c328c273c36355b5f88175173bcbd79",
            "ba2bcb61f28751b464bf2c8a675c5fd5dfe92e10bcbc605ea4b0fb7205e8a374",
            "b160648068fc3c6894dde0c097f3c8778f848edd529d0dc5f0248a623172d36e",
            "d6a570c2506b28c9a9adbcfd6a90141816e02ddda84dcdfac38e5b8912e4883e",
            "f621a87583c48c4f64484634c3a0a3e8c4dab99ffdbbddb604e980701824af6a",
            "e3ed2d3f7759acb9f0575fc9f28cdfb9aeea74fd29245e7e1992e1ba7b6e44f3",
            "3606b520e0958acced418909bd16fa307fe81e0dea8ac81227280c09b4f4c413",
            "ddd77b97e419488b4acb96f2c464abae1aeaa1ba3a3ca13fe400515a795f4c77",
            "6bd78d97e9bbf112581125d32bd1a93016b0359d127f670d8d89ab2cee230184",
            "cf421034c41ecf1c0b794117d4b97bcd7decfa32c573b0478e3cc2d54aaedd18",
        ))
        self.assertEqual(release.root, ROOT / "modpack")

    def test_release_exposes_one_pristine_hash_per_binary_target(self):
        release = load_release(ROOT)
        self.assertEqual(release.pristine_sha256, {
            "game/sys/game.dll": "eb01589a2f6c05e67623a26994b0167df20536e976012f0d470ed526e54292e3",
            "sys/dx8.dll": "7dcdca3c8c01dd03650507da1264d5322f8d8f3587f731201dc0e0e49dbbd66c",
            "prism3d.exe": "5a8f94da924b05620e2a69daa09682dae3cab6b301ddc463b54f5750b514bddd",
        })

    def test_rejects_disagreeing_pristine_hashes_for_one_target(self):
        factory = self.factory()
        alpha = valid_binary_mod()
        beta = valid_binary_mod()
        beta.update(id="beta", source_sha256="1" * 64, conflicts=["alpha"])
        release = {
            "schema_version": 1,
            "mods": ["alpha", "beta"],
            "game_variants": {},
        }
        self.assert_invalid(
            factory.write(release, {"alpha": alpha, "beta": beta}),
            "disagree.*pristine|pristine.*disagree",
        )

    def test_legacy_game_hashes_are_unique_lowercase_sha256_strings(self):
        valid = "a" * 64
        factory = self.factory()
        release = {
            "schema_version": 1,
            "mods": ["alpha"],
            "game_variants": {},
            "legacy_game_sha256": [valid],
        }
        loaded = load_release(factory.write(release=release))
        self.assertEqual(loaded.legacy_game_sha256, (valid,))
        for value in ("not-a-list", ["A" * 64], ["invalid"], [0], [valid, valid]):
            with self.subTest(value=value):
                factory = self.factory()
                release = {
                    "schema_version": 1,
                    "mods": ["alpha"],
                    "game_variants": {},
                    "legacy_game_sha256": value,
                }
                self.assert_invalid(factory.write(release=release), "legacy_game_sha256")

    def test_rejects_release_and_mod_schema_versions(self):
        for location in ("release", "mod"):
            with self.subTest(location=location):
                factory = self.factory()
                release = {"schema_version": 2, "mods": ["alpha"], "game_variants": {}}
                mod = copy.deepcopy(VALID_MOD)
                if location == "mod":
                    release["schema_version"] = 1
                    mod["schema_version"] = 2
                root = factory.write(release, {"alpha": mod})
                self.assert_invalid(root, "schema_version")

    def test_rejects_missing_required_metadata(self):
        for field in ("id", "version", "kind", "setup", "requires", "conflicts", "new_profile"):
            with self.subTest(field=field):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                del mod[field]
                root = factory.write(mods={"alpha": mod})
                self.assert_invalid(root, field)

    def test_rejects_unknown_kind(self):
        factory = self.factory()
        mod = copy.deepcopy(VALID_MOD)
        mod["kind"] = "mystery"
        self.assert_invalid(factory.write(mods={"alpha": mod}), "kind")

    def test_rejects_unknown_binary_handler(self):
        factory = self.factory()
        mod = copy.deepcopy(VALID_MOD)
        mod.pop("files")
        mod.update({
            "kind": "binary",
            "handler": "dynamic_python_path",
            "target": "game/sys/game.dll",
            "source_sha256": "0" * 64,
        })
        self.assert_invalid(factory.write(mods={"alpha": mod}), "handler")

    def test_rejects_unknown_transform(self):
        factory = self.factory()
        mod = copy.deepcopy(VALID_MOD)
        mod["files"][0]["transforms"] = ["run_python"]
        self.assert_invalid(factory.write(mods={"alpha": mod}), "transform")

    def test_rejects_duplicate_release_ids(self):
        factory = self.factory()
        release = {"schema_version": 1, "mods": ["alpha", "alpha"], "game_variants": {}}
        self.assert_invalid(factory.write(release=release), "duplicate")

    def test_rejects_manifest_id_mismatch(self):
        factory = self.factory()
        mod = copy.deepcopy(VALID_MOD)
        mod["id"] = "other"
        self.assert_invalid(factory.write(mods={"alpha": mod}), "does not match")

    def test_rejects_duplicate_data_destinations(self):
        factory = self.factory()
        beta = copy.deepcopy(VALID_MOD)
        beta["id"] = "beta"
        release = {"schema_version": 1, "mods": ["alpha", "beta"], "game_variants": {}}
        self.assert_invalid(
            factory.write(release, {"alpha": copy.deepcopy(VALID_MOD), "beta": beta}),
            "destination.*already owned",
        )

    def test_rejects_unsafe_source_paths(self):
        for source in ("../secret", "/absolute", "folder\\escape", "folder/../../escape"):
            with self.subTest(source=source):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                mod["files"][0]["source"] = source
                self.assert_invalid(factory.write(mods={"alpha": mod}), "source path")

    def test_rejects_unsafe_or_unowned_destinations(self):
        for destination in (
            "../config.cfg",
            "/game/usr/config.cfg",
            "game/usr/../config.cfg",
            "game\\usr\\config.cfg",
            "game/config.cfg",
            "game/usr/save/profile.sav",
        ):
            with self.subTest(destination=destination):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                mod["files"][0]["destination"] = destination
                self.assert_invalid(factory.write(mods={"alpha": mod}), "destination")

    def test_rejects_nonportable_windows_source_and_destination_paths(self):
        destinations = (
            "game/usr/CON",
            "game/usr/con.sc",
            "game/usr/PRN",
            "game/usr/AUX.txt",
            "game/usr/NUL",
            "game/usr/COM1",
            "game/usr/com9.sc",
            "game/usr/LPT1",
            "game/usr/lpt9.sc",
            "game/usr/CONIN$",
            "game/usr/CONOUT$.sc",
            "game/usr/file:ads",
            "game/usr/trailing.",
            "game/usr/trailing ",
            "game/usr/<invalid",
            "game/usr/invalid>",
            'game/usr/invalid"',
            "game/usr/invalid|",
            "game/usr/invalid?",
            "game/usr/invalid*",
            "game/usr/invalid\x01name",
        )
        for destination in destinations:
            with self.subTest(destination=repr(destination)):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                mod["files"][0]["destination"] = destination
                self.assert_invalid(factory.write(mods={"alpha": mod}), "nonportable|reserved|unsafe")
        for source in ("CON", "file:ads", "trailing.", "bad\x01name"):
            with self.subTest(source=repr(source)):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                mod["files"][0]["source"] = source
                self.assert_invalid(factory.write(mods={"alpha": mod}), "nonportable|reserved|unsafe")

    def test_rejects_missing_dependency_and_conflict_references(self):
        for field in ("requires", "conflicts"):
            with self.subTest(field=field):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                mod[field] = ["missing"]
                self.assert_invalid(factory.write(mods={"alpha": mod}), field)

    def test_rejects_unknown_release_mod_and_variant_component(self):
        factory = self.factory()
        release = {"schema_version": 1, "mods": ["missing"], "game_variants": {}}
        self.assert_invalid(factory.write(release=release), "unknown mod")

        factory = self.factory()
        release = {
            "schema_version": 1,
            "mods": ["alpha"],
            "game_variants": {
                "0": {"file": "game-0.dll", "components": ["missing"]}
            },
        }
        self.assert_invalid(factory.write(release, {"alpha": valid_binary_mod()}), "unknown component")

    def test_rejects_noncanonical_variant_name_and_file(self):
        for name, filename in (("1", "game-001.dll"), ("001", "other.dll")):
            with self.subTest(name=name, filename=filename):
                factory = self.factory()
                release = {
                    "schema_version": 1,
                    "mods": ["alpha"],
                    "game_variants": {
                        name: {"file": filename, "components": []}
                    },
                }
                self.assert_invalid(factory.write(release, {"alpha": valid_binary_mod()}), "variant")

    def test_rejects_files_root_symlink_escaping_mod_registry(self):
        factory = self.factory()
        root = factory.write()
        files_root = factory.modpack / "mods/alpha/files"
        escaped = Path(factory.temporary.name) / "escaped-files"
        files_root.rename(escaped)
        files_root.symlink_to(escaped, target_is_directory=True)
        self.assert_invalid(root, "files root")

    def test_rejects_case_insensitive_destination_collisions(self):
        for scope in ("same_mod", "cross_mod"):
            with self.subTest(scope=scope):
                factory = self.factory()
                alpha = copy.deepcopy(VALID_MOD)
                collision = {
                    "source": "other.cfg",
                    "destination": "game/usr/CONFIG.CFG",
                }
                mods = {"alpha": alpha}
                mod_ids = ["alpha"]
                if scope == "same_mod":
                    alpha["files"].append(collision)
                else:
                    beta = copy.deepcopy(VALID_MOD)
                    beta["id"] = "beta"
                    beta["files"] = [collision]
                    mods["beta"] = beta
                    mod_ids.append("beta")
                release = {
                    "schema_version": 1,
                    "mods": mod_ids,
                    "game_variants": {},
                }
                self.assert_invalid(factory.write(release, mods), "destination.*already owned")

    def test_rejects_unknown_keys_in_every_schema_record(self):
        for location in ("release", "mod", "setup", "file", "variant"):
            with self.subTest(location=location):
                factory = self.factory()
                release = {
                    "schema_version": 1,
                    "mods": ["alpha"],
                    "game_variants": {
                        "000": {"file": "game-000.dll", "components": []}
                    },
                }
                mod = copy.deepcopy(VALID_MOD)
                if location == "release":
                    release["unexpected"] = True
                elif location == "mod":
                    mod["unexpected"] = True
                elif location == "setup":
                    mod["setup"]["unexpected"] = True
                elif location == "file":
                    mod["files"][0]["transform"] = mod["files"][0].pop("transforms")
                else:
                    mod = valid_binary_mod()
                    release["game_variants"] = {"0": {"file": "game-0.dll", "components": [], "unexpected": True}}
                self.assert_invalid(factory.write(release, {"alpha": mod}), "unknown key")

    def test_malformed_registry_types_raise_manifest_error(self):
        for field in ("kind", "handler"):
            with self.subTest(field=field):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                if field == "handler":
                    mod.pop("files")
                    mod.update({
                        "kind": "binary",
                        "target": "game/sys/game.dll",
                        "source_sha256": "0" * 64,
                    })
                mod[field] = []
                self.assert_invalid(factory.write(mods={"alpha": mod}), field)

    def test_nul_paths_raise_manifest_error(self):
        for field in ("source", "destination"):
            with self.subTest(field=field):
                factory = self.factory()
                mod = copy.deepcopy(VALID_MOD)
                mod["files"][0][field] = "bad\0path"
                self.assert_invalid(factory.write(mods={"alpha": mod}), "path")
        factory = self.factory()
        with self.assertRaises(ManifestError):
            load_release(str(factory.root) + "\0")

    def test_selection_validates_dependencies_conflicts_and_unknown_ids(self):
        factory = self.factory()
        alpha = copy.deepcopy(VALID_MOD)
        alpha["requires"] = ["beta"]
        alpha["conflicts"] = ["gamma"]
        beta = copy.deepcopy(VALID_MOD)
        beta["id"] = "beta"
        beta["files"][0]["destination"] = "game/usr/beta.cfg"
        gamma = copy.deepcopy(VALID_MOD)
        gamma["id"] = "gamma"
        gamma["files"][0]["destination"] = "game/usr/gamma.cfg"
        release = {
            "schema_version": 1,
            "mods": ["alpha", "beta", "gamma"],
            "game_variants": {},
        }
        loaded = load_release(factory.write(release, {"alpha": alpha, "beta": beta, "gamma": gamma}))
        Selection(["alpha", "beta"]).validate(loaded.mods)
        with self.assertRaisesRegex(ManifestError, "requires"):
            Selection(["alpha"]).validate(loaded.mods)
        with self.assertRaisesRegex(ManifestError, "conflicts"):
            Selection(["alpha", "beta", "gamma"]).validate(loaded.mods)
        with self.assertRaisesRegex(ManifestError, "unknown mod"):
            Selection(["missing"]).validate(loaded.mods)


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/htmod.py"), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def test_list_prints_stable_component_ids(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "config",
                "handling_defs",
                "economy_def",
                "driver_def",
                "camera",
                "physics",
                "ai",
                "ui_keyboard",
                "frame_cap",
                "dx8_vsync",
            ],
        )

    def test_validate_reports_valid_release(self):
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("valid", result.stdout.lower())

    def test_validate_returns_failure_for_invalid_release(self):
        factory = ReleaseFactory(self)
        mod = copy.deepcopy(VALID_MOD)
        mod["kind"] = "invalid"
        root = factory.write(mods={"alpha": mod})
        result = self.run_cli("--root", str(root), "validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kind", result.stderr)

    def test_validate_malformed_types_do_not_print_tracebacks(self):
        factory = ReleaseFactory(self)
        mod = copy.deepcopy(VALID_MOD)
        mod["kind"] = []
        root = factory.write(mods={"alpha": mod})
        result = self.run_cli("--root", str(root), "validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("kind", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
