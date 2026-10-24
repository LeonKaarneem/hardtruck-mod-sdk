import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
import zipfile

from tools import build_windows_release


ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get("PWSH") or shutil.which("pwsh")
COMPONENT_IDS = [
    "config",
    "handling_defs",
    "economy_def",
    "driver_def",
    "camera",
    "physics",
    "ai",
    "dx8_vsync",
]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipIf(PWSH is None, "PowerShell 7 is required for installer runtime tests")
class InstallerCase(unittest.TestCase):
    def make_game(self, parent, name="game"):
        game = Path(parent) / name
        (game / "game/sys").mkdir(parents=True)
        (game / "sys").mkdir()
        shutil.copyfile(build_windows_release.default_test_base_file(Path("prism3d.exe")), game / "prism3d.exe")
        shutil.copyfile(build_windows_release.default_test_base_file(Path("game/sys/game.dll")), game / "game/sys/game.dll")
        shutil.copyfile(build_windows_release.default_test_base_file(Path("sys/dx8.dll")), game / "sys/dx8.dll")
        return game

    def make_package(self, parent):
        package = Path(parent) / "package"
        shutil.copytree(self.release, package)
        return package

    def run_setup(self, package, game=None, action="InstallAll", components=None,
                  interactive=False, input_text=None, env=None):
        command = [PWSH, "-NoProfile", "-File", str(Path(package) / "installer/Setup.ps1")]
        if game is not None:
            command += ["-GamePath", str(game)]
        command += ["-Action", action]
        if components is not None:
            command += ["-Components", ",".join(components)]
        if not interactive:
            command += ["-NonInteractive"]
        environment = os.environ.copy()
        environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
        if env:
            environment.update(env)
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            env=environment,
            timeout=45,
        )

    def manifest(self, package):
        return json.loads((Path(package) / "installer/payload/manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, package, manifest):
        (Path(package) / "installer/payload/manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def backup_manifests(self, package):
        return sorted(Path(package).glob("installer/backups/*/installation.json"))

    def assert_stock(self, game):
        manifest = self.manifest(self.package)
        self.assertEqual(sha256(Path(game) / "game/sys/game.dll"), manifest["game_variants"]["000"]["sha256"])
        self.assertEqual(sha256(Path(game) / "sys/dx8.dll"), manifest["dx8"]["stock"]["sha256"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="htmod-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.make_package(self.root)
        self.game = self.make_game(self.root)


class InstallerMetadataRuntimeTests(InstallerCase):
    def test_install_all_rerun_and_uninstall_preserve_component_ids_and_backup_schema(self):
        result = self.run_setup(self.package, self.game)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = self.manifest(self.package)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), manifest["game_variants"]["111"]["sha256"])
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), manifest["dx8"]["vsync"]["sha256"])
        for component, files in manifest["data_components"].items():
            for payload in files:
                self.assertEqual(
                    sha256(self.game / "game/usr" / payload.removeprefix("data/")),
                    manifest["payloads"][payload],
                )
        backups = self.backup_manifests(self.package)
        self.assertEqual(len(backups), 1)
        installation = json.loads(backups[0].read_text(encoding="utf-8"))
        self.assertEqual(installation["schema_version"], 1)
        self.assertEqual(installation["status"], "Installed")
        self.assertEqual(len(installation["entries"]), 16)

        rerun = self.run_setup(self.package, self.game)
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertIn("Already correct", rerun.stdout)
        uninstall = self.run_setup(self.package, self.game, action="Uninstall")
        self.assertEqual(uninstall.returncode, 0, uninstall.stdout + uninstall.stderr)
        self.assert_stock(self.game)
        for files in manifest["data_components"].values():
            for payload in files:
                self.assertFalse((self.game / "game/usr" / payload.removeprefix("data/")).exists())

    def test_custom_noninteractive_ids_select_only_requested_components(self):
        result = self.run_setup(self.package, self.game, action="Custom", components=["config", "camera"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = self.manifest(self.package)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), manifest["game_variants"]["001"]["sha256"])
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), manifest["dx8"]["stock"]["sha256"])
        self.assertTrue((self.game / "game/usr/ui/cosetup.sc").is_file())
        self.assertFalse((self.game / "game/usr/defs/truck_base.def").exists())

    def test_every_curated_variant_uses_existing_noninteractive_component_ids(self):
        manifest = self.manifest(self.package)
        for number in range(8):
            name = f"{number:03b}"
            components = [
                component
                for bit, component in enumerate(("camera", "physics", "ai"))
                if number & (1 << bit)
            ]
            with self.subTest(variant=name, components=components):
                root = self.root / f"variant-{name}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                result = self.run_setup(package, game, action="Custom", components=components)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    sha256(game / "game/sys/game.dll"),
                    manifest["game_variants"][name]["sha256"],
                )

    def test_interactive_prompts_use_generated_labels_descriptions_and_defaults(self):
        manifest = self.manifest(self.package)
        for component in manifest["components"].values():
            component["default"] = False
        config = manifest["components"]["config"]
        config["label"] = "Metadata Configuration Label"
        config["description"] = "Metadata configuration description"
        self.write_manifest(self.package, manifest)
        result = self.run_setup(
            self.package,
            self.game,
            action="Custom",
            interactive=True,
            input_text="\n" * len(COMPONENT_IDS) + "y\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Metadata Configuration Label", result.stdout)
        self.assertIn("Metadata configuration description", result.stdout)
        self.assertIn("[y/N]", result.stdout)
        self.assertFalse((self.game / "game/usr/config.cfg").exists())
        self.assertFalse((self.game / "game/usr/ui/cosetup.sc").exists())
        self.assert_stock(self.game)

    def test_metadata_dependencies_and_conflicts_fail_before_changes(self):
        cases = []
        dependency = self.manifest(self.package)
        dependency["components"]["config"]["requires"] = ["camera"]
        cases.append((dependency, ["config"], "requires 'camera'"))
        conflict = self.manifest(self.package)
        conflict["components"]["camera"]["conflicts"] = ["physics"]
        cases.append((conflict, ["camera", "physics"], "conflicts with 'physics'"))
        for index, (manifest, components, message) in enumerate(cases):
            with self.subTest(message=message):
                root = self.root / f"invalid-{index}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                self.write_manifest(package, manifest)
                result = self.run_setup(package, game, action="Custom", components=components)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertEqual(sha256(game / "game/sys/game.dll"), manifest["game_variants"]["000"]["sha256"])
                self.assertFalse((game / "game/usr/config.cfg").exists())
                self.assertFalse((package / "installer/backups").exists())

    def test_unknown_component_lists_generated_stable_ids(self):
        result = self.run_setup(self.package, self.game, action="Custom", components=["missing"])
        self.assertEqual(result.returncode, 1)
        output = result.stdout + result.stderr
        self.assertIn("Unknown component 'missing'", output)
        for component in COMPONENT_IDS:
            self.assertIn(component, output)

    def test_new_profile_warning_is_driven_by_component_metadata(self):
        manifest = self.manifest(self.package)
        for component in manifest["components"].values():
            component["new_profile"] = False
        manifest["components"]["config"]["new_profile"] = True
        self.write_manifest(self.package, manifest)
        result = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("new profile", (result.stdout + result.stderr).lower())

    def test_powershell_consumes_data_components_without_hardcoded_ids(self):
        manifest = self.manifest(self.package)
        manifest["components"]["extra"] = {
            "label": "Extra",
            "description": "Extra data",
            "default": True,
            "requires": [],
            "conflicts": [],
            "new_profile": False,
            "kind": "data",
            "target": None,
        }
        payload = self.package / "installer/payload/data/ui/extra.sc"
        payload.write_bytes(b"spawn ui_window {\r\n}\r\n")
        manifest["data_components"]["extra"] = ["data/ui/extra.sc"]
        manifest["payloads"]["data/ui/extra.sc"] = sha256(payload)
        self.write_manifest(self.package, manifest)
        result = self.run_setup(self.package, self.game, action="Custom", components=["extra"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(self.game / "game/usr/ui/extra.sc"), sha256(payload))


class InstallerSafetyTests(InstallerCase):
    def test_required_game_files_are_validated_before_changes(self):
        for relative in ("prism3d.exe", "game/sys/game.dll", "sys/dx8.dll"):
            with self.subTest(path=relative):
                root = self.root / f"missing-{relative.replace('/', '-')}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                (game / relative).unlink()
                result = self.run_setup(package, game, action="Custom", components=["config"])
                self.assertEqual(result.returncode, 1)
                self.assertIn("Not a Hard Truck game folder", result.stdout + result.stderr)
                self.assertFalse((package / "installer/backups").exists())

    def test_unknown_game_and_dx8_hashes_are_rejected_without_backup(self):
        for relative in ("game/sys/game.dll", "sys/dx8.dll"):
            with self.subTest(path=relative):
                root = self.root / relative.replace("/", "-")
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                target = game / relative
                target.write_bytes(target.read_bytes() + b"foreign")
                before = sha256(target)
                result = self.run_setup(package, game, action="Custom", components=["config"])
                self.assertEqual(result.returncode, 1)
                self.assertIn("unsupported", (result.stdout + result.stderr).lower())
                self.assertEqual(sha256(target), before)
                self.assertFalse((package / "installer/backups").exists())

    def test_corrupt_selected_payload_fails_before_any_change(self):
        payload = self.package / "installer/payload/data/config.cfg"
        payload.write_bytes(payload.read_bytes() + b"corrupt")
        result = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing or corrupt", result.stdout + result.stderr)
        self.assert_stock(self.game)
        self.assertFalse((self.game / "game/usr/config.cfg").exists())
        self.assertFalse((self.package / "installer/backups").exists())

    def test_mid_install_failure_rolls_back_every_changed_file(self):
        (self.game / "game/usr/ui/mmain.sc").mkdir(parents=True)
        result = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("restored", (result.stdout + result.stderr).lower())
        self.assert_stock(self.game)
        self.assertFalse((self.game / "game/usr/config.cfg").exists())
        backups = self.backup_manifests(self.package)
        self.assertEqual(len(backups), 1)
        failure = json.loads(backups[0].read_text(encoding="utf-8"))
        self.assertEqual(failure["schema_version"], 1)
        self.assertEqual(failure["status"], "RolledBack")

    def test_uninstall_preserves_user_modified_files_and_marks_partial(self):
        installed = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        modified = self.game / "game/usr/ui/mmain.sc"
        modified.write_bytes(modified.read_bytes() + b"\r\n// user edit\r\n")
        expected = sha256(modified)
        result = self.run_setup(self.package, self.game, action="Uninstall")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(modified), expected)
        self.assertIn("Skipping user-modified", result.stdout + result.stderr)
        manifests = [json.loads(path.read_text(encoding="utf-8")) for path in self.backup_manifests(self.package)]
        self.assertIn("UninstallPartial", [item["status"] for item in manifests])

    def test_uninstall_rejects_destination_outside_manifest_allowlist(self):
        installed = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        backup = self.backup_manifests(self.package)[0]
        installation = json.loads(backup.read_text(encoding="utf-8"))
        installation["entries"][0]["destination"] = "../outside"
        backup.write_text(json.dumps(installation), encoding="utf-8")
        result = self.run_setup(self.package, self.game, action="Uninstall")
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout + result.stderr, "Unsafe relative path|not an installer-owned file")
        self.assertFalse((self.root / "outside").exists())

    def test_interactive_cancel_and_noninteractive_menu_change_nothing(self):
        cancelled = self.run_setup(
            self.package,
            self.game,
            action="Custom",
            interactive=True,
            input_text="\n" * len(COMPONENT_IDS) + "n\n",
        )
        self.assertEqual(cancelled.returncode, 1)
        self.assertIn("cancelled", (cancelled.stdout + cancelled.stderr).lower())
        self.assertFalse((self.package / "installer/backups").exists())
        menu = self.run_setup(self.package, self.game, action="Menu")
        self.assertEqual(menu.returncode, 1)
        self.assertIn("Noninteractive setup requires", menu.stdout + menu.stderr)
        quit_result = self.run_setup(self.package, action="Quit")
        self.assertEqual(quit_result.returncode, 0, quit_result.stdout + quit_result.stderr)


class InstallerDetectionTests(InstallerCase):
    def install_detected(self, inputs, env, game=None, package=None):
        package = package or self.package
        return self.run_setup(
            package,
            interactive=True,
            input_text=inputs,
            env=env,
        )

    def test_registry_candidate_confirmation(self):
        steam = self.root / "steam"
        game = self.make_game(steam / "steamapps/common", "Hard Truck")
        result = self.install_detected(
            "y\ny\n",
            {"HARDTRUCK_DISCOVERY_REGISTRY_JSON": json.dumps({"HKLM": str(steam)})},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Detected Hard Truck installation", result.stdout)
        installation = json.loads(self.backup_manifests(self.package)[0].read_text(encoding="utf-8"))
        self.assertEqual(installation["game_path"], str(game))

    def test_multiple_candidates_reject_oversized_number_then_use_numbered_choice(self):
        steam_one = self.root / "steam-one"
        steam_two = self.root / "steam-two"
        self.make_game(steam_one / "steamapps/common", "Hard Truck")
        other = self.make_game(steam_two / "steamapps/common", "Hard Truck")
        result = self.install_detected(
            "999999999999999999999999\n2\ny\n",
            {"HARDTRUCK_DISCOVERY_REGISTRY_JSON": json.dumps({"one": str(steam_one), "two": str(steam_two)})},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Select a listed number", result.stdout)
        installation = json.loads(self.backup_manifests(self.package)[0].read_text(encoding="utf-8"))
        self.assertEqual(installation["game_path"], str(other))

    def test_manual_path_when_discovery_has_no_candidates(self):
        result = self.install_detected(
            str(self.game) + "\ny\n",
            {
                "HARDTRUCK_DISCOVERY_REGISTRY_JSON": "{}",
                "HARDTRUCK_DISCOVERY_PROGRAM_ROOTS": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.backup_manifests(self.package))

    def test_malformed_registry_falls_back_to_program_roots(self):
        programs = self.root / "programs"
        programs.mkdir()
        game = self.make_game(programs, "Hard Truck")
        result = self.install_detected(
            "y\ny\n",
            {
                "HARDTRUCK_DISCOVERY_REGISTRY_JSON": "not json",
                "HARDTRUCK_DISCOVERY_PROGRAM_ROOTS": str(programs),
            },
            game=game,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.backup_manifests(self.package))

    def test_utf8_steam_vdf_library_candidate(self):
        steam = self.root / "steam"
        (steam / "config").mkdir(parents=True)
        library = self.root / "bibliothèque"
        game = self.make_game(library / "steamapps/common", "Hard Truck")
        escaped = str(library).replace("\\", "\\\\")
        (steam / "config/libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n  "0"\n  {\n    "path" "' + escaped + '"\n  }\n}\n',
            encoding="utf-8",
        )
        result = self.install_detected(
            "y\ny\n",
            {
                "HARDTRUCK_DISCOVERY_REGISTRY_JSON": json.dumps({"steam": str(steam)}),
                "HARDTRUCK_DISCOVERY_PROGRAM_ROOTS": "",
            },
            game=game,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.backup_manifests(self.package))

    def test_malformed_vdf_does_not_hide_valid_steam_root_candidate(self):
        steam = self.root / "steam"
        (steam / "config").mkdir(parents=True)
        (steam / "config/libraryfolders.vdf").write_text("not valid VDF \x00", encoding="utf-8")
        game = self.make_game(steam / "steamapps/common", "Hard Truck")
        result = self.install_detected(
            "y\ny\n",
            {
                "HARDTRUCK_DISCOVERY_REGISTRY_JSON": json.dumps({"steam": str(steam)}),
                "HARDTRUCK_DISCOVERY_PROGRAM_ROOTS": "",
            },
            game=game,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installation = json.loads(self.backup_manifests(self.package)[0].read_text(encoding="utf-8"))
        self.assertEqual(installation["game_path"], str(game))

    def test_previous_backup_manifest_is_a_detection_candidate(self):
        backup = self.package / "installer/backups/previous"
        backup.mkdir(parents=True)
        (backup / "installation.json").write_text(json.dumps({
            "schema_version": 1,
            "status": "Installed",
            "game_path": str(self.game),
            "entries": [],
        }), encoding="utf-8")
        result = self.install_detected(
            "y\ny\n",
            {
                "HARDTRUCK_DISCOVERY_REGISTRY_JSON": "{}",
                "HARDTRUCK_DISCOVERY_PROGRAM_ROOTS": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.backup_manifests(self.package))

    def test_duplicate_and_invalid_candidates_are_ignored(self):
        invalid = self.root / "invalid"
        invalid.mkdir()
        steam = self.root / "steam"
        self.make_game(steam / "steamapps/common", "Hard Truck")
        result = self.install_detected(
            "y\ny\n",
            {
                "HARDTRUCK_DISCOVERY_REGISTRY_JSON": json.dumps({
                    "invalid": str(invalid),
                    "first": str(steam),
                    "duplicate": str(steam) + "/",
                }),
                "HARDTRUCK_DISCOVERY_PROGRAM_ROOTS": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Detected Hard Truck installations:", result.stdout)
        self.assertIn("Detected Hard Truck installation:", result.stdout)


@unittest.skipIf(PWSH is None, "PowerShell 7 is required for installer runtime tests")
class FreshExtractedDeliverableTests(InstallerCase):
    def test_zip_extracted_setup_installs_and_uninstalls_with_batch_entry_point(self):
        archive = self.root / "HardTruckModern-v3.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in sorted(self.release.rglob("*")):
                if path.is_file():
                    output.write(path, Path("HardTruckModern") / path.relative_to(self.release))
        extracted = self.root / "extracted"
        with zipfile.ZipFile(archive) as source:
            self.assertIsNone(source.testzip())
            source.extractall(extracted)
        package = extracted / "HardTruckModern"
        self.assertEqual(
            (package / "Setup.bat").read_bytes(),
            (ROOT / "tools/templates/Setup.bat").read_bytes(),
        )
        self.assertFalse(list(package.rglob("__pycache__")))
        self.assertFalse(list(package.rglob("research")))
        result = self.run_setup(package, self.game, action="Custom", components=["config", "physics"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.game / "game/usr/ui/cosetup.sc").is_file())
        uninstall = self.run_setup(package, self.game, action="Uninstall")
        self.assertEqual(uninstall.returncode, 0, uninstall.stdout + uninstall.stderr)
        self.assertFalse((self.game / "game/usr/ui/cosetup.sc").exists())


def build_release(cls):
    cls.temporary = tempfile.TemporaryDirectory(prefix="htmod-installer-release-")
    cls.release = Path(cls.temporary.name) / "HardTruckModern"
    build_windows_release.build(SimpleNamespace(
        output=cls.release,
        game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
        dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
    ))


for test_class in (
    InstallerMetadataRuntimeTests,
    InstallerSafetyTests,
    InstallerDetectionTests,
    FreshExtractedDeliverableTests,
):
    test_class.setUpClass = classmethod(build_release)
    test_class.tearDownClass = classmethod(lambda cls: cls.temporary.cleanup())
del test_class


if __name__ == "__main__":
    unittest.main()
