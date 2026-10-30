import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tests.legacy_variants import (
    FIXED_EYE_GAME_SHA256,
    LEGACY_GAME_SHA256,
    write_fixed_eye_variant,
    write_legacy_variant,
)
from tests.release_fixtures import write_current_payload

ROOT = Path(__file__).resolve().parents[1]
WINE = "/usr/bin/wine"
WINEPATH = "/usr/bin/winepath"


class FrozenSpecTests(unittest.TestCase):
    def test_spec_bundles_sdk_and_modpack_without_tests_research_or_payloads(self):
        spec = (ROOT / "tools/htmod-windows.spec").read_text(encoding="utf-8")
        self.assertIn("modpack", spec)
        self.assertIn("sdk", spec)
        self.assertIn("onefile", spec.lower())
        self.assertIn('excludes=["tests", "research"]', spec)
        for forbidden in ("test-base", "game-*.dll", "dx8-*.dll"):
            self.assertNotIn(forbidden, spec)

    def test_build_requirements_and_python_toolchain_are_pinned(self):
        requirements = (ROOT / "tools/requirements-windows-patcher.txt").read_text(encoding="utf-8")
        self.assertIn("pyinstaller==6.11.1", requirements)
        self.assertIn("pyinstaller-hooks-contrib==2024.10", requirements)
        for line in requirements.splitlines():
            if line.strip():
                self.assertIn("--hash=sha256:", line)
        script = (ROOT / "tools/build_windows_patcher.py").read_text(encoding="utf-8")
        self.assertIn("3.12.7", script)
        self.assertIn("1206721601a62c925d4e4a0dcfc371e88f2ddbe8c0c07962ebb2be9b5bde4570", script)
        self.assertIn("WINEPREFIX", script)
        self.assertIn('"--require-hashes"', script)
        self.assertIn("shutil.rmtree(prefix)", script)


@unittest.skipUnless(os.environ.get("HTMOD_TEST_PATCHER_EXE"), "real frozen patcher path not supplied")
class RealFrozenPatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = Path(os.environ["HTMOD_TEST_PATCHER_EXE"]).resolve()
        cls.temporary = tempfile.TemporaryDirectory(prefix="htmod-frozen-matrix-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.payload = cls.root / "payload"
        write_current_payload(cls.payload)
        cls.game_sources = []
        for name in LEGACY_GAME_SHA256:
            cls.game_sources.append((f"current-{name}", (cls.payload / f"game-{name}.dll").read_bytes()))
        for name in LEGACY_GAME_SHA256:
            path = cls.root / f"legacy-{name}.dll"
            write_legacy_variant(cls.payload, name, path)
            cls.game_sources.append((f"legacy-{name}", path.read_bytes()))
        for name in FIXED_EYE_GAME_SHA256:
            path = cls.root / f"fixed-eye-{name}.dll"
            write_fixed_eye_variant(cls.payload, name, path)
            cls.game_sources.append((f"fixed-eye-{name}", path.read_bytes()))
        from tests.estonian_variants import estonian_binary_images
        estonian_game, estonian_dx8 = estonian_binary_images(cls.payload)
        cls.game_sources.append(("estonian", estonian_game))
        cls.dx8_sources = (
            ("stock", (cls.payload / "dx8-stock.dll").read_bytes()),
            ("vsync", (cls.payload / "dx8-vsync.dll").read_bytes()),
            ("estonian", estonian_dx8),
        )
        cls.golden = json.loads((ROOT / "tests/golden/v3.json").read_text(encoding="utf-8"))
        cls.game_dir = cls.root / "game with spaces"
        (cls.game_dir / "game/sys").mkdir(parents=True)
        (cls.game_dir / "sys").mkdir()
        cls.output = cls.root / "staged with spaces"
        cls.runtime_prefix = cls.root / "runtime-prefix"
        cls.runtime_prefix.mkdir()
        cls.wine_environment = {
            "PATH": "",
            "HOME": os.environ.get("HOME", ""),
            "WINEPREFIX": str(cls.runtime_prefix),
            "WINEDLLOVERRIDES": "mscoree,mshtml=",
        }

    def windows_path(self, path):
        path = Path(path).resolve()
        self.assertTrue(path.is_absolute())
        return "Z:" + str(path)

    def stage(self, selected, expected):
        arguments = [WINE, str(self.exe), "stage"]
        for name, value in (
            ("--game-dir", self.game_dir),
            ("--output-dir", self.output),
            ("--migrations", ROOT / "modpack/migrations.json"),
        ):
            arguments.extend((name, self.windows_path(value)))
        for mod in selected:
            arguments.extend(("--mod", mod))
        for target, expected_hash in expected.items():
            arguments.extend(("--expect", f"{target}={expected_hash}"))
        self.assertEqual(self.wine_environment["PATH"], "")
        result = subprocess.run(
            arguments,
            env=self.wine_environment,
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pe64_and_every_game_lineage_with_every_handler_without_python_on_path(self):
        data = self.exe.read_bytes()
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(data[pe_offset:pe_offset + 4], b"PE\0\0")
        self.assertEqual(struct.unpack_from("<H", data, pe_offset + 4)[0], 0x8664)
        expected = {"game/sys/game.dll": self.golden["game_variants"]["1111"]["sha256"]}
        selected = ("ui_keyboard", "ai", "physics", "camera", "config")
        for lineage, source in self.game_sources:
            with self.subTest(lineage=lineage):
                target = self.game_dir / "game/sys/game.dll"
                target.write_bytes(source)
                self.stage(selected, expected)
                self.assertEqual(hashlib.sha256((self.output / "game/sys/game.dll").read_bytes()).hexdigest(), expected["game/sys/game.dll"])
                self.assertEqual(target.read_bytes(), source)

    def test_zero_mods_old_camera_removal_and_every_dx8_source(self):
        game_target = self.game_dir / "game/sys/game.dll"
        _, old_camera = next((name, data) for name, data in self.game_sources if name == "legacy-0001")
        game_target.write_bytes(old_camera)
        self.stage([], {"game/sys/game.dll": self.golden["pristine"]["game_dll"]})
        self.assertEqual(
            hashlib.sha256((self.output / "game/sys/game.dll").read_bytes()).hexdigest(),
            self.golden["pristine"]["game_dll"],
        )
        for lineage, source in self.dx8_sources:
            for selected, expected_name in (([], "stock"), (["dx8_vsync"], "vsync")):
                with self.subTest(dx8=lineage, selected=selected):
                    (self.game_dir / "sys/dx8.dll").write_bytes(source)
                    self.stage(selected, {"sys/dx8.dll": self.golden["dx8"][expected_name]})
                    self.assertEqual(
                        hashlib.sha256((self.output / "sys/dx8.dll").read_bytes()).hexdigest(),
                        self.golden["dx8"][expected_name],
                    )


if __name__ == "__main__":
    unittest.main()
