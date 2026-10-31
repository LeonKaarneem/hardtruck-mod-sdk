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
    FIXED_EYE_GAME_SHA256,
    LEGACY_GAME_SHA256,
    write_fixed_eye_variant,
    write_legacy_variant,
)


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
    "ui_keyboard",
    "dx8_vsync",
]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipIf(
    PWSH is None or not (os.environ.get("HTMOD_RELEASE_ZIP") or os.environ.get("HTMOD_TEST_PATCHER_EXE")),
    "PowerShell 7 and a real frozen patcher are required for installer runtime tests",
)
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
            timeout=120,
        )

    def manifest(self, package):
        return json.loads((Path(package) / "installer/payload/manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, package, manifest):
        (Path(package) / "installer/payload/manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def fake_patcher_environment(self, package, root, plan):
        patcher = Path(package) / "installer/payload/htmod.exe"
        patcher.write_bytes(b"fake patcher")
        manifest = self.manifest(package)
        manifest["payloads"]["htmod.exe"] = sha256(patcher)
        self.write_manifest(package, manifest)
        binary = Path(root) / "fake-bin"
        binary.mkdir()
        commands = Path(root) / "fake-commands"
        wine = binary / "wine"
        wine.write_text(r'''#!/bin/sh
set -eu
command_name=
game_dir=
output_dir=
previous=
for argument in "$@"; do
    if [ "$previous" = "--game-dir" ]; then game_dir=$argument; fi
    if [ "$previous" = "--output-dir" ]; then output_dir=$argument; fi
    if [ -z "$command_name" ]; then
        case $argument in plan|stage) command_name=$argument ;; esac
    fi
    previous=$argument
done
printf '%s\n' "$command_name" >> "$HTMOD_TEST_COMMANDS"
if [ "$command_name" = "plan" ]; then
    printf '%s\n' "$HTMOD_TEST_PLAN"
    exit 0
fi
if [ "$command_name" = "stage" ]; then
    game_dir=$(printf '%s' "${game_dir#Z:}" | tr '\\' '/')
    output_dir=$(printf '%s' "${output_dir#Z:}" | tr '\\' '/')
    mkdir -p "$output_dir/game/sys" "$output_dir/sys"
    if [ "${HTMOD_TEST_WRONG_STAGE:-0}" = "1" ]; then
        printf 'wrong staged game' > "$output_dir/game/sys/game.dll"
        printf 'wrong staged dx8' > "$output_dir/sys/dx8.dll"
    else
        cp "$game_dir/game/sys/game.dll" "$output_dir/game/sys/game.dll"
        cp "$game_dir/sys/dx8.dll" "$output_dir/sys/dx8.dll"
    fi
    exit 0
fi
exit 64
''', encoding="utf-8")
        wine.chmod(0o755)
        return {
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "HTMOD_TEST_PLAN": plan if isinstance(plan, str) else json.dumps(plan, separators=(",", ":")),
            "HTMOD_TEST_COMMANDS": str(commands),
        }

    def backup_manifests(self, package):
        return sorted(Path(package).glob("installer/backups/*/installation.json"))

    def assert_stock(self, game):
        manifest = self.manifest(self.package)
        self.assertEqual(sha256(Path(game) / "game/sys/game.dll"), manifest["game_variants"]["0000"]["sha256"])
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
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), manifest["game_variants"]["1111"]["sha256"])
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
        self.assertEqual(len(installation["entries"]), 22)

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
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), manifest["game_variants"]["0001"]["sha256"])
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), manifest["dx8"]["stock"]["sha256"])
        quick_setup = self.game / "game/usr/ui/cosetup.sc"
        self.assertTrue(quick_setup.is_file())
        content = quick_setup.read_bytes()
        self.assertIn(b"<br>Enabled: configuration, camera\"\r\n", content)
        self.assertNotIn(b"Features depend on Setup choices", content)
        self.assertNotIn(b"handling", content.split(b"Enabled:", 1)[1])
        installation = json.loads(self.backup_manifests(self.package)[0].read_text(encoding="utf-8"))
        entry = next(
            item for item in installation["entries"]
            if item["destination"] == "game/usr/ui/cosetup.sc"
        )
        self.assertEqual(entry["installed_sha256"], sha256(quick_setup))
        self.assertFalse((self.game / "game/usr/defs/truck_base.def").exists())

    def test_custom_dependency_closure_and_data_selection_come_from_plan(self):
        result = self.run_setup(self.package, self.game, action="Custom", components=["ui_keyboard"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            sha256(self.game / "game/sys/game.dll"),
            self.manifest(self.package)["game_variants"]["1000"]["sha256"],
        )
        self.assertIn(
            b"<br>Enabled: configuration, keyboard\"\r\n",
            (self.game / "game/usr/ui/cosetup.sc").read_bytes(),
        )

    def test_noop_binary_targets_use_current_hashes_instead_of_curated_metadata(self):
        manifest = self.manifest(self.package)
        expected_game = sha256(self.game / "game/sys/game.dll")
        expected_dx8 = sha256(self.game / "sys/dx8.dll")
        for record in manifest["game_variants"].values():
            record["sha256"] = "0" * 64
        for record in manifest["dx8"].values():
            record["sha256"] = "0" * 64
        self.write_manifest(self.package, manifest)

        result = self.run_setup(self.package, self.game, action="Custom", components=["config"])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), expected_game)
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), expected_dx8)
        installation = json.loads(self.backup_manifests(self.package)[0].read_text(encoding="utf-8"))
        self.assertFalse([entry for entry in installation["entries"] if entry["destination"].endswith(".dll")])

    def test_changed_binary_expectation_comes_from_plan_instead_of_curated_metadata(self):
        manifest = self.manifest(self.package)
        expected_game = manifest["game_variants"]["0001"]["sha256"]
        expected_dx8 = manifest["dx8"]["stock"]["sha256"]
        for record in manifest["game_variants"].values():
            record["sha256"] = "0" * 64
        for record in manifest["dx8"].values():
            record["sha256"] = "0" * 64
        self.write_manifest(self.package, manifest)

        result = self.run_setup(self.package, self.game, action="Custom", components=["camera"])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), expected_game)
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), expected_dx8)

    def test_every_curated_variant_uses_existing_noninteractive_component_ids(self):
        manifest = self.manifest(self.package)
        for number in range(16):
            name = f"{number:04b}"
            components = [
                component
                for bit, component in enumerate(("camera", "physics", "ai", "ui_keyboard"))
                if number & (1 << bit)
            ]
            if "ui_keyboard" in components:
                components.insert(0, "config")
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

    def test_synthetic_unknown_binary_component_is_refused_before_backup(self):
        manifest = self.manifest(self.package)
        manifest["components"]["fourth"] = {
            "label": "Fourth",
            "description": "Synthetic fourth game component",
            "default": False,
            "requires": [],
            "conflicts": [],
            "new_profile": False,
            "kind": "binary",
            "target": "game/sys/game.dll",
            "version": "1.0.0",
        }
        variants = {}
        for name, record in manifest["game_variants"].items():
            variants[name + "0"] = dict(record)
        variants["10000"] = {"components": ["fourth"], "sha256": "0" * 64}
        manifest["game_variants"] = variants
        self.write_manifest(self.package, manifest)

        result = self.run_setup(self.package, self.game, action="Custom", components=["fourth"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown mod", result.stdout + result.stderr)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), manifest["game_variants"]["00000"]["sha256"])
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), manifest["dx8"]["stock"]["sha256"])
        self.assertFalse(self.backup_manifests(self.package))

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

    def test_new_profile_warning_uses_dependency_expanded_plan_selection(self):
        manifest = self.manifest(self.package)
        for component in manifest["components"].values():
            component["new_profile"] = False
        manifest["components"]["config"]["new_profile"] = True
        self.write_manifest(self.package, manifest)

        result = self.run_setup(self.package, self.game, action="Custom", components=["ui_keyboard"])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("new profile", (result.stdout + result.stderr).lower())
        self.assertTrue((self.game / "game/usr/config.cfg").is_file())
        self.assertEqual(
            sha256(self.game / "game/sys/game.dll"),
            manifest["game_variants"]["1000"]["sha256"],
        )

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
        plan = {
            "schema_version": 1,
            "mode": "exact",
            "requested": ["extra"],
            "selected": ["extra"],
            "no_op": False,
            "data_changes": [],
            "binary_changes": [],
        }
        environment = self.fake_patcher_environment(self.package, self.root, plan)
        result = self.run_setup(
            self.package,
            self.game,
            action="Custom",
            components=["extra"],
            env=environment,
        )
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
                self.assertRegex((result.stdout + result.stderr).lower(), "unknown|unsupported|recognized")
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

    def test_corrupt_patcher_and_catalog_fail_before_backup(self):
        for failure in ("patcher", "catalog"):
            with self.subTest(failure=failure):
                root = self.root / f"stage-failure-{failure}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                if failure == "patcher":
                    (package / "installer/payload/htmod.exe").write_bytes(b"not an executable")
                else:
                    (package / "installer/payload/migrations.json").write_text("{", encoding="utf-8")
                before_game = (game / "game/sys/game.dll").read_bytes()
                before_dx8 = (game / "sys/dx8.dll").read_bytes()
                result = self.run_setup(package, game)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual((game / "game/sys/game.dll").read_bytes(), before_game)
                self.assertEqual((game / "sys/dx8.dll").read_bytes(), before_dx8)
                self.assertFalse((package / "installer/backups").exists())
                self.assertFalse((game / "game/usr/config.cfg").exists())

    @unittest.skipIf(os.name == "nt", "fake Wine command is a POSIX test fixture")
    def test_invalid_plan_is_refused_before_stage_backup_or_game_changes(self):
        cases = [
            ("malformed", "{"),
            ("schema", '{"schema_version":2}'),
            ("multiple", '{"schema_version":1}\n{"schema_version":1}'),
            ("scalar-plan", "1"),
        ]
        for name, field, value in (
            ("null-selected", "selected", None),
            ("scalar-selected", "selected", "config"),
            ("null-binary-changes", "binary_changes", None),
            ("scalar-data-changes", "data_changes", "invalid"),
            ("non-boolean-no-op", "no_op", "false"),
        ):
            plan = {
                "schema_version": 1,
                "mode": "exact",
                "requested": [],
                "selected": [],
                "no_op": True,
                "data_changes": [],
                "binary_changes": [],
            }
            plan[field] = value
            cases.append((name, json.dumps(plan, separators=(",", ":"))))
        for name, output in cases:
            with self.subTest(plan=name):
                root = self.root / f"invalid-plan-{name}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                environment = self.fake_patcher_environment(package, root, output)
                before = {
                    str(path.relative_to(game)): path.read_bytes()
                    for path in game.rglob("*")
                    if path.is_file()
                }

                result = self.run_setup(package, game, env=environment)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("plan", (result.stdout + result.stderr).lower())
                after = {
                    str(path.relative_to(game)): path.read_bytes()
                    for path in game.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(after, before)
                self.assertFalse((package / "installer/backups").exists())
                self.assertEqual((root / "fake-commands").read_text(encoding="utf-8").splitlines(), ["plan"])

    @unittest.skipIf(os.name == "nt", "fake Wine command is a POSIX test fixture")
    def test_declarative_plan_with_wrong_staged_hash_fails_before_backup(self):
        plan = {
            "schema_version": 1,
            "mode": "exact",
            "requested": [],
            "selected": [],
            "no_op": False,
            "data_changes": [],
            "binary_changes": [
                {
                    "target": target,
                    "action": "replace",
                    "variant": "fake",
                    "components": [],
                    "source_sha256": sha256(self.game / target),
                    "expected_sha256": "0" * 64,
                }
                for target in ("game/sys/game.dll", "sys/dx8.dll")
            ],
        }
        environment = self.fake_patcher_environment(self.package, self.root, plan)
        environment["HTMOD_TEST_WRONG_STAGE"] = "1"

        result = self.run_setup(self.package, self.game, env=environment)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout + result.stderr, r"Staged\s+(?:\|\s+)?DLL\s+hash\s+verification\s+failed")
        self.assertFalse(self.backup_manifests(self.package))
        self.assertEqual((self.root / "fake-commands").read_text(encoding="utf-8").splitlines(), ["plan", "stage"])

    def test_patcher_and_staged_hash_failures_clean_only_generated_staging(self):
        script = r'''
$root = $env:HTMOD_STAGE_TEST_ROOT
$source = Get-Content -LiteralPath (Join-Path $root "package/installer/Setup.ps1") -Raw
foreach ($name in @("Convert-FrozenPath", "Get-ComponentRecords", "Invoke-LifecyclePlan", "Invoke-StagedMods")) {
    $match = [regex]::Match($source, "(?ms)^function $name \{.*?^\}")
    if (-not $match.Success) { throw "Missing function $name" }
    Invoke-Expression $match.Value
}
function Test-Hash {
    param([string]$Path, [string]$ExpectedHash)
    return $Path -notlike "*staging-*game/sys/game.dll"
}
function winepath {
    param([switch]$w, [string]$Path)
    $global:LASTEXITCODE = 0
    return $Path
}
function wine {
    if ($args -contains "plan") {
        Write-Output '{"schema_version":1,"mode":"exact","requested":[],"selected":[],"no_op":true,"data_changes":[],"binary_changes":[]}'
        $global:LASTEXITCODE = 0
        return
    }
    $index = [Array]::IndexOf($args, "--output-dir")
    $stage = $args[$index + 1]
    $game = Join-Path $stage "game/sys/game.dll"
    $dx8 = Join-Path $stage "sys/dx8.dll"
    New-Item -ItemType Directory -Path (Split-Path -Parent $game) -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $dx8) -Force | Out-Null
    Set-Content -LiteralPath $game -Value "staged"
    Set-Content -LiteralPath $dx8 -Value "staged"
    $global:LASTEXITCODE = if ($env:HTMOD_STAGE_FAILURE -eq "patcher") { 1 } else { 0 }
}
$script:PayloadRoot = Join-Path $root "package/installer/payload"
$script:InstallerRoot = Join-Path $root "package/installer"
$script:GamePath = Join-Path $root "game"
$manifest = @{ payloads = @{ "htmod.exe" = "a"; "migrations.json" = "b" }; components = @{} }
$plan = Invoke-LifecyclePlan @{} $manifest
$jobs = @(
    @{ relative = "game/sys/game.dll"; hash = "c" },
    @{ relative = "sys/dx8.dll"; hash = "d" }
)
$threw = $false
try {
    Invoke-StagedMods $plan $manifest $jobs
}
catch {
    $threw = $true
}
if (-not $threw) { throw "Expected staged failure" }
$remaining = @(Get-ChildItem -LiteralPath $script:InstallerRoot -Directory -Filter "staging-*")
if ($remaining.Count -ne 0) { throw "Generated staging was not cleaned: $($remaining.FullName)" }
$sentinel = Join-Path $script:InstallerRoot "backups/existing/keep.txt"
if ((Get-Content -LiteralPath $sentinel -Raw) -ne "preserve") { throw "Backup sentinel changed" }
'''
        for failure in ("patcher", "staged-hash"):
            with self.subTest(failure=failure):
                root = self.root / f"staging-cleanup-{failure}"
                (root / "package/installer/backups/existing").mkdir(parents=True)
                (root / "package/installer/backups/existing/keep.txt").write_text("preserve", encoding="utf-8")
                (root / "package/installer/Setup.ps1").write_bytes((self.package / "installer/Setup.ps1").read_bytes())
                result = subprocess.run(
                    [PWSH, "-NoProfile", "-Command", script],
                    env={**os.environ, "HTMOD_STAGE_TEST_ROOT": str(root), "HTMOD_STAGE_FAILURE": failure},
                    text=True,
                    capture_output=True,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_directory_destination_is_refused_before_backup_or_game_changes(self):
        (self.game / "game/usr/ui/mmain.sc").mkdir(parents=True)

        def snapshot():
            return {
                str(path.relative_to(self.game)): path.read_bytes() if path.is_file() else None
                for path in self.game.rglob("*")
            }

        before = snapshot()
        result = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(result.returncode, 1)
        output = (result.stdout + result.stderr).lower()
        self.assertIn("frozen lifecycle planning failed", output)
        self.assertRegex(output, "unsafe|not a regular file")
        self.assertFalse(self.backup_manifests(self.package))
        self.assertEqual(snapshot(), before)

    def test_post_change_failure_rolls_back_every_changed_file_in_reverse(self):
        before = {
            str(path.relative_to(self.game)): path.read_bytes()
            for path in self.game.rglob("*")
            if path.is_file()
        }
        setup = self.package / "installer/Setup.ps1"
        source = setup.read_text(encoding="utf-8")
        install = "            Copy-Atomic $job.source $job.destination $job.hash\n"
        rollback = "    foreach ($entry in $Changes) {\n"
        self.assertEqual(source.count(install), 1)
        self.assertEqual(source.count(rollback), 1)
        source = source.replace(
            install,
            install
            + '            Add-Content -LiteralPath $env:HTMOD_TEST_CHANGE_LOG -Value ("installed:" + $job.relative)\n'
            + '            if ($job.relative -eq "game/usr/config.cfg") { throw "Injected post-change failure." }\n',
        )
        source = source.replace(
            rollback,
            rollback
            + '            Add-Content -LiteralPath $env:HTMOD_TEST_CHANGE_LOG -Value ("rollback:" + $entry.destination)\n',
        )
        setup.write_text(source, encoding="utf-8")
        change_log = self.root / "changes.log"

        result = self.run_setup(self.package, self.game, env={"HTMOD_TEST_CHANGE_LOG": str(change_log)})

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = change_log.read_text(encoding="utf-8").splitlines()
        installed = [line.removeprefix("installed:") for line in events if line.startswith("installed:")]
        restored = [line.removeprefix("rollback:") for line in events if line.startswith("rollback:")]
        self.assertGreater(len(installed), 1)
        self.assertEqual(installed[-1], "game/usr/config.cfg")
        self.assertEqual(restored, list(reversed(installed)))
        after = {
            str(path.relative_to(self.game)): path.read_bytes()
            for path in self.game.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        backups = self.backup_manifests(self.package)
        self.assertEqual(len(backups), 1)
        failure = json.loads(backups[0].read_text(encoding="utf-8"))
        self.assertEqual(failure["schema_version"], 1)
        self.assertEqual(failure["status"], "RolledBack")
        self.assertEqual(failure["error"], "Injected post-change failure.")
        self.assertEqual([entry["destination"] for entry in failure["entries"]], installed)

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

    def test_schema1_backup_uninstall_restores_exact_files_and_preserves_sentinels(self):
        fixture = ROOT / "tests/fixtures/schema1_config_backup"
        backup = self.package / "installer/backups/schema1"
        shutil.copytree(fixture, backup)
        installation_path = backup / "installation.json"
        installation = json.loads(installation_path.read_text(encoding="utf-8"))
        self.assertEqual(installation["schema_version"], 1)
        self.assertEqual(installation["status"], "Installed")
        installation["game_path"] = str(self.game)
        installation_path.write_text(json.dumps(installation, indent=2), encoding="utf-8")

        payload = self.package / "installer/payload/data"
        (self.game / "game/usr/ui").mkdir(parents=True)
        shutil.copyfile(payload / "config.cfg", self.game / "game/usr/config.cfg")
        shutil.copyfile(payload / "ui/mmain.sc", self.game / "game/usr/ui/mmain.sc")
        for entry in installation["entries"]:
            self.assertEqual(
                sha256(self.game / entry["destination"]),
                entry["installed_sha256"],
            )
        save = self.game / "game/usr/save/keep.sav"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"save sentinel\x00\xff")
        unrelated = self.game / "game/usr/ui/unrelated.txt"
        unrelated.write_bytes(b"unrelated sentinel\r\n")

        result = self.run_setup(self.package, self.game, action="Uninstall")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.game / "game/usr/config.cfg").read_bytes(),
            (fixture / "files/game/usr/config.cfg").read_bytes(),
        )
        self.assertFalse((self.game / "game/usr/ui/mmain.sc").exists())
        self.assertEqual(save.read_bytes(), b"save sentinel\x00\xff")
        self.assertEqual(unrelated.read_bytes(), b"unrelated sentinel\r\n")
        self.assert_stock(self.game)
        updated = json.loads(installation_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["schema_version"], 1)
        self.assertEqual(updated["status"], "Uninstalled")

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

    def test_direct_and_menu_quit_do_not_read_missing_or_corrupt_payload(self):
        for state in ("missing", "corrupt"):
            with self.subTest(state=state):
                root = self.root / f"quit-{state}"
                root.mkdir()
                package = self.make_package(root)
                manifest = package / "installer/payload/manifest.json"
                if state == "missing":
                    manifest.unlink()
                else:
                    manifest.write_text("{not json", encoding="utf-8")
                direct = self.run_setup(package, action="Quit")
                self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
                menu = self.run_setup(
                    package,
                    action="Menu",
                    interactive=True,
                    input_text="5\n",
                )
                self.assertEqual(menu.returncode, 2, menu.stdout + menu.stderr)


class LegacyInstallerUpgradeTests(InstallerCase):
    @classmethod
    def setUpClass(cls):
        build_release(cls)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_reported_legacy_all_components_dll_upgrades_with_verified_backup(self):
        target = self.game / "game/sys/game.dll"
        write_legacy_variant(self.package / "installer/payload", "1111", target)
        original = target.read_bytes()
        self.assertEqual(sha256(target), LEGACY_GAME_SHA256["1111"])

        result = self.run_setup(self.package, self.game, action="InstallAll")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
        backups = self.backup_manifests(self.package)
        self.assertEqual(len(backups), 1)
        installation = json.loads(backups[0].read_text(encoding="utf-8"))
        game_entry = next(entry for entry in installation["entries"] if entry["destination"] == "game/sys/game.dll")
        self.assertEqual(game_entry["original_sha256"], LEGACY_GAME_SHA256["1111"])
        backup = backups[0].parent / game_entry["backup_file"]
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(sha256(backup), LEGACY_GAME_SHA256["1111"])

    def test_estonian_install_all_and_uninstall_restore_exact_sources(self):
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

        result = self.run_setup(self.package, self.game, action="InstallAll")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(self.game / "game/sys/game.dll"), CURRENT_ALL_COMPONENTS_SHA256)
        self.assertEqual(
            sha256(self.game / "sys/dx8.dll"),
            "c900a47f5ac5e05116adea04895b8ac4171522a7812b50fc6a25b931bdb4b519",
        )
        backups = self.backup_manifests(self.package)
        self.assertEqual(len(backups), 1)
        installation = json.loads(backups[0].read_text(encoding="utf-8"))
        for relative, source, source_hash in (
            ("game/sys/game.dll", game_source, ESTONIAN_GAME_SHA256),
            ("sys/dx8.dll", dx8_source, ESTONIAN_DX8_SHA256),
        ):
            with self.subTest(path=relative):
                entry = next(item for item in installation["entries"] if item["destination"] == relative)
                self.assertEqual(entry["original_sha256"], source_hash)
                self.assertEqual((backups[0].parent / entry["backup_file"]).read_bytes(), source)

        uninstall = self.run_setup(self.package, self.game, action="Uninstall")

        self.assertEqual(uninstall.returncode, 0, uninstall.stdout + uninstall.stderr)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), game_source)
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), dx8_source)

    def test_every_broken_fixed_eye_variant_upgrades_with_backup(self):
        for name, fixed_eye_hash in FIXED_EYE_GAME_SHA256.items():
            with self.subTest(variant=name, fixed_eye_hash=fixed_eye_hash):
                root = self.root / f"fixed-eye-{name}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                target = game / "game/sys/game.dll"
                write_fixed_eye_variant(package / "installer/payload", name, target)
                original = target.read_bytes()
                result = self.run_setup(package, game, action="InstallAll")

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
                self.assertNotEqual(sha256(target), fixed_eye_hash)
                backups = self.backup_manifests(package)
                self.assertEqual(len(backups), 1)
                installation = json.loads(backups[0].read_text(encoding="utf-8"))
                game_entry = next(entry for entry in installation["entries"] if entry["destination"] == "game/sys/game.dll")
                self.assertEqual(game_entry["original_sha256"], fixed_eye_hash)
                self.assertEqual((backups[0].parent / game_entry["backup_file"]).read_bytes(), original)

    def test_every_released_legacy_game_hash_is_accepted(self):
        for name, legacy_hash in LEGACY_GAME_SHA256.items():
            with self.subTest(variant=name, legacy_hash=legacy_hash):
                root = self.root / f"legacy-{name}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                target = game / "game/sys/game.dll"
                write_legacy_variant(package / "installer/payload", name, target)

                result = self.run_setup(package, game, action="InstallAll")

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
                backups = [json.loads(path.read_text(encoding="utf-8")) for path in self.backup_manifests(package)]
                self.assertEqual(len(backups), 1)
                game_entry = next(entry for entry in backups[0]["entries"] if entry["destination"] == "game/sys/game.dll")
                self.assertEqual(game_entry["original_sha256"], legacy_hash)

    def test_every_current_game_variant_reinstalls_with_incoming_backup(self):
        from tests.release_fixtures import current_binary_images

        for name, source in current_binary_images()[0].items():
            with self.subTest(variant=name):
                root = self.root / f"current-{name}"
                root.mkdir()
                package = self.make_package(root)
                game = self.make_game(root)
                target = game / "game/sys/game.dll"
                target.write_bytes(source)
                result = self.run_setup(package, game, action="InstallAll")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sha256(target), CURRENT_ALL_COMPONENTS_SHA256)
                backups = [json.loads(path.read_text(encoding="utf-8")) for path in self.backup_manifests(package)]
                self.assertEqual(len(backups), 1)
                entries = [entry for entry in backups[0]["entries"] if entry["destination"] == "game/sys/game.dll"]
                if name == "1111":
                    self.assertEqual(entries, [])
                else:
                    self.assertEqual(len(entries), 1)
                    self.assertEqual((self.backup_manifests(package)[0].parent / entries[0]["backup_file"]).read_bytes(), source)

    def test_data_only_selection_removes_old_camera_and_preserves_incoming_backup(self):
        target = self.game / "game/sys/game.dll"
        write_legacy_variant(self.package / "installer/payload", "0001", target)
        original = target.read_bytes()
        result = self.run_setup(self.package, self.game, action="Custom", components=["config"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sha256(target), self.manifest(self.package)["game_variants"]["0000"]["sha256"])
        self.assertEqual(sha256(self.game / "sys/dx8.dll"), self.manifest(self.package)["dx8"]["stock"]["sha256"])
        installation = json.loads(self.backup_manifests(self.package)[0].read_text(encoding="utf-8"))
        entry = next(entry for entry in installation["entries"] if entry["destination"] == "game/sys/game.dll")
        self.assertEqual((self.backup_manifests(self.package)[0].parent / entry["backup_file"]).read_bytes(), original)
        self.assertTrue((self.game / "game/usr/ui/cosetup.sc").is_file())


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
        archive = self.root / "HardTruckModern-0.2.0.zip"
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
    archive = os.environ.get("HTMOD_RELEASE_ZIP")
    if archive:
        with zipfile.ZipFile(archive) as source:
            if source.testzip() is not None:
                raise RuntimeError(f"corrupt release ZIP: {archive}")
            source.extractall(cls.temporary.name)
        if not cls.release.is_dir():
            raise RuntimeError(f"release ZIP has no HardTruckModern root: {archive}")
        return
    from tests.release_fixtures import build_installer_fixture
    build_installer_fixture(cls.release)


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
