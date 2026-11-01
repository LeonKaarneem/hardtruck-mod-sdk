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
        for forbidden in ("test-base", "game-*.dll", "dx8-*.dll", "prism3d-*.exe"):
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
        from tests.estonian_variants import estonian_binary_images, estonian_prism3d_image
        estonian_game, estonian_dx8 = estonian_binary_images(cls.payload)
        cls.game_sources.append(("estonian", estonian_game))
        cls.dx8_sources = (
            ("stock", (cls.payload / "dx8-stock.dll").read_bytes()),
            ("vsync", (cls.payload / "dx8-vsync.dll").read_bytes()),
            ("estonian", estonian_dx8),
        )
        cls.prism3d_sources = (
            ("stock", (cls.payload / "prism3d-stock.exe").read_bytes()),
            ("cap", (cls.payload / "prism3d-cap.exe").read_bytes()),
            ("estonian", estonian_prism3d_image(cls.payload)),
        )
        cls.golden = json.loads((ROOT / "tests/golden/v3.json").read_text(encoding="utf-8"))
        cls.game_dir = cls.root / "game with spaces"
        (cls.game_dir / "game/sys").mkdir(parents=True)
        (cls.game_dir / "sys").mkdir()
        (cls.game_dir / "game/sys/game.dll").write_bytes(cls.game_sources[0][1])
        (cls.game_dir / "sys/dx8.dll").write_bytes(cls.dx8_sources[0][1])
        (cls.game_dir / "prism3d.exe").write_bytes(cls.prism3d_sources[0][1])
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

    def plan(self, selected):
        arguments = [WINE, str(self.exe), "plan", "--exact"]
        for name, value in (
            ("--game-dir", self.game_dir),
            ("--migrations", ROOT / "modpack/migrations.json"),
        ):
            arguments.extend((name, self.windows_path(value)))
        for mod in selected:
            arguments.extend(("--mod", mod))
        self.assertEqual(self.wine_environment["PATH"], "")
        result = subprocess.run(
            arguments,
            env=self.wine_environment,
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(plan["mode"], "exact")
        return plan

    def stage(self, selected, expected):
        plan = self.plan(selected)
        if "ui_keyboard" in selected:
            self.assertIn("config", plan["selected"])
        changes = {change["target"]: change for change in plan["binary_changes"]}
        for target, expected_hash in expected.items():
            if target in changes:
                self.assertEqual(changes[target]["expected_sha256"], expected_hash)
            else:
                self.assertEqual(hashlib.sha256((self.game_dir / target).read_bytes()).hexdigest(), expected_hash)
        arguments = [WINE, str(self.exe), "stage"]
        for name, value in (
            ("--game-dir", self.game_dir),
            ("--output-dir", self.output),
            ("--migrations", ROOT / "modpack/migrations.json"),
        ):
            arguments.extend((name, self.windows_path(value)))
        for mod in plan["selected"]:
            arguments.extend(("--mod", mod))
        for target in ("game/sys/game.dll", "sys/dx8.dll", "prism3d.exe"):
            expected_hash = changes[target]["expected_sha256"] if target in changes else hashlib.sha256((self.game_dir / target).read_bytes()).hexdigest()
            arguments.extend(("--expect", f"{target}={expected_hash}"))
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
        selected = ("ui_keyboard", "ai", "physics", "camera")
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

    def test_every_prism3d_source_stages_stock_and_cap_without_python_on_path(self):
        for lineage, source in self.prism3d_sources:
            for selected, expected_name in (([], "stock"), (["frame_cap"], "cap")):
                with self.subTest(prism3d=lineage, selected=selected):
                    target = self.game_dir / "prism3d.exe"
                    target.write_bytes(source)
                    self.stage(selected, {"prism3d.exe": self.golden["prism3d"][expected_name]})
                    self.assertEqual(
                        hashlib.sha256((self.output / "prism3d.exe").read_bytes()).hexdigest(),
                        self.golden["prism3d"][expected_name],
                    )
                    self.assertEqual(target.read_bytes(), source)

    def test_render_quick_setup_without_python_on_path(self):
        output = self.root / "rendered Quick Setup"
        result = subprocess.run(
            [
                WINE,
                str(self.exe),
                "render-quick-setup",
                "--output-dir",
                self.windows_path(output),
                "--mod",
                "ui_keyboard",
                "--mod",
                "config",
            ],
            env=self.wine_environment,
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.wine_environment["PATH"], "")
        rendered = output / "game/usr/ui/cosetup.sc"
        content = rendered.read_bytes()
        self.assertIn(b"<br>Enabled: configuration, keyboard\"\r\n", content)
        digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(result.stdout.strip(), f"game/usr/ui/cosetup.sc: {digest}")


if __name__ == "__main__":
    unittest.main()
