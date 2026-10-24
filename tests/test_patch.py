import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "tests"))

from test_pe import make_pe

from htmod.errors import PatchError, StateError, TransactionError
from htmod.patch import Anchor, BinaryMod, BytePatch, PatchRunner, PatchState


def binary_mod(mod_id="alpha", target="game/sys/game.dll", offset=0x210):
    return BinaryMod(
        mod_id,
        target,
        (
            BytePatch(
                "test operand",
                offset,
                b"\x00\x01",
                b"\x02\x03",
                anchors=(Anchor(offset - 2, b"\xaa\xbb"), Anchor(offset + 2, b"\xcc\xdd")),
            ),
        ),
    )


def image_with_patch():
    data = make_pe()
    data[0x20E:0x214] = b"\xaa\xbb\x00\x01\xcc\xdd"
    return data


class BinaryModTests(unittest.TestCase):
    def test_original_apply_check_restore_round_trip(self):
        original = image_with_patch()
        image = original[:]
        mod = binary_mod()
        self.assertEqual(mod.inspect_bytes(image), PatchState.ORIGINAL)
        mod.apply_bytes(image)
        self.assertEqual(image[0x210:0x212], b"\x02\x03")
        self.assertEqual(mod.inspect_bytes(image), PatchState.PATCHED)
        mod.restore_bytes(image)
        self.assertEqual(image, original)
        self.assertEqual(mod.inspect_bytes(image), PatchState.ORIGINAL)

    def test_foreign_operand_or_anchor_is_not_modified(self):
        for offset, value in ((0x210, b"\xff\xff"), (0x20E, b"\x00\x00")):
            with self.subTest(offset=offset):
                image = image_with_patch()
                image[offset:offset + 2] = value
                before = image[:]
                self.assertEqual(binary_mod().inspect_bytes(image), PatchState.FOREIGN)
                with self.assertRaises(StateError):
                    binary_mod().apply_bytes(image)
                with self.assertRaises(StateError):
                    binary_mod().restore_bytes(image)
                self.assertEqual(image, before)

    def test_out_of_bounds_patch_is_foreign_and_refused(self):
        image = image_with_patch()
        mod = binary_mod(offset=len(image) - 1)
        self.assertEqual(mod.inspect_bytes(image), PatchState.FOREIGN)
        with self.assertRaises(StateError):
            mod.apply_bytes(image)


class PatchRunnerTests(unittest.TestCase):
    def test_check_apply_and_restore_are_all_or_nothing_in_memory(self):
        game = image_with_patch()
        dx8 = image_with_patch()
        images = {"game/sys/game.dll": game, "sys/dx8.dll": dx8}
        mods = [binary_mod(), binary_mod("dx8", "sys/dx8.dll")]
        runner = PatchRunner(images)
        self.assertEqual(runner.check(mods), {"alpha": PatchState.ORIGINAL, "dx8": PatchState.ORIGINAL})
        runner.apply(mods)
        self.assertEqual(runner.check(mods), {"alpha": PatchState.PATCHED, "dx8": PatchState.PATCHED})
        runner.restore(mods)
        self.assertEqual(images["game/sys/game.dll"], game)
        self.assertEqual(images["sys/dx8.dll"], dx8)

    def test_failed_preflight_or_verify_leaves_every_image_unchanged(self):
        original = image_with_patch()
        images = {"game/sys/game.dll": original[:], "sys/dx8.dll": original[:]}
        mods = [binary_mod(), binary_mod("dx8", "sys/dx8.dll")]
        images["sys/dx8.dll"][0x210] = 0xFF
        runner = PatchRunner(images)
        with self.assertRaises(StateError):
            runner.apply(mods)
        self.assertEqual(images["game/sys/game.dll"], original)
        self.assertEqual(images["sys/dx8.dll"][0x210], 0xFF)

    def test_restore_runs_in_reverse_order(self):
        original = image_with_patch()
        runner = PatchRunner({"game/sys/game.dll": original})
        runner.apply([binary_mod()])
        runner.restore([binary_mod()])
        self.assertEqual(runner.images["game/sys/game.dll"], original)

    def test_atomic_multi_target_commit_rolls_back_replaced_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {}
            images = {}
            originals = {}
            for target in ("game/sys/game.dll", "sys/dx8.dll"):
                path = root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                originals[target] = image_with_patch()
                path.write_bytes(originals[target])
                paths[target] = path
                images[target] = original = image_with_patch()
                original[0x210:0x212] = b"\x02\x03"
            runner = PatchRunner(images, paths)
            real_replace = os.replace

            def fail_second_replace(source, destination):
                if Path(destination) == paths["sys/dx8.dll"] and not getattr(fail_second_replace, "failed", False):
                    fail_second_replace.failed = True
                    raise OSError("simulated replacement failure")
                real_replace(source, destination)

            with mock.patch("htmod.patch.os.replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(TransactionError, "simulated replacement failure"):
                    runner.commit()
            for target, path in paths.items():
                self.assertEqual(path.read_bytes(), originals[target])
            self.assertEqual(list(root.rglob("*.htmod-*")), [])

    def test_failed_rollback_preserves_pristine_backup_and_reports_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {}
            images = {}
            originals = {}
            for target in ("game/sys/game.dll", "sys/dx8.dll"):
                path = root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                originals[target] = image_with_patch()
                path.write_bytes(originals[target])
                paths[target] = path
                images[target] = bytearray(originals[target])
                images[target][0x210:0x212] = b"\x02\x03"
            runner = PatchRunner(images, paths)
            real_replace = os.replace

            def fail_replace_and_rollback(source, destination):
                if Path(destination) == paths["sys/dx8.dll"] and not getattr(fail_replace_and_rollback, "failed", False):
                    fail_replace_and_rollback.failed = True
                    raise OSError("simulated replacement failure")
                if Path(destination) == paths["game/sys/game.dll"] and str(source).endswith(".htmod-backup"):
                    raise OSError("simulated rollback failure")
                real_replace(source, destination)

            with mock.patch("htmod.patch.os.replace", side_effect=fail_replace_and_rollback):
                with self.assertRaisesRegex(TransactionError, "rollback failure") as caught:
                    runner.commit()
            backups = list(root.rglob("*.htmod-backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), originals["game/sys/game.dll"])
            self.assertIn(str(backups[0]), str(caught.exception))
            self.assertEqual(paths["sys/dx8.dll"].read_bytes(), originals["sys/dx8.dll"])


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/htmod.py"), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def test_binary_commands_require_game_dir_and_at_least_one_mod(self):
        for command in ("check", "apply", "restore"):
            with self.subTest(command=command):
                result = self.run_cli(command, "--mod", "physics")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--game-dir", result.stderr)
                result = self.run_cli(command, "--game-dir", str(ROOT))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--mod", result.stderr)

    def test_unknown_data_and_camera_mods_fail_without_tracebacks(self):
        for mod_id in ("missing", "config", "camera"):
            with self.subTest(mod=mod_id):
                result = self.run_cli(
                    "check", "--game-dir", str(ROOT), "--mod", mod_id
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
