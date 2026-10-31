import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import build_windows_release
from tests.legacy_variants import (
    CURRENT_ALL_COMPONENTS_SHA256,
    FIXED_EYE_GAME_SHA256,
    LEGACY_GAME_SHA256,
    write_fixed_eye_variant,
    write_legacy_variant,
)


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(
    os.environ.get("HTMOD_RELEASE_ZIP") or os.environ.get("HTMOD_TEST_PATCHER_EXE"),
    "a real frozen patcher is required for shell installer tests",
)
class ShellInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release_temporary = tempfile.TemporaryDirectory(prefix="htmod-shell-release-")
        cls.release = Path(cls.release_temporary.name) / "HardTruckModern"
        archive = os.environ.get("HTMOD_RELEASE_ZIP")
        if archive:
            with zipfile.ZipFile(archive) as source:
                if source.testzip() is not None:
                    raise RuntimeError(f"corrupt release ZIP: {archive}")
                script_info = source.getinfo("HardTruckModern/INSTALL.sh")
                if not (script_info.external_attr >> 16) & 0o111:
                    raise RuntimeError("release INSTALL.sh is not executable in ZIP metadata")
                source.extractall(cls.release_temporary.name)
            if not cls.release.is_dir():
                raise RuntimeError(f"release ZIP has no HardTruckModern root: {archive}")
            script = cls.release / "INSTALL.sh"
            script.chmod(script.stat().st_mode | 0o111)
            return
        from tests.release_fixtures import build_installer_fixture
        build_installer_fixture(cls.release)

    @classmethod
    def tearDownClass(cls):
        cls.release_temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="htmod-shell-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        shutil.copytree(self.release, self.package)
        self.game = self.root / "game with spaces"
        (self.game / "game/sys").mkdir(parents=True)
        (self.game / "sys").mkdir()
        for relative in ("prism3d.exe", "game/sys/game.dll", "sys/dx8.dll"):
            shutil.copyfile(build_windows_release.default_test_base_file(Path(relative)), self.game / relative)

    def run_install(self, package=None, game=None):
        package = package or self.package
        return subprocess.run(
            ["/bin/sh", str(package / "INSTALL.sh"), "--yes", str(game or self.game)],
            text=True,
            capture_output=True,
            timeout=120,
        )

    def manifest(self):
        return json.loads((self.package / "installer/payload/manifest.json").read_text(encoding="utf-8"))

    def test_builder_generates_executable_python_free_shell_installer_and_manifest(self):
        script = self.package / "INSTALL.sh"
        payload_manifest = self.package / "installer/payload/install-manifest.tsv"
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK))
        self.assertTrue(payload_manifest.is_file())
        text = script.read_text(encoding="utf-8")
        self.assertNotIn("python", text.casefold())
        records = [line.split("\t") for line in payload_manifest.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len([r for r in records if r[0] == "variant"]), 16)
        self.assertEqual(len([r for r in records if r[0] == "data"]), 20)
        self.assertIn(["install-game", "game/sys/game.dll", self.manifest()["game_variants"]["1111"]["sha256"]], records)

    def test_install_all_validates_payloads_and_backs_up_every_replaced_or_created_file(self):
        original_game = (self.game / "game/sys/game.dll").read_bytes()
        original_dx8 = (self.game / "sys/dx8.dll").read_bytes()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = self.manifest()
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), manifest["game_variants"]["1111"]["sha256"])
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), manifest["dx8"]["vsync"]["sha256"])
        data_records = []
        for files in manifest["data_components"].values():
            data_records.extend(files)
        self.assertEqual(len(data_records), 20)
        for payload in data_records:
            relative = payload.removeprefix("data/")
            self.assertEqual(sha256(self.game / "game/usr" / relative), manifest["payloads"][payload])
        backups = list((self.package / "installer/backups").glob("shell-*/installation.tsv"))
        self.assertEqual(len(backups), 1)
        backup = backups[0].parent
        self.assertEqual((backup / "files/game/sys/game.dll").read_bytes(), original_game)
        self.assertEqual((backup / "files/sys/dx8.dll").read_bytes(), original_dx8)
        entries = [line.split("\t") for line in backups[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(entries), 22)
        self.assertEqual(len([entry for entry in entries if entry[0] == "created"]), 20)

    def test_estonian_install_all_preserves_sources_and_installs_all_data(self):
        from tests.estonian_variants import (
            ESTONIAN_DX8_SHA256,
            ESTONIAN_GAME_SHA256,
            estonian_binary_images,
        )
        from tests.release_fixtures import write_current_payload

        payload = self.root / "current-payload"
        write_current_payload(payload)
        game_source, dx8_source = estonian_binary_images(payload)
        (self.game / "game/sys/game.dll").write_bytes(game_source)
        (self.game / "sys/dx8.dll").write_bytes(dx8_source)

        result = self.run_install()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), CURRENT_ALL_COMPONENTS_SHA256)
        self.assertEqual(
            sha256(self.game / "sys/dx8.dll"),
            "c900a47f5ac5e05116adea04895b8ac4171522a7812b50fc6a25b931bdb4b519",
        )
        manifest = self.manifest()
        for files in manifest["data_components"].values():
            for data in files:
                relative = data.removeprefix("data/")
                self.assertEqual(sha256(self.game / "game/usr" / relative), manifest["payloads"][data])
        backups = list((self.package / "installer/backups").glob("shell-*/installation.tsv"))
        self.assertEqual(len(backups), 1)
        backup = backups[0].parent
        entries = [line.split("\t") for line in backups[0].read_text(encoding="utf-8").splitlines()]
        for relative, source, source_hash, output_hash in (
            ("game/sys/game.dll", game_source, ESTONIAN_GAME_SHA256, CURRENT_ALL_COMPONENTS_SHA256),
            (
                "sys/dx8.dll",
                dx8_source,
                ESTONIAN_DX8_SHA256,
                "c900a47f5ac5e05116adea04895b8ac4171522a7812b50fc6a25b931bdb4b519",
            ),
        ):
            with self.subTest(path=relative):
                self.assertIn(["replaced", relative, source_hash, output_hash, f"files/{relative}"], entries)
                self.assertEqual((backup / "files" / relative).read_bytes(), source)

    def test_directory_and_symlink_destinations_are_rejected_before_any_change(self):
        for kind in ("directory", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / "package"
                shutil.copytree(self.package, package)
                game = root / "game"
                shutil.copytree(self.game, game)
                original_game = (game / "game/sys/game.dll").read_bytes()
                original_dx8 = (game / "sys/dx8.dll").read_bytes()
                destination = game / "game/usr/config.cfg"
                destination.parent.mkdir(parents=True, exist_ok=True)
                if kind == "directory":
                    destination.mkdir()
                    sentinel = destination / "keep.txt"
                    sentinel.write_bytes(b"keep")
                else:
                    outside = root / "outside.cfg"
                    outside.write_bytes(b"outside original")
                    destination.symlink_to(outside)

                result = self.run_install(package=package, game=game)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertRegex(result.stdout + result.stderr, "regular file|symlink")
                self.assertEqual((game / "game/sys/game.dll").read_bytes(), original_game)
                self.assertEqual((game / "sys/dx8.dll").read_bytes(), original_dx8)
                self.assertFalse((package / "installer/backups").exists())
                self.assertFalse(list(game.rglob("*.htmod-shell-*")))
                self.assertFalse(list(game.rglob("*.htmod-rollback-*")))
                if kind == "directory":
                    self.assertEqual((destination / "keep.txt").read_bytes(), b"keep")
                    self.assertEqual(list(destination.iterdir()), [destination / "keep.txt"])
                else:
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(outside.read_bytes(), b"outside original")

    def test_mid_install_failure_rolls_back_every_earlier_file_and_preserves_sentinel(self):
        manifest = self.manifest()
        original_game = (self.game / "game/sys/game.dll").read_bytes()
        original_dx8 = (self.game / "sys/dx8.dll").read_bytes()
        sentinel = self.game / "game/usr/defs"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"user sentinel; mkdir must fail")

        result = self.run_install()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Rollback complete", result.stdout + result.stderr)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), original_game)
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), original_dx8)
        self.assertEqual(sentinel.read_bytes(), b"user sentinel; mkdir must fail")
        for relative in ("config.cfg", "ui/mmain.sc", "ui/cosetup.sc"):
            self.assertFalse((self.game / "game/usr" / relative).exists(), relative)
        backups = list((self.package / "installer/backups").glob("shell-*/installation.tsv"))
        self.assertEqual(len(backups), 1)
        self.assertIn("game/sys/game.dll", backups[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["game_variants"]["0000"]["sha256"], sha256(self.game / "game/sys/game.dll"))

    def test_every_released_legacy_game_hash_upgrades_with_backup(self):
        for name, legacy_hash in LEGACY_GAME_SHA256.items():
            with self.subTest(variant=name, legacy_hash=legacy_hash), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / "package"
                shutil.copytree(self.package, package)
                game = root / "game"
                shutil.copytree(self.game, game)
                target = game / "game/sys/game.dll"
                write_legacy_variant(package / "installer/payload", name, target)
                original = target.read_bytes()

                result = self.run_install(package=package, game=game)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
                backups = list((package / "installer/backups").glob("shell-*/installation.tsv"))
                self.assertEqual(len(backups), 1)
                backup = backups[0].parent / "files/game/sys/game.dll"
                self.assertEqual(backup.read_bytes(), original)
                self.assertEqual(sha256(backup), legacy_hash)

    def test_every_broken_fixed_eye_variant_upgrades_with_backup(self):
        for name, fixed_eye_hash in FIXED_EYE_GAME_SHA256.items():
            with self.subTest(variant=name, fixed_eye_hash=fixed_eye_hash), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / "package"
                shutil.copytree(self.package, package)
                game = root / "game"
                shutil.copytree(self.game, game)
                target = game / "game/sys/game.dll"
                write_fixed_eye_variant(package / "installer/payload", name, target)
                original = target.read_bytes()
                result = self.run_install(package=package, game=game)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
                self.assertNotEqual(sha256(target), fixed_eye_hash)
                backups = list((package / "installer/backups").glob("shell-*/installation.tsv"))
                self.assertEqual(len(backups), 1)
                self.assertEqual((backups[0].parent / "files/game/sys/game.dll").read_bytes(), original)

    def test_every_current_game_variant_reinstalls_with_incoming_backup(self):
        from tests.release_fixtures import current_binary_images

        for name, source in current_binary_images()[0].items():
            with self.subTest(variant=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / "package"
                shutil.copytree(self.package, package)
                game = root / "game"
                shutil.copytree(self.game, game)
                target = game / "game/sys/game.dll"
                target.write_bytes(source)
                result = self.run_install(package=package, game=game)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
                backups = list((package / "installer/backups").glob("shell-*/installation.tsv"))
                self.assertEqual(len(backups), 1)
                backup = backups[0].parent / "files/game/sys/game.dll"
                if name == "1111":
                    self.assertFalse(backup.exists())
                else:
                    self.assertEqual(backup.read_bytes(), source)

    def test_corrupt_patcher_catalog_and_wrong_staged_hash_fail_before_backup(self):
        for failure in ("patcher", "catalog", "wrong-hash"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / "package"
                shutil.copytree(self.package, package)
                game = root / "game"
                shutil.copytree(self.game, game)
                if failure == "patcher":
                    (package / "installer/payload/htmod.exe").write_bytes(b"not an executable")
                elif failure == "catalog":
                    (package / "installer/payload/migrations.json").write_text("{", encoding="utf-8")
                else:
                    manifest = package / "installer/payload/install-manifest.tsv"
                    lines = manifest.read_text(encoding="utf-8").splitlines()
                    lines = [
                        f"install-game\tgame/sys/game.dll\t{'0' * 64}" if line.startswith("install-game\t") else line
                        for line in lines
                    ]
                    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
                before_game = (game / "game/sys/game.dll").read_bytes()
                before_dx8 = (game / "sys/dx8.dll").read_bytes()
                result = self.run_install(package=package, game=game)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((game / "game/sys/game.dll").read_bytes(), before_game)
                self.assertEqual((game / "sys/dx8.dll").read_bytes(), before_dx8)
                self.assertFalse((package / "installer/backups").exists())
                self.assertFalse((game / "game/usr/config.cfg").exists())

    def test_unknown_source_or_corrupt_payload_changes_nothing(self):
        for failure in ("unknown-source", "corrupt-payload"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = self.root / failure
                shutil.copytree(self.package, package)
                game = root / "game"
                shutil.copytree(self.game, game)
                if failure == "unknown-source":
                    target = game / "game/sys/game.dll"
                    target.write_bytes(target.read_bytes() + b"foreign")
                    before = sha256(target)
                else:
                    target = package / "installer/payload/data/config.cfg"
                    target.write_bytes(target.read_bytes() + b"corrupt")
                    before = sha256(game / "game/sys/game.dll")
                result = self.run_install(package=package, game=game)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertRegex(result.stdout + result.stderr, "unknown|unsupported|corrupt|SHA-256")
                self.assertEqual(sha256(game / "game/sys/game.dll"), before)
                self.assertFalse((game / "game/usr/config.cfg").exists())
                self.assertFalse((package / "installer/backups").exists())


class ShellSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release_temporary = tempfile.TemporaryDirectory(prefix="htmod-shell-selection-")
        cls.root = Path(cls.release_temporary.name)
        modpack = cls.root / "modpack"
        shutil.copytree(ROOT / "modpack", modpack)
        release_path = modpack / "release.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        release["mods"].insert(1, "synthetic")
        release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
        synthetic = modpack / "mods/synthetic"
        (synthetic / "files").mkdir(parents=True)
        (synthetic / "files/marker.txt").write_bytes(b"synthetic\n")
        (synthetic / "mod.json").write_text(json.dumps({
            "schema_version": 1,
            "id": "synthetic",
            "version": "1",
            "kind": "data",
            "setup": {
                "label": "Synthetic",
                "description": "Synthetic release component",
                "default": True,
            },
            "requires": ["config"],
            "conflicts": [],
            "new_profile": False,
            "files": [{
                "source": "marker.txt",
                "destination": "game/usr/synthetic.txt",
            }],
        }, indent=2) + "\n", encoding="utf-8")
        cls.component_ids = release["mods"]
        cls.release = cls.root / "release"
        patcher = cls.root / "htmod.exe"
        from tests.release_fixtures import fake_pe64
        fake_pe64(patcher)
        args = SimpleNamespace(
            output=cls.release,
            game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
            dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
            patcher_exe=patcher,
            migrations=ROOT / "modpack/migrations.json",
        )
        with mock.patch.object(
            build_windows_release,
            "load_release",
            return_value=build_windows_release.load_release(cls.root),
        ):
            build_windows_release.build(args)

        cls.bin = cls.root / "bin"
        cls.bin.mkdir()
        wine = cls.bin / "wine"
        wine.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf '%s\\n' \"$@\" > \"$HTMOD_TEST_ARGUMENTS\"\n"
            "shift\n"
            "exec python3 \"$HTMOD_TEST_CLI\" --root \"$HTMOD_TEST_ROOT\" \"$@\"\n",
            encoding="utf-8",
        )
        winepath = cls.bin / "winepath"
        winepath.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "[ \"$1\" = \"-w\" ]\n"
            "printf '%s\\n' \"$2\"\n",
            encoding="utf-8",
        )
        wine.chmod(0o755)
        winepath.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.release_temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="htmod-shell-selection-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        shutil.copytree(self.release, self.package)
        self.game = self.root / "game"
        (self.game / "game/sys").mkdir(parents=True)
        (self.game / "sys").mkdir()
        for relative in ("prism3d.exe", "game/sys/game.dll", "sys/dx8.dll"):
            shutil.copyfile(build_windows_release.default_test_base_file(Path(relative)), self.game / relative)
        self.original_game = (self.game / "game/sys/game.dll").read_bytes()
        self.original_dx8 = (self.game / "sys/dx8.dll").read_bytes()
        self.arguments = self.root / "arguments"
        self.environment = os.environ.copy()
        self.environment.update({
            "PATH": str(self.bin) + os.pathsep + self.environment["PATH"],
            "HTMOD_TEST_ARGUMENTS": str(self.arguments),
            "HTMOD_TEST_CLI": str(ROOT / "tools/htmod.py"),
            "HTMOD_TEST_ROOT": str(self.release_temporary_path),
        })

    @property
    def release_temporary_path(self):
        return Path(self.release_temporary.name)

    def run_install(self):
        return subprocess.run(
            ["/bin/sh", str(self.package / "INSTALL.sh"), "--yes", str(self.game)],
            text=True,
            capture_output=True,
            timeout=120,
            env=self.environment,
        )

    def manifest_path(self):
        return self.package / "installer/payload/install-manifest.tsv"

    def replace_selection(self, *selections):
        path = self.manifest_path()
        lines = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("install-mods\t")
        ]
        lines.extend(selections)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def assert_failed_without_changes(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.original_game)
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.original_dx8)
        self.assertFalse((self.game / "game/usr/config.cfg").exists())
        self.assertFalse((self.game / "game/usr/synthetic.txt").exists())
        self.assertFalse((self.package / "installer/backups").exists())

    def test_manifest_has_one_release_ordered_selection(self):
        records = [
            line.split("\t")
            for line in self.manifest_path().read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [record for record in records if record[0] == "install-mods"],
            [["install-mods", "-", " ".join(self.component_ids)]],
        )
        self.assertEqual(len([record for record in records if record[0] == "variant"]), 16)
        self.assertEqual(len([record for record in records if record[0] == "data"]), 21)

    def test_install_passes_each_release_component_once_in_order(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = self.arguments.read_text(encoding="utf-8").splitlines()
        selected = [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == "--mod"]
        self.assertEqual(arguments[1], "render-quick-setup")
        self.assertEqual(selected, self.component_ids)
        self.assertEqual((self.game / "game/usr/synthetic.txt").read_bytes(), b"synthetic\n")
        quick_setup = self.game / "game/usr/ui/cosetup.sc"
        self.assertIn(b"Enabled: configuration, camera, physics,<br>", quick_setup.read_bytes())
        backups = list((self.package / "installer/backups").glob("shell-*/installation.tsv"))
        self.assertEqual(len(backups), 1)
        entries = [line.split("\t") for line in backups[0].read_text(encoding="utf-8").splitlines()]
        entry = next(item for item in entries if item[1] == "game/usr/ui/cosetup.sc")
        self.assertEqual(entry[3], sha256(quick_setup))

    def test_missing_duplicate_and_malformed_selections_fail_before_changes(self):
        valid = "install-mods\t-\t" + " ".join(self.component_ids)
        cases = {
            "missing": (),
            "duplicate": (valid, valid),
            "empty": ("install-mods\t-\t",),
            "whitespace": ("install-mods\t-\t   ",),
            "relative": ("install-mods\tnot-internal\t" + " ".join(self.component_ids),),
            "extra-field": (valid + "\textra",),
            "empty-middle-column": ("install-mods\t-\t\tconfig",),
            "trailing-empty-column": ("install-mods\t-\tconfig\t",),
            "invalid-id": ("install-mods\t-\tconfig invalid-id",),
        }
        for name, selections in cases.items():
            with self.subTest(selection=name), tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / "package"
                shutil.copytree(self.package, package)
                original_package = self.package
                self.package = package
                try:
                    self.replace_selection(*selections)
                    self.arguments.unlink(missing_ok=True)
                    result = self.run_install()
                    self.assert_failed_without_changes(result)
                    self.assertFalse(self.arguments.exists(), "stage ran for an invalid install-mods record")
                    self.assertRegex(result.stdout + result.stderr, "install-mods|selection")
                finally:
                    self.package = original_package

    def test_unknown_and_incomplete_selections_are_rejected_by_stage_before_changes(self):
        for name, selection, message in (
            ("unknown", "config missing", "unknown mod"),
            ("dependency", "synthetic", "requires"),
        ):
            with self.subTest(selection=name):
                self.replace_selection("install-mods\t-\t" + selection)
                result = self.run_install()
                self.assert_failed_without_changes(result)
                self.assertRegex(result.stdout + result.stderr, message)


if __name__ == "__main__":
    unittest.main()
