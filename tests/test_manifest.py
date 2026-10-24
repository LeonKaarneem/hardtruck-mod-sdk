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
                "dx8_vsync",
            ],
        )
        self.assertEqual(list(release.game_variants), [f"{n:04b}" for n in range(16)])
        self.assertEqual(release.root, ROOT / "modpack")

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
