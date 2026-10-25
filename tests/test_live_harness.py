import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/live/run_wine.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("htmod_live_run_wine", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LiveHarnessTests(unittest.TestCase):
    def test_cli_requires_explicit_game_prefix_output_and_display(self):
        harness = load_harness()
        args = harness.parse_args([
            "--game", "/games/hard truck",
            "--prefix", "/prefixes/smoke",
            "--output", "/evidence/run",
            "--display", "121",
            "--resolution", "1024x768",
        ])
        self.assertEqual(args.game, Path("/games/hard truck"))
        self.assertEqual(args.prefix, Path("/prefixes/smoke"))
        self.assertEqual(args.output, Path("/evidence/run"))
        self.assertEqual(args.display, ":121")
        self.assertEqual(args.resolution, "1024x768")

    def test_dead_display_processes_abort_before_dependent_interactions(self):
        harness = load_harness()
        for failed_process in ("Xvfb", "openbox"):
            with self.subTest(failed_process=failed_process), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                game = output / "game"
                game.mkdir()
                (game / "prism3d.exe").write_bytes(b"")
                session = harness.WineSession(game, output / "prefix", output, ":124")
                alive = mock.Mock()
                alive.poll.return_value = None
                dead = mock.Mock()
                dead.poll.return_value = 1
                processes = [dead, alive] if failed_process == "Xvfb" else [alive, dead]
                session.spawn = mock.Mock(side_effect=processes)
                session.wait_for_window = mock.Mock()
                with mock.patch.object(harness.shutil, "which", return_value="/usr/bin/tool"), \
                        mock.patch.object(harness.time, "sleep"), \
                        self.assertRaisesRegex(harness.LiveSmokeError, failed_process):
                    session.start()
                expected_spawns = 1 if failed_process == "Xvfb" else 2
                self.assertEqual(session.spawn.call_count, expected_spawns)
                session.wait_for_window.assert_not_called()

    def test_capture_refuses_dead_display_before_scrot(self):
        harness = load_harness()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            session = harness.WineSession(output / "game", output / "prefix", output, ":124")
            session.xvfb_process = mock.Mock()
            session.xvfb_process.poll.return_value = None
            session.openbox_process = mock.Mock()
            session.openbox_process.poll.return_value = 1
            session.game_process = mock.Mock()
            session.game_process.poll.return_value = None
            session.activate = mock.Mock()
            session.command = mock.Mock()
            with self.assertRaisesRegex(harness.LiveSmokeError, "openbox"):
                session.capture("frame.png")
            session.command.assert_not_called()

    def test_cleanup_terminates_only_owned_processes_and_scopes_wineserver_to_prefix(self):
        harness = load_harness()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            session = harness.WineSession(
                game=output / "game",
                prefix=output / "prefix",
                output=output,
                display=":122",
                resolution="1024x768",
            )
            session.prefix.mkdir()
            first = mock.Mock()
            first.poll.return_value = None
            first.pid = 101
            second = mock.Mock()
            second.poll.return_value = None
            second.pid = 202
            session.processes.extend((first, second))
            with mock.patch.object(harness.subprocess, "run") as run:
                session.cleanup()
            first.terminate.assert_called_once_with()
            second.terminate.assert_called_once_with()
            first.wait.assert_called_once_with(timeout=5)
            second.wait.assert_called_once_with(timeout=5)
            run.assert_called_once_with(
                ["wineserver", "-k"],
                env=mock.ANY,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["WINEPREFIX"], str(session.prefix))
            self.assertEqual(environment["DISPLAY"], ":122")

    def test_real_cleanup_reaps_owned_pid(self):
        harness = load_harness()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            session = harness.WineSession(output, output / "prefix", output, ":123")
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            session.processes.append(process)
            session.cleanup()
            self.assertIsNotNone(process.poll())
            with self.assertRaises(ProcessLookupError):
                os.kill(process.pid, 0)

    def test_failed_start_still_cleans_owned_processes(self):
        harness = load_harness()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            session = harness.WineSession(output, output / "prefix", output, ":123")
            session.start = mock.Mock(side_effect=harness.LiveSmokeError("start failed"))
            session.cleanup = mock.Mock()
            with self.assertRaisesRegex(harness.LiveSmokeError, "start failed"):
                session.__enter__()
            session.cleanup.assert_called_once_with()

    def test_game_log_validation_accepts_known_benign_errors_and_rejects_crashes(self):
        harness = load_harness()
        benign = "setting state GAME\nscript ui/cosetup.sc read OK\n*** ERROR *** dx8_gamma read only\n"
        harness.validate_game_log(benign, require_quick_setup=True)
        headless = benign + "*** ERROR *** No modes found for device Direct3D8\n"
        with self.assertRaisesRegex(harness.LiveSmokeError, "unexpected game errors"):
            harness.validate_game_log(headless, require_quick_setup=True)
        harness.validate_game_log(headless, require_quick_setup=True, allow_headless_graphics=True)
        for fatal in ("page fault", "0x88888786", "creation failed"):
            with self.subTest(fatal=fatal), self.assertRaises(harness.LiveSmokeError):
                harness.validate_game_log(benign + fatal + "\n")
        with self.assertRaises(harness.LiveSmokeError):
            harness.validate_game_log("setting state MENU\n", require_quick_setup=True)

    def test_camera_smoke_requires_test_only_look_bindings(self):
        harness = load_harness()
        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary)
            config = game / "game/usr/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_text('bind_imp y "mleft"\nbind_imp o "mright"\nbind_imp i "mlookup"\nbind_imp k "mlookdown"\n')
            harness.validate_camera_bindings(game)
            config.write_text('bind_imp y "mleft"\n')
            with self.assertRaisesRegex(harness.LiveSmokeError, "o, i, k"):
                harness.validate_camera_bindings(game)

    def test_camera_battery_covers_sustained_clamps_both_input_orders_and_every_camera(self):
        harness = load_harness()

        class FakeSession:
            def __init__(self):
                self.keys = []
                self.holds = []
                self.screenshots = []
                self.probe_started = False
                self.finished = False

            def capture(self, name):
                self.screenshots.append(name)

            def enter_map(self, wait):
                pass

            def console(self, command):
                if command != "ui t cosetup":
                    raise AssertionError(command)

            def close_console(self, settle):
                pass

            def start_truck_probe(self):
                self.probe_started = True

            def key(self, key, repeat=1):
                self.keys.append((key, repeat))

            def hold(self, key, seconds):
                self.holds.append((key, seconds))

            def finish(self, require_quick_setup):
                self.finished = require_quick_setup

        session = FakeSession()
        with mock.patch.object(harness.time, "sleep"):
            harness.run_camera(session, mock.Mock(boot_wait=0, map_wait=0))
        self.assertEqual(session.holds, [
            ("k", 8), ("i", 8),
            ("y", 2), ("k", 8),
            ("o", 2), ("i", 8),
            ("k", 8), ("y", 2),
            ("i", 8), ("o", 2),
        ])
        self.assertEqual(
            [name for name in session.screenshots if name.startswith("14-camera-")],
            [f"14-camera-{camera}.png" for camera in range(1, 8)],
        )
        for name in (
            "01-truck-before.png",
            "03-cab-straight-down-80.png",
            "04-cab-straight-up-80.png",
            "06-cab-yaw-left-down-80.png",
            "09-cab-yaw-right-up-80.png",
            "11-cab-down-80-then-yaw-left.png",
            "12-cab-up-80-then-yaw-right.png",
            "13-cab-level-reset.png",
        ):
            self.assertIn(name, session.screenshots)
        self.assertTrue(session.probe_started)
        self.assertTrue(session.finished)

    def test_truck_probe_requires_samples_and_an_unchanged_physical_transform(self):
        harness = load_harness()
        samples = [
            {"label": "before", "physical_transform_hex": "00" * 64},
            {"label": "after", "physical_transform_hex": "00" * 64},
        ]
        harness.validate_truck_samples(samples)
        with self.assertRaisesRegex(harness.LiveSmokeError, "no truck memory samples"):
            harness.validate_truck_samples([])
        samples[1]["physical_transform_hex"] = "01" + "00" * 63
        with self.assertRaisesRegex(harness.LiveSmokeError, "physical transform changed"):
            harness.validate_truck_samples(samples)

    def test_accessibility_smoke_covers_keyboard_cues_and_never_activates_quick_ok(self):
        harness = load_harness()

        class FakeSession:
            def __init__(self):
                self.keys = []
                self.screenshots = []
                self.console_commands = []
                self.text_entries = []
                self.finished = False

            def capture(self, name):
                self.screenshots.append(name)

            def enter_map(self, wait):
                pass

            def console(self, command):
                self.console_commands.append(command)

            def close_console(self, settle):
                pass

            def text(self, value):
                self.text_entries.append(value)

            def key(self, key, repeat=1):
                self.keys.append((key, repeat))
                if key == "Return" and self.screenshots and self.screenshots[-1].startswith(("15-", "16-", "17-", "18-")):
                    raise AssertionError("Quick Setup OK must remain unactivated")

            def finish(self, require_quick_setup, allow_headless_graphics=False):
                self.finished = require_quick_setup and allow_headless_graphics

        session = FakeSession()
        with mock.patch.object(harness.time, "sleep"):
            harness.run_accessibility(session, mock.Mock(boot_wait=0, map_wait=0))
        self.assertEqual(session.text_entries, ["A", "B"])
        self.assertIn(("shift+Tab", 1), session.keys)
        self.assertEqual(
            session.console_commands,
            ["ui t graphics", "ui t sound", "ui t controls", "ui t options"],
        )
        self.assertEqual(session.keys[-1], ("Tab", 1))
        self.assertEqual(session.keys.count(("Return", 1)), 3)
        self.assertEqual(session.screenshots[-1], "18-quick-ok-unpressed.png")
        for name in (
            "02-main-forward.png",
            "04-main-backward.png",
            "06-options-enter.png",
            "09-sound-radio-focused.png",
            "10-sound-selected.png",
            "13-controls-radio-focused.png",
            "14-controls-selected.png",
            "15-quick-company.png",
            "16-quick-owner.png",
            "18-quick-ok-unpressed.png",
        ):
            self.assertIn(name, session.screenshots)
        self.assertTrue(session.finished)

    def test_accessibility_entrypoint_uses_shared_parameterized_harness(self):
        entrypoint = ROOT / "tests/live/accessibility.py"
        self.assertTrue(entrypoint.is_file())
        self.assertIn('from run_wine import main', entrypoint.read_text(encoding="utf-8"))
        args = load_harness().parse_args([
            "--game", "/game", "--prefix", "/prefix", "--output", "/output",
            "--display", ":125", "--resolution", "1920x1080", "--smoke", "accessibility",
        ])
        self.assertEqual(args.smoke, "accessibility")
        self.assertEqual(args.resolution, "1920x1080")

    def test_retired_one_off_patchers_are_not_active_sources(self):
        for name in (
            "patch_camera_mods.py",
            "patch_physics_fixes.py",
            "patch_ai_fixes.py",
            "patch_dx8_vsync.py",
        ):
            with self.subTest(script=name):
                self.assertFalse((ROOT / "modpack/patches" / name).exists())
        self.assertTrue((ROOT / "modpack/patches/patch_fov_horplus.py").is_file())


if __name__ == "__main__":
    unittest.main()
