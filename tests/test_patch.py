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


def commit_fixture(root):
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
    mods = [binary_mod(), binary_mod("dx8", "sys/dx8.dll")]
    runner = PatchRunner(images, paths)
    runner.apply(mods)
    return runner, paths, originals


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
        events = []

        class InstrumentedMod(BinaryMod):
            def restore(self, image):
                events.append(self.id)
                super().restore(image)

        original = image_with_patch()
        original[0x21E:0x224] = b"\xaa\xbb\x00\x01\xcc\xdd"
        first = InstrumentedMod("first", "game/sys/game.dll", binary_mod(offset=0x210).patches)
        second = InstrumentedMod("second", "game/sys/game.dll", binary_mod(offset=0x220).patches)
        runner = PatchRunner({"game/sys/game.dll": original})
        runner.apply([first, second])
        runner.restore([first, second])
        self.assertEqual(events, ["second", "first"])
        self.assertEqual(runner.images["game/sys/game.dll"], original)

    def test_commit_refuses_mutation_after_initial_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner, paths, originals = commit_fixture(Path(temporary))
            external = bytearray(originals["sys/dx8.dll"])
            external[0] = 0x5A
            paths["sys/dx8.dll"].write_bytes(external)
            with mock.patch("htmod.patch.os.replace") as replace:
                with self.assertRaisesRegex(TransactionError, "changed since preflight"):
                    runner.commit()
                replace.assert_not_called()
            self.assertEqual(paths["game/sys/game.dll"].read_bytes(), originals["game/sys/game.dll"])
            self.assertEqual(paths["sys/dx8.dll"].read_bytes(), external)

    def test_commit_rolls_back_keyboard_interrupt_around_replace(self):
        for replace_happened in (False, True):
            with self.subTest(replace_happened=replace_happened), tempfile.TemporaryDirectory() as temporary:
                runner, paths, originals = commit_fixture(Path(temporary))
                real_replace = os.replace

                def interrupt(source, destination):
                    if Path(destination) == paths["game/sys/game.dll"]:
                        if replace_happened:
                            real_replace(source, destination)
                        raise KeyboardInterrupt("simulated asynchronous interruption")
                    real_replace(source, destination)

                with mock.patch("htmod.patch.os.replace", side_effect=interrupt):
                    with self.assertRaisesRegex(TransactionError, "interruption"):
                        runner.commit()
                for target, path in paths.items():
                    self.assertEqual(path.read_bytes(), originals[target])
                self.assertEqual(list(Path(temporary).rglob("*.htmod-*")), [])

    def test_final_post_commit_verification_failure_rolls_back_every_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner, paths, originals = commit_fixture(Path(temporary))
            real_replace = os.replace
            real_read_bytes = Path.read_bytes
            replaced = set()
            failed = False

            def tracking_replace(source, destination):
                real_replace(source, destination)
                replaced.add(Path(destination))

            def fail_final_read(path):
                nonlocal failed
                if path == paths["sys/dx8.dll"] and len(replaced) == 2 and not failed:
                    failed = True
                    return b"post-commit mutation"
                return real_read_bytes(path)

            with mock.patch("htmod.patch.os.replace", side_effect=tracking_replace), mock.patch(
                "htmod.patch.Path.read_bytes", autospec=True, side_effect=fail_final_read
            ):
                with self.assertRaisesRegex(TransactionError, "post-commit verification"):
                    runner.commit()
            for target, path in paths.items():
                self.assertEqual(path.read_bytes(), originals[target])
            self.assertEqual(list(Path(temporary).rglob("*.htmod-*")), [])

    def test_atomic_multi_target_commit_rolls_back_replaced_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner, paths, originals = commit_fixture(root)
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
            runner, paths, originals = commit_fixture(root)
            real_replace = os.replace

            def fail_replace_and_rollback(source, destination):
                if Path(destination) == paths["sys/dx8.dll"] and not getattr(fail_replace_and_rollback, "failed", False):
                    fail_replace_and_rollback.failed = True
                    raise OSError("simulated replacement failure")
                if Path(destination) == paths["game/sys/game.dll"] and str(source).endswith(
                    (".htmod-backup", ".htmod-restore")
                ):
                    raise OSError("simulated rollback failure")
                real_replace(source, destination)

            with mock.patch("htmod.patch.os.replace", side_effect=fail_replace_and_rollback):
                with self.assertRaisesRegex(TransactionError, "rollback failure") as caught:
                    runner.commit()
            backups = list(root.rglob("*.htmod-backup"))
            self.assertEqual(len(backups), 2)
            for target, path in paths.items():
                target_backups = list(path.parent.glob("*.htmod-backup"))
                self.assertEqual(len(target_backups), 1)
                self.assertEqual(target_backups[0].read_bytes(), originals[target])
            failed_backup = next(path for path in backups if path.parent == paths["game/sys/game.dll"].parent)
            self.assertIn(str(failed_backup), str(caught.exception))
            self.assertEqual(paths["sys/dx8.dll"].read_bytes(), originals["sys/dx8.dll"])

    def test_restore_verification_failure_retains_real_pristine_backup(self):
        for scenario in ("read_failure", "external_mutation"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                runner, paths, originals = commit_fixture(Path(temporary))
                real_replace = os.replace
                real_read_bytes = Path.read_bytes
                restore_replaced = False

                def replace_and_damage_restore(source, destination):
                    nonlocal restore_replaced
                    if Path(destination) == paths["sys/dx8.dll"] and str(source).endswith(".htmod-new"):
                        raise OSError("simulated initial replacement failure")
                    real_replace(source, destination)
                    if Path(destination) == paths["game/sys/game.dll"] and str(source).endswith(
                        (".htmod-backup", ".htmod-restore")
                    ):
                        restore_replaced = True
                        if scenario == "external_mutation":
                            Path(destination).write_bytes(b"external mutation after restore")

                def fail_restore_read(path):
                    if scenario == "read_failure" and restore_replaced and path == paths["game/sys/game.dll"]:
                        raise OSError("simulated restore verification read failure")
                    return real_read_bytes(path)

                with mock.patch("htmod.patch.os.replace", side_effect=replace_and_damage_restore), mock.patch(
                    "htmod.patch.Path.read_bytes", autospec=True, side_effect=fail_restore_read
                ):
                    with self.assertRaises(TransactionError) as caught:
                        runner.commit()
                backups = list(paths["game/sys/game.dll"].parent.glob("*.htmod-backup"))
                self.assertEqual(len(backups), 1)
                self.assertTrue(backups[0].is_file())
                self.assertEqual(backups[0].read_bytes(), originals["game/sys/game.dll"])
                self.assertIn(str(backups[0]), str(caught.exception))


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

    def test_conflicting_handler_makes_any_selected_foreign_state_fail_before_commit(self):
        from htmod import cli as cli_module

        class ConflictingMod(BinaryMod):
            def apply(self, image):
                super().apply(image)
                image.data[0x20E:0x210] = b"\x00\x00"

        alpha = binary_mod()
        beta = ConflictingMod("beta", "game/sys/game.dll", binary_mod("beta", offset=0x220).patches)
        data = image_with_patch()
        data[0x21E:0x224] = b"\xaa\xbb\x00\x01\xcc\xdd"
        alpha.apply_bytes(data)
        images = {"game/sys/game.dll": data}
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "game/sys/game.dll"
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            runner = PatchRunner(images, {"game/sys/game.dll": target})
            with mock.patch("htmod.cli.load_release"), \
                    mock.patch("htmod.cli.load_binary_mods", return_value=[alpha, beta]), \
                    mock.patch("htmod.cli.runner_from_directory", return_value=runner), \
                    mock.patch.object(runner, "commit") as commit:
                result = cli_module.main(["apply", "--game-dir", str(temporary), "--mod", "alpha", "--mod", "beta"])
            self.assertEqual(result, 1)
            commit.assert_not_called()

    def test_unknown_data_mods_fail_without_tracebacks(self):
        for mod_id in ("missing", "config"):
            with self.subTest(mod=mod_id):
                result = self.run_cli(
                    "check", "--game-dir", str(ROOT), "--mod", mod_id
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
