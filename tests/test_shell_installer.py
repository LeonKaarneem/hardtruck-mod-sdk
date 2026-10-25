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
from tests.legacy_variants import (
    CURRENT_ALL_COMPONENTS_SHA256,
    FIXED_EYE_ALL_COMPONENTS_SHA256,
    LEGACY_GAME_SHA256,
    write_fixed_eye_all_components,
    write_legacy_variant,
)


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
        build_windows_release.build(SimpleNamespace(
            output=cls.release,
            game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
            dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
        ))

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
            timeout=30,
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
        self.assertEqual(len([r for r in records if r[0] == "data"]), 19)
        self.assertIn(["install-game", "game-1111.dll", self.manifest()["game_variants"]["1111"]["sha256"]], records)

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
        self.assertEqual(len(data_records), 19)
        for payload in data_records:
            relative = payload.removeprefix("data/")
            self.assertEqual(sha256(self.game / "game/usr" / relative), manifest["payloads"][payload])
        backups = list((self.package / "installer/backups").glob("shell-*/installation.tsv"))
        self.assertEqual(len(backups), 1)
        backup = backups[0].parent
        self.assertEqual((backup / "files/game/sys/game.dll").read_bytes(), original_game)
        self.assertEqual((backup / "files/sys/dx8.dll").read_bytes(), original_dx8)
        entries = [line.split("\t") for line in backups[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(entries), 21)
        self.assertEqual(len([entry for entry in entries if entry[0] == "created"]), 19)

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

    def test_broken_fixed_eye_release_upgrades_with_backup(self):
        target = self.game / "game/sys/game.dll"
        write_fixed_eye_all_components(self.package / "installer/payload", target)
        original = target.read_bytes()
        result = self.run_install()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
        self.assertNotEqual(sha256(target), FIXED_EYE_ALL_COMPONENTS_SHA256)
        backups = list((self.package / "installer/backups").glob("shell-*/installation.tsv"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0].parent / "files/game/sys/game.dll").read_bytes(), original)

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
                self.assertRegex(result.stdout + result.stderr, "unsupported|corrupt|SHA-256")
                self.assertEqual(sha256(game / "game/sys/game.dll"), before)
                self.assertFalse((game / "game/usr/config.cfg").exists())
                self.assertFalse((package / "installer/backups").exists())


if __name__ == "__main__":
    unittest.main()
