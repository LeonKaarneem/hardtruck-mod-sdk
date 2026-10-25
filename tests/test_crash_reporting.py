import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
import zipfile

from tools import build_windows_release


ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get("PWSH") or shutil.which("pwsh")
COMPONENT_IDS = {
    "config",
    "handling_defs",
    "economy_def",
    "driver_def",
    "camera",
    "physics",
    "ai",
    "ui_keyboard",
    "dx8_vsync",
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CrashReportTemplateTests(unittest.TestCase):
    def test_setup_uses_bounded_tail_retrieval_for_engine_logs(self):
        text = (ROOT / "tools/templates/Setup.ps1").read_text(encoding="utf-8")
        self.assertNotIn("[System.IO.File]::ReadAllLines($logPath)", text)
        self.assertIn("Get-Content -LiteralPath $logPath -Tail 4000 -Encoding UTF8", text)

    def test_batch_templates_forward_arguments_to_their_actions(self):
        for name, action in (("Play Modded.bat", "Play"), ("CrashReport.bat", "CrashReport")):
            with self.subTest(template=name):
                text = (ROOT / "tools/templates" / name).read_text(encoding="utf-8")
                self.assertIn("powershell.exe", text)
                self.assertIn("installer\\Setup.ps1", text)
                self.assertIn(f"-Action {action}", text)
                self.assertIn("%*", text)

    def test_direct_play_dialog_is_fixed_centered_dpi_scaled_with_native_copy(self):
        text = (ROOT / "tools/templates/Setup.ps1").read_text(encoding="utf-8")
        self.assertIn("Add-Type -AssemblyName System.Windows.Forms", text)
        self.assertIn("Add-Type -AssemblyName System.Drawing", text)
        for copy in (
            "Hard Truck Modern v3",
            "Better physics, cameras and handling",
            "Improved economy and AI cleanup",
            "MOD SDK: LeonKaarneem",
            "Play",
            "Cancel",
        ):
            self.assertIn(copy, text)
        self.assertIn("$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog", text)
        self.assertIn("$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen", text)
        self.assertIn("$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi", text)
        self.assertIn("$form.MaximizeBox = $false", text)
        self.assertIn("$form.MinimizeBox = $false", text)

    def test_play_enter_escape_and_window_close_have_explicit_semantics(self):
        text = (ROOT / "tools/templates/Setup.ps1").read_text(encoding="utf-8")
        self.assertIn("$play.DialogResult = [System.Windows.Forms.DialogResult]::OK", text)
        self.assertIn("$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel", text)
        self.assertIn("$form.AcceptButton = $play", text)
        self.assertIn("$form.CancelButton = $cancel", text)
        self.assertIn("$result = $form.ShowDialog()", text)
        self.assertIn("return $result -eq [System.Windows.Forms.DialogResult]::OK", text)

    def test_direct_play_cancel_exits_before_game_or_report_flow(self):
        text = (ROOT / "tools/templates/Setup.ps1").read_text(encoding="utf-8")
        guard = '''        if ($Action -eq "Play" -and -not $NonInteractive -and
            [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            if (-not (Show-PlayConfirmation)) {
                exit 0
            }
        }
'''
        self.assertIn(guard, text)
        self.assertLess(text.index(guard), text.index("        Invoke-Play $manifest $script:GamePath"))


@unittest.skipIf(PWSH is None, "PowerShell 7 is required for crash reporting runtime tests")
class CrashReportingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release_temporary = tempfile.TemporaryDirectory(prefix="htmod-crash-release-")
        cls.release = Path(cls.release_temporary.name) / "HardTruckModern"
        archive = os.environ.get("HTMOD_RELEASE_ZIP")
        if archive:
            with zipfile.ZipFile(archive) as source:
                if source.testzip() is not None:
                    raise RuntimeError(f"corrupt release ZIP: {archive}")
                source.extractall(cls.release_temporary.name)
            if not cls.release.is_dir():
                raise RuntimeError(f"release ZIP has no HardTruckModern root: {archive}")
            return
        from tests.release_fixtures import build_legacy_installer_fixture
        build_legacy_installer_fixture(cls.release)

    @classmethod
    def tearDownClass(cls):
        cls.release_temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="htmod-crash-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "private package"
        shutil.copytree(self.release, self.package)
        self.game = self.root / "private game"
        (self.game / "game/sys").mkdir(parents=True)
        (self.game / "sys").mkdir()
        shutil.copyfile(build_windows_release.default_test_base_file(Path("game/sys/game.dll")), self.game / "game/sys/game.dll")
        shutil.copyfile(build_windows_release.default_test_base_file(Path("sys/dx8.dll")), self.game / "sys/dx8.dll")
        self.marker = self.root / "fake-process.txt"
        self.desktop = self.root / "Desktop"
        self.desktop.mkdir()
        self.home = self.root / "home" / "sensitive_user"
        self.home.mkdir(parents=True)
        self.write_fake_game()

    def write_fake_game(self, exit_code=0, newer_crash=False):
        crash = ""
        if newer_crash:
            crash = "sleep 1\nprintf 'new crash from fake executable\\n' > prism3d.crash\n"
        script = (
            "#!/bin/sh\n"
            "printf '%s\\n' \"$PWD\" > \"$HARDTRUCK_FAKE_MARKER\"\n"
            "printf '%s\\n' \"$@\" >> \"$HARDTRUCK_FAKE_MARKER\"\n"
            f"{crash}"
            f"exit {exit_code}\n"
        )
        executable = self.game / "prism3d.exe"
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o755)

    def run_setup(self, action, interactive=False, input_text=None, package=None, components=None):
        package = package or self.package
        command = [
            PWSH,
            "-NoProfile",
            "-File",
            str(package / "installer/Setup.ps1"),
            "-GamePath",
            str(self.game),
            "-Action",
            action,
        ]
        if components is not None:
            command += ["-Components", ",".join(components)]
        if not interactive:
            command.append("-NonInteractive")
        environment = os.environ.copy()
        environment.pop("USER", None)
        environment.update({
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "HARDTRUCK_FAKE_MARKER": str(self.marker),
            "HARDTRUCK_DESKTOP_PATH": str(self.desktop),
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "USERNAME": "sensitive_user",
            "COMPUTERNAME": "sensitive_computer",
        })
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            env=environment,
            timeout=45,
        )

    def reports(self):
        return list(self.desktop.glob("HardTruckCrashReport-*.zip"))

    def report_components(self):
        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        latest = max(self.reports(), key=lambda path: path.stat().st_mtime_ns)
        with zipfile.ZipFile(latest) as archive:
            report = json.loads(archive.read("report.json"))
        return {item["id"]: item for item in report["components"]}

    def test_builder_copies_batch_entry_points(self):
        for name in ("Play Modded.bat", "CrashReport.bat"):
            self.assertEqual(
                (self.package / name).read_bytes(),
                (ROOT / "tools/templates" / name).read_bytes(),
            )

    def test_play_normal_exit_uses_game_working_directory_and_does_not_prompt(self):
        result = self.run_setup("Play", interactive=True, input_text="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.reports(), [])
        self.assertNotIn("Create a crash report?", result.stdout)
        self.assertEqual(self.marker.read_text(encoding="utf-8").splitlines(), [
            str(self.game),
            "-game",
            "game",
            "-icon",
            "game.ico",
            "-title",
            "Hard Truck Modern",
        ])

    def test_noninteractive_play_bypasses_dialog_and_preserves_normal_launch(self):
        result = self.run_setup("Play")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.reports(), [])
        self.assertNotIn("Create a crash report?", result.stdout)
        self.assertTrue(self.marker.is_file())

    def test_play_nonzero_exit_offers_default_yes_and_records_exit_code(self):
        self.write_fake_game(exit_code=7)
        result = self.run_setup("Play", interactive=True, input_text="\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Create a crash report? [Y/n]", result.stdout)
        self.assertEqual(len(self.reports()), 1)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            report = json.loads(archive.read("report.json"))
        self.assertEqual(report["exit_code"], 7)
        self.assertEqual(report["working_directory"], "<GAME>")
        self.assertIn("started_utc", report)

    def test_play_newer_crash_offers_report_even_after_zero_exit(self):
        self.write_fake_game(newer_crash=True)
        result = self.run_setup("Play", interactive=True, input_text="\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Create a crash report? [Y/n]", result.stdout)
        self.assertEqual(len(self.reports()), 1)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            report = json.loads(archive.read("report.json"))
            self.assertEqual(report["exit_code"], 0)
            self.assertIn("new crash from fake executable", archive.read("prism3d.crash").decode("utf-8"))

    def test_play_report_decline_creates_no_archive(self):
        self.write_fake_game(exit_code=9)
        result = self.run_setup("Play", interactive=True, input_text="n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Create a crash report? [Y/n]", result.stdout)
        self.assertEqual(self.reports(), [])

    def test_paths_with_spaces_metadata_log_bound_and_all_entry_privacy(self):
        installed = self.run_setup("InstallAll")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        sensitive = [
            str(self.game),
            str(self.package),
            str(self.home),
            str(self.game).replace("/", "\\"),
            str(self.package).replace("/", "\\"),
            str(self.home).replace("/", "\\"),
            "sensitive_user",
            "sensitive_computer",
            platform.node(),
            "192.0.2.10",
            "2001:db8::1",
            "::1",
        ]
        crash_text = "fake crash\n" + "\n".join(sensitive) + "\n"
        (self.game / "prism3d.crash").write_text(crash_text, encoding="utf-8")
        lines = [f"log-line-{number}" for number in range(1, 4006)]
        lines.extend(sensitive)
        (self.game / "prism3d.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for relative in ("game/usr/save/private.sav", "game/usr/profile/private.cfg", "memory.dmp", "dxdiag.txt"):
            target = self.game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("must not be collected", encoding="utf-8")

        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("game is closed", result.stdout + result.stderr)
        self.assertEqual(len(self.reports()), 1)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {
                "prism3d.crash",
                "prism3d.log",
                "report.json",
                "README.txt",
            })
            log = archive.read("prism3d.log").decode("utf-8").splitlines()
            self.assertEqual(len(log), 4000)
            self.assertEqual(log[0], f"log-line-{6 + len(sensitive)}")
            self.assertEqual(log[-1], "<IP>")
            self.assertIn("log-line-4005", log)
            report = json.loads(archive.read("report.json"))
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8")
                for value in sensitive:
                    self.assertNotIn(value, text, f"{value!r} leaked in {name}")

        self.assertEqual(report["schema_version"], 1)
        self.assertIsNone(report["exit_code"])
        self.assertEqual(report["payload_manifest_schema"], 2)
        self.assertEqual({item["id"] for item in report["components"]}, COMPONENT_IDS)
        self.assertTrue(all(item["status"] == "installed" and item["version"] for item in report["components"]))
        self.assertEqual(report["game_hashes"], {
            "prism3d.exe": sha256(self.game / "prism3d.exe"),
            "game/sys/game.dll": sha256(self.game / "game/sys/game.dll"),
            "sys/dx8.dll": sha256(self.game / "sys/dx8.dll"),
        })
        self.assertEqual(report["logs"], {"prism3d.crash": True, "prism3d.log": True})
        self.assertEqual(set(report["os"]), {"platform", "version"})
        self.assertIsInstance(report["gpu"], list)
        self.assertTrue(report["created_utc"].endswith("Z"))

    def test_missing_logs_create_metadata_only_report(self):
        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            self.assertEqual(set(archive.namelist()), {"report.json", "README.txt"})
            report = json.loads(archive.read("report.json"))
        self.assertEqual(report["logs"], {"prism3d.crash": False, "prism3d.log": False})

    def test_corrupt_logs_still_create_valid_sanitized_zip(self):
        sensitive = b"sensitive_user\nsensitive_computer\n192.0.2.10\n2001:db8::1\n"
        (self.game / "prism3d.crash").write_bytes(b"\xffinvalid\x80\n" + sensitive)
        (self.game / "prism3d.log").write_bytes(b"\x81invalid\xfe\n" + sensitive)
        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            self.assertIsNone(archive.testzip())
            report = json.loads(archive.read("report.json"))
            self.assertEqual(report["logs"], {"prism3d.crash": True, "prism3d.log": True})
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8")
                for value in sensitive.decode("ascii").splitlines():
                    self.assertNotIn(value, text, f"{value!r} leaked in {name}")

    def test_unpatched_game_reports_no_components(self):
        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            report = json.loads(archive.read("report.json"))
        self.assertEqual(report["components"], [])
        self.assertEqual(report["game_hashes"]["game/sys/game.dll"], sha256(self.game / "game/sys/game.dll"))

    def test_desktop_write_failure_falls_back_to_package_folder(self):
        self.desktop.rmdir()
        self.desktop.write_text("not a directory", encoding="utf-8")
        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        fallback = list(self.package.glob("HardTruckCrashReport-*.zip"))
        self.assertEqual(self.reports(), [])
        self.assertEqual(len(fallback), 1)
        with zipfile.ZipFile(fallback[0]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("report.json", archive.namelist())

    def test_full_custom_and_reinstalled_components_keep_exact_versions(self):
        for _ in range(2):
            result = self.run_setup("Custom", components=["config", "camera"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((self.package / "installer/payload/manifest.json").read_text(encoding="utf-8"))

        components = self.report_components()
        self.assertEqual(set(components), {"config", "camera"})
        for component_id, component in components.items():
            self.assertEqual(component, {
                "id": component_id,
                "status": "installed",
                "version": manifest["components"][component_id]["version"],
            })

    def test_missing_or_corrupt_payload_marks_component_partial_with_null_version(self):
        installed = self.run_setup("InstallAll")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        manifest = json.loads((self.package / "installer/payload/manifest.json").read_text(encoding="utf-8"))
        handling_files = manifest["data_components"]["handling_defs"]
        self.assertGreater(len(handling_files), 1)
        corrupt = self.game / "game/usr" / handling_files[0].removeprefix("data/")
        corrupt.write_bytes(corrupt.read_bytes() + b"corrupt")

        components = self.report_components()
        self.assertEqual(components["handling_defs"], {
            "id": "handling_defs",
            "status": "partial",
            "version": None,
        })
        for component_id in COMPONENT_IDS - {"handling_defs"}:
            self.assertEqual(components[component_id]["status"], "installed")
            self.assertEqual(
                components[component_id]["version"],
                manifest["components"][component_id]["version"],
            )

        missing = self.game / "game/usr" / manifest["data_components"]["config"][0].removeprefix("data/")
        missing.unlink()
        components = self.report_components()
        self.assertEqual(components["config"]["status"], "partial")
        self.assertIsNone(components["config"]["version"])
        self.assertEqual(components["handling_defs"]["status"], "partial")

    def test_uninstall_partial_reports_only_skipped_component_as_partial(self):
        installed = self.run_setup("InstallAll")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        manifest = json.loads((self.package / "installer/payload/manifest.json").read_text(encoding="utf-8"))
        modified = self.game / "game/usr" / manifest["data_components"]["config"][0].removeprefix("data/")
        modified.write_bytes(modified.read_bytes() + b"user edit")
        uninstalled = self.run_setup("Uninstall")
        self.assertEqual(uninstalled.returncode, 0, uninstalled.stdout + uninstalled.stderr)
        backup = next((self.package / "installer/backups").glob("*/installation.json"))
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["status"], "UninstallPartial")

        self.assertEqual(self.report_components(), {
            "config": {"id": "config", "status": "partial", "version": None},
        })

    def test_rollback_failed_mixed_history_uses_current_payload_state(self):
        manifest = json.loads((self.package / "installer/payload/manifest.json").read_text(encoding="utf-8"))
        entries = []

        def add_entry(destination, installed_hash, existed=False, original_hash=None):
            entries.append({
                "destination": destination,
                "installed_sha256": installed_hash,
                "existed": existed,
                "backup_file": None,
                "original_sha256": original_hash,
            })

        for payload in manifest["data_components"]["config"]:
            add_entry("game/usr/" + payload.removeprefix("data/"), manifest["payloads"][payload])

        handling_files = manifest["data_components"]["handling_defs"]
        self.assertGreater(len(handling_files), 2)
        for index, payload in enumerate(handling_files):
            destination = "game/usr/" + payload.removeprefix("data/")
            add_entry(destination, manifest["payloads"][payload])
            if index == 0:
                target = self.game / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.package / "installer/payload" / payload, target)
            elif index == 1:
                target = self.game / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"remaining mismatched payload")

        add_entry(
            "game/sys/game.dll",
            manifest["game_variants"]["0001"]["sha256"],
            existed=True,
            original_hash=sha256(self.game / "game/sys/game.dll"),
        )
        add_entry(
            "sys/dx8.dll",
            manifest["dx8"]["vsync"]["sha256"],
            existed=True,
            original_hash=sha256(self.game / "sys/dx8.dll"),
        )
        backup = self.package / "installer/backups/mixed-rollback"
        backup.mkdir(parents=True)
        (backup / "installation.json").write_text(json.dumps({
            "schema_version": 1,
            "status": "RollbackFailed",
            "created": "2026-10-25T00:00:00Z",
            "game_path": str(self.game),
            "error": "synthetic mixed rollback",
            "rollback_errors": ["synthetic remaining payload"],
            "entries": entries,
        }), encoding="utf-8")

        self.assertEqual(self.report_components(), {
            "handling_defs": {"id": "handling_defs", "status": "partial", "version": None},
        })

    def test_zip_logs_redact_prefixed_ipv4_and_ipv6_but_preserve_timestamps_and_three_part_versions(self):
        addresses = [
            "192.0.2.10",
            "10.0.0.1",
            "1.2.3.4",
            "192.0.2.20",
            "203.0.113.9",
            "2001:db8::1",
            "::1",
            "2001:db8:0:1:1:1:1:1",
        ]
        prefixed = {
            "version 1.2.3.4": "version <IP>",
            "v192.0.2.20": "v<IP>",
            "server=203.0.113.9": "server=<IP>",
        }
        ordinary_text = [
            "2026-10-25T12:34:56Z",
            "12:34:56",
            "v2.3.4",
            "version 1.2.3",
            "release 1.2.3",
            "build 999.1.1.1",
        ]
        text = "\n".join(addresses + list(prefixed) + ordinary_text) + "\n"
        (self.game / "prism3d.crash").write_text(text, encoding="utf-8")
        (self.game / "prism3d.log").write_text(text, encoding="utf-8")

        result = self.run_setup("CrashReport")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(self.reports()[0]) as archive:
            for name in ("prism3d.crash", "prism3d.log"):
                sanitized = archive.read(name).decode("utf-8")
                for address in addresses:
                    self.assertNotIn(address, sanitized, f"{address} leaked in {name}")
                self.assertGreaterEqual(sanitized.count("<IP>"), len(addresses))
                for value in prefixed.values():
                    self.assertIn(value, sanitized)
                for value in ordinary_text:
                    self.assertIn(value, sanitized)

    def test_manual_and_menu_report_actions_skip_game_closed_confirmation(self):
        manual = self.run_setup("CrashReport", interactive=True, input_text="\n")
        self.assertEqual(manual.returncode, 0, manual.stdout + manual.stderr)
        self.assertNotIn("game is closed", manual.stdout + manual.stderr)
        self.assertEqual(len(self.reports()), 1)

        menu = self.run_setup("Menu", interactive=True, input_text="6\n\n")
        self.assertEqual(menu.returncode, 2, menu.stdout + menu.stderr)
        self.assertIn("Create crash report", menu.stdout)
        self.assertNotIn("game is closed", menu.stdout + menu.stderr)
        self.assertEqual(len(self.reports()), 2)

    def test_menu_keeps_quit_at_four_and_play_at_five(self):
        quit_result = self.run_setup("Menu", interactive=True, input_text="4\n")
        self.assertEqual(quit_result.returncode, 2, quit_result.stdout + quit_result.stderr)
        self.assertIn("4. Quit", quit_result.stdout)
        self.assertIn("5. Play modded", quit_result.stdout)
        self.assertIn("6. Create crash report", quit_result.stdout)

        play_result = self.run_setup("Menu", interactive=True, input_text="5\n")
        self.assertEqual(play_result.returncode, 2, play_result.stdout + play_result.stderr)
        self.assertNotIn("game is closed", play_result.stdout + play_result.stderr)
        self.assertEqual(self.reports(), [])
        self.assertEqual(self.marker.read_text(encoding="utf-8").splitlines()[0], str(self.game))


if __name__ == "__main__":
    unittest.main()
