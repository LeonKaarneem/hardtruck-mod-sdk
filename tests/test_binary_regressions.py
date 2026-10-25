import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod.manifest import load_release
from htmod.patch import PatchRunner, PatchState
from htmod.runner import load_binary_mods


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pristine_file(relative):
    candidates = []
    if os.environ.get("HTMOD_TEST_BASE"):
        candidates.append(Path(os.environ["HTMOD_TEST_BASE"]) / relative)
    candidates.append(ROOT / "test-base" / relative)
    if ROOT.parent.name == ".worktrees":
        candidates.append(ROOT.parent.parent / "test-base" / relative)
    return next((path for path in candidates if path.is_file()), None)


class BinaryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game_path = pristine_file(Path("game/sys/game.dll"))
        cls.dx8_path = pristine_file(Path("sys/dx8.dll"))
        if cls.game_path is None or cls.dx8_path is None:
            raise unittest.SkipTest("pinned test-base game and DX8 DLLs are unavailable")
        cls.game = cls.game_path.read_bytes()
        cls.dx8 = cls.dx8_path.read_bytes()
        cls.golden = json.loads((ROOT / "tests/golden/v3.json").read_text(encoding="utf-8"))
        cls.release = load_release(ROOT)

    def test_pristine_inputs_are_pinned(self):
        self.assertEqual(sha256(self.game), self.golden["pristine"]["game_dll"])
        self.assertEqual(sha256(self.dx8), self.golden["pristine"]["dx8_dll"])

    def test_every_physics_ai_combination_matches_legacy_golden_and_restores(self):
        for variant in ("0000", "0010", "0100", "0110"):
            with self.subTest(variant=variant):
                components = self.golden["game_variants"][variant]["components"]
                mods = load_binary_mods(self.release, components)
                images = {"game/sys/game.dll": bytearray(self.game)}
                runner = PatchRunner(images)
                if mods:
                    runner.apply(mods)
                    self.assertEqual(runner.check(mods), {mod.id: PatchState.PATCHED for mod in mods})
                self.assertEqual(
                    sha256(images["game/sys/game.dll"]),
                    self.golden["game_variants"][variant]["sha256"],
                )
                if mods:
                    runner.restore(mods)
                self.assertEqual(sha256(images["game/sys/game.dll"]), self.golden["pristine"]["game_dll"])

    def test_dx8_matches_legacy_golden_and_restores(self):
        mod = load_binary_mods(self.release, ["dx8_vsync"])[0]
        images = {"sys/dx8.dll": bytearray(self.dx8)}
        runner = PatchRunner(images)
        runner.apply([mod])
        self.assertEqual(sha256(images["sys/dx8.dll"]), self.golden["dx8"]["vsync"])
        self.assertEqual(runner.check([mod]), {"dx8_vsync": PatchState.PATCHED})
        runner.restore([mod])
        self.assertEqual(sha256(images["sys/dx8.dll"]), self.golden["pristine"]["dx8_dll"])

    def test_handlers_refuse_foreign_operands_without_mutation(self):
        cases = (("physics", self.game, 0x294DF), ("ai", self.game, 0xB18A4), ("dx8_vsync", self.dx8, 0x3699))
        for mod_id, source, offset in cases:
            with self.subTest(mod=mod_id):
                target = load_binary_mods(self.release, [mod_id])[0].target
                mutated = bytearray(source)
                mutated[offset:offset + 4] = b"\x02\x00\x00\x00"
                before = mutated[:]
                mod = load_binary_mods(self.release, [mod_id])[0]
                self.assertEqual(mod.inspect_bytes(mutated), PatchState.FOREIGN)
                runner = PatchRunner({target: mutated})
                with self.assertRaises(Exception):
                    runner.apply([mod])
                self.assertEqual(mutated, before)

    def test_cli_check_apply_restore_multiple_targets_and_mods(self):
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            for source, relative in ((self.game_path, "game/sys/game.dll"), (self.dx8_path, "sys/dx8.dll")):
                destination = game_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)

            def run(command):
                return subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools/htmod.py"),
                        command,
                        "--game-dir",
                        str(game_dir),
                        "--mod",
                        "physics",
                        "--mod",
                        "dx8_vsync",
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )

            result = run("check")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("physics: ORIGINAL", result.stdout)
            self.assertIn("dx8_vsync: ORIGINAL", result.stdout)
            result = run("apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(sha256((game_dir / "game/sys/game.dll").read_bytes()), self.golden["pristine"]["game_dll"])
            self.assertEqual(sha256((game_dir / "sys/dx8.dll").read_bytes()), self.golden["dx8"]["vsync"])
            result = run("check")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("physics: PATCHED", result.stdout)
            self.assertIn("dx8_vsync: PATCHED", result.stdout)
            result = run("restore")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sha256((game_dir / "game/sys/game.dll").read_bytes()), self.golden["pristine"]["game_dll"])
            self.assertEqual(sha256((game_dir / "sys/dx8.dll").read_bytes()), self.golden["pristine"]["dx8_dll"])

    def test_cli_rejects_unknown_source_hash_before_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            target = game_dir / "game/sys/game.dll"
            target.parent.mkdir(parents=True)
            target.write_bytes(self.game + b"foreign")
            before = sha256(target.read_bytes())
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/htmod.py"), "apply", "--game-dir", str(game_dir), "--mod", "camera"],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertRegex(result.stderr, "source SHA-256|unrecognized source")
            self.assertEqual(sha256(target.read_bytes()), before)

    def test_cli_rejects_appended_bytes_from_otherwise_valid_patched_handlers(self):
        cases = (
            ("physics", "game/sys/game.dll", "0010"),
            ("ai", "game/sys/game.dll", "0100"),
            ("dx8_vsync", "sys/dx8.dll", None),
        )
        for mod, relative, variant in cases:
            with self.subTest(mod=mod), tempfile.TemporaryDirectory() as temporary:
                game_dir = Path(temporary)
                target = game_dir / relative
                target.parent.mkdir(parents=True)
                if mod == "dx8_vsync":
                    shutil.copyfile(self.dx8_path, target)
                else:
                    shutil.copyfile(self.game_path, target)

                def run(command):
                    return subprocess.run(
                        [sys.executable, str(ROOT / "tools/htmod.py"), command, "--game-dir", str(game_dir), "--mod", mod],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                    )

                result = run("apply")
                self.assertEqual(result.returncode, 0, result.stderr)
                if variant is not None:
                    self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"][variant]["sha256"])
                else:
                    self.assertEqual(sha256(target.read_bytes()), self.golden["dx8"]["vsync"])
                target.write_bytes(target.read_bytes() + b"unknown composed suffix")
                before = target.read_bytes()
                result = run("apply")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertRegex(result.stderr, "whole-file|exact|unrecognized")
                self.assertEqual(target.read_bytes(), before)

    def test_cli_every_curated_composed_state_reruns_and_restores_exactly(self):
        for number in range(16):
            name = f"{number:04b}"
            selected = [
                component
                for bit, component in enumerate(("camera", "physics", "ai", "ui_keyboard"))
                if number & (1 << bit)
            ]
            with self.subTest(variant=name), tempfile.TemporaryDirectory() as temporary:
                game_dir = Path(temporary)
                target = game_dir / "game/sys/game.dll"
                target.parent.mkdir(parents=True)
                shutil.copyfile(self.game_path, target)
                if not selected:
                    self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"][name]["sha256"])
                    continue

                def run(command, mods):
                    arguments = [sys.executable, str(ROOT / "tools/htmod.py"), command, "--game-dir", str(game_dir)]
                    arguments += ["--mod", "config"]
                    for mod in mods:
                        arguments += ["--mod", mod]
                    return subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True)

                cumulative = []
                for mod in selected:
                    cumulative.append(mod)
                    result = run("apply", cumulative)
                    self.assertEqual(result.returncode, 0, result.stderr)
                result = run("apply", selected)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"][name]["sha256"])
                result = run("restore", selected)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sha256(target.read_bytes()), self.golden["pristine"]["game_dll"])

    def test_cli_recognizes_composed_states_and_reruns_only_pending_mods(self):
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            target = game_dir / "game/sys/game.dll"
            target.parent.mkdir(parents=True)
            shutil.copyfile(self.game_path, target)

            def run(command, *mods):
                arguments = [sys.executable, str(ROOT / "tools/htmod.py"), command, "--game-dir", str(game_dir)]
                for mod in mods:
                    arguments += ["--mod", mod]
                return subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True)

            result = run("apply", "camera")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"]["0001"]["sha256"])

            result = run("apply", "physics")
            self.assertEqual(result.returncode, 1)
            self.assertIn("selected mods", result.stderr)
            self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"]["0001"]["sha256"])

            result = run("apply", "camera", "physics")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"]["0011"]["sha256"])
            result = run("apply", "camera", "physics")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sha256(target.read_bytes()), self.golden["game_variants"]["0011"]["sha256"])

            result = run("restore", "camera", "physics")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sha256(target.read_bytes()), self.golden["pristine"]["game_dll"])
            result = run("restore", "camera", "physics")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sha256(target.read_bytes()), self.golden["pristine"]["game_dll"])

    def test_release_builder_uses_sdk_for_every_simple_binary_payload(self):
        from unittest import mock
        from tools import build_windows_release

        self.assertFalse(hasattr(build_windows_release, "CAMERA_PATCHER"))
        self.assertFalse(hasattr(build_windows_release, "run_patcher"))
        legacy_path = (ROOT / "modpack/patches/patch_camera_mods.py").resolve()
        real_read_bytes = Path.read_bytes

        def legacy_unavailable(path, *args, **kwargs):
            if Path(path).resolve() == legacy_path:
                raise FileNotFoundError("legacy camera intentionally unavailable")
            return real_read_bytes(path, *args, **kwargs)

        def no_legacy(patcher, dll, mode=None):
            raise AssertionError(f"legacy simple patcher invoked: {patcher.name}")

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(build_windows_release, "run_patcher", side_effect=no_legacy, create=True), \
                    mock.patch("pathlib.Path.read_bytes", autospec=True, side_effect=legacy_unavailable):
                variants = build_windows_release.build_game_variants(self.game_path)
                dx8 = build_windows_release.build_dx8_payloads(self.dx8_path)
            for name, record in variants.items():
                self.assertEqual(record["sha256"], self.golden["game_variants"][name]["sha256"])
            self.assertEqual(dx8["vsync"]["sha256"], self.golden["dx8"]["vsync"])
            self.assertEqual(dx8["stock"]["sha256"], self.golden["pristine"]["dx8_dll"])


if __name__ == "__main__":
    unittest.main()
