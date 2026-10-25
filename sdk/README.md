# Hard Truck Mod SDK

The SDK is the only active authoring and release path for Hard Truck Modern. Players do not need Python: they extract the release ZIP and run `Setup.bat`. Authors use Python 3 and the repository's standard-library-only SDK.

The source of truth is:

- `modpack/mods/<id>/mod.json` - one component's metadata and owned data or registered binary handler.
- `modpack/mods/<id>/files/` - data source files.
- `modpack/mods/<id>/patch.py` - binary implementation, exposing `MOD`.
- `modpack/release.json` - ordered release components and explicitly curated game variants.
- `sdk/htmod/manifest.py` and `sdk/htmod/runner.py` - static handler allowlists. JSON never names a Python path and no plugin directory is scanned.

Do not add a payload copy map, standalone patcher, or release-only special case. Experimental FOV code under `modpack/patches/patch_fov_horplus.py` and `modpack/src/htd8/` is excluded research, not an SDK component and not a safe release feature.

## Author workflow

1. Add or change a manifest-owned mod and, for a binary mod, its statically registered handler.
2. Add focused tests and update golden hashes only when a payload change is intentional.
3. Validate and list the release:
   ```sh
   python3 tools/htmod.py validate
   python3 tools/htmod.py list
   ```
4. Run the committed suite from the repository root:
   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
   ```
   Installer runtime tests need PowerShell 7 on the test machine. Set `PWSH=/path/to/pwsh` when it is not on `PATH`; without PowerShell those tests skip explicitly. Pinned binary fixtures also need the repository's normal `test-base` input or `HTMOD_TEST_BASE`.
5. Optionally run a committed live smoke on a disposable installed game copy (see below).
6. Build the frozen patcher, then build twice into separate empty directories with that same executable and compare every file:
   ```sh
   python3 tools/build_windows_patcher.py --work-dir /tmp/htmod-patcher-build \
     --output /tmp/htmod-build-htmod.exe
   python3 tools/build_windows_release.py --patcher-exe /tmp/htmod-build-htmod.exe \
     --output /tmp/htmod-build-a
   python3 tools/build_windows_release.py --patcher-exe /tmp/htmod-build-htmod.exe \
     --output /tmp/htmod-build-b
   diff -r /tmp/htmod-build-a /tmp/htmod-build-b
   ```
   The patcher build recreates its dedicated Wine prefix and installs hash-locked requirements. The release builder uses pinned pristine v1.2 `game.dll` and `sys/dx8.dll` only for verification; override their locations with `--game-dll` and `--dx8-dll` when necessary. Schema-3 output contains `htmod.exe`, `migrations.json`, data, and expected hashes, but no game/DX8 DLL payloads. A custom `--migrations` catalog must contain exactly both canonical targets, pristine lengths/hashes matching those build inputs, and every required current, released legacy, and DX8 source state.
7. Package the `HardTruckModern` directory, fresh-extract that exact ZIP, and run the **complete** suite against it before publishing. With `HTMOD_RELEASE_ZIP` set, both the PowerShell installer tests and `tests/test_shell_installer.py` extract and exercise that ZIP:
   ```sh
   PWSH=/path/to/pwsh HTMOD_RELEASE_ZIP=/path/to/HardTruckModern-v3.zip \
     PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
   ```
   Check ZIP integrity and forbidden files (`__pycache__`, Python bytecode, research, test prefixes, logs, saves, backups, and other non-release content). Never publish merely because an in-tree build or only the PowerShell suite passed.

## Example: data mod

Create `modpack/mods/example_data/files/ui/example.sc`, then `modpack/mods/example_data/mod.json`:

```json
{
  "schema_version": 1,
  "id": "example_data",
  "version": "1.0.0",
  "kind": "data",
  "setup": {
    "label": "Example data",
    "description": "Example UI override",
    "default": false
  },
  "requires": ["config"],
  "conflicts": [],
  "new_profile": false,
  "files": [
    {"source": "ui/example.sc", "destination": "game/usr/ui/example.sc"}
  ]
}
```

Add `example_data` to `modpack/release.json`'s ordered `mods` list. Destinations must stay under `game/usr`; saves, music, logs, bytecode, unsafe paths, Windows reserved/device names, colons, trailing dots/spaces, Windows-special/control characters, and case-insensitive duplicate ownership are rejected. The only current transform is `strip_r_renderer`, used by `config`. A definition-changing mod should set `new_profile` to `true` so Setup warns about incompatible old saves.

## Example: anchored operand patch

Use `BytePatch` for a small replacement and anchors for the surrounding known bytes. Resolve RVAs through `PEImage` instead of assuming file offsets. A minimal handler has this shape:

```python
from htmod.errors import SignatureError
from htmod.patch import Anchor, BinaryMod, BytePatch


class ExampleOperandMod(BinaryMod):
    def __init__(self):
        super().__init__("example_operand", "game/sys/game.dll", ())

    def patches_for(self, image):
        site = image.rva_to_offset(0x12340, 4)
        context = image.rva_to_offset(0x12338, 8)
        if site is None or context is None:
            raise SignatureError("example patch site is not mapped")
        return (BytePatch(
            "example operand",
            site,
            bytes.fromhex("34120000"),
            bytes.fromhex("78560000"),
            (Anchor(context, bytes.fromhex("9090909090909090")),),
        ),)


MOD = ExampleOperandMod()
```

The addresses and bytes above are illustrative, not a patch for the game. For a real mod, add a unique context signature as in `modpack/mods/physics/patch.py`, pin the pristine source SHA-256, and test ORIGINAL, PATCHED, FOREIGN, apply/check/restore, and composition. Ambiguous or foreign input must be refused without mutation.

For a bounded immediate change whose original and patched instruction can occur at the same site, follow `modpack/mods/dx8_vsync/patch.py`: bound the search to one function and require exactly one complete state.

## Example: code hook

A code hook is a stateful `BinaryMod`, not an arbitrary script run by the builder. `modpack/mods/camera/patch.py` is the complete reference for:

1. finding and validating inline hook sites;
2. generating helper code and relative `call`/`jmp` operands;
3. appending an owned section with `PEImage.append_section`;
4. recording imported absolute addresses with `append_highlow_relocations`;
5. inspecting the complete original/patched state and reversing with `remove_highlow_relocations` and `remove_section`.

A relative branch helper is only this small part of the implementation:

```python
import struct


def jump(source_va, destination_va):
    displacement = destination_va - (source_va + 5)
    return b"\xe9" + struct.pack("<i", displacement)
```

The handler must also verify section flags/layout, relocation ownership, padding, file length, resume addresses, and foreign/trailing data. SizeOfImage uses `image.section_alignment`; HIGHLOW relocation blocks are DWORD-padded with a zero entry when the real entry count is odd. `PatchRunner` applies selected mods to in-memory copies and restores in reverse order; its file commit verifies preflight and post-write bytes and retains recovery backups after incomplete rollback. Do not write directly to a game DLL from a handler.

## Register a binary mod

Using `example_operand` as the ID:

1. Put the implementation in `modpack/mods/example_operand/patch.py` and expose `MOD` with matching `id` and `target`; loading rejects either identity mismatch with a clear error.
2. Add `example_operand` to both `BINARY_HANDLERS` in `sdk/htmod/manifest.py` and `REGISTERED_BINARY_HANDLERS` in `sdk/htmod/runner.py`.
3. Create its binary `mod.json` with `handler: "example_operand"`, a target such as `game/sys/game.dll`, and the pinned pristine `source_sha256`.
4. Add the ID to `modpack/release.json`. Dependencies and conflicts reference other registered IDs:
   ```json
   "requires": ["config"],
   "conflicts": ["example_other"]
   ```
   `Selection.validate` rejects missing selections and conflicts before file changes; generated schema-3 metadata carries the same rules. Handler loading itself is identity-only so the builder can load every game handler even when two handlers conflict. Each curated build selection is then validated with non-game release components available, allowing independent conflicting variants and data requirements such as `config` to build; the installer still validates the user's actual selection.
5. For a `game.dll` component, deliberately extend `game_variants` in `modpack/release.json`. Game-component bit positions follow release order; every variant name has exactly one bit per game component, and its `components` list must match those bits. The builder iterates only this curated list-never an automatically generated exponential matrix-and generates expected hashes in memory without game/DX8 DLL payloads. When adding a component, preserve every prior payload byte stream while widening names and curated combinations as required, and retain those prior hashes as recognized installed states. Record hashes from released goldens in the optional `legacy_game_sha256` list: schema validation requires unique lowercase SHA-256 strings, and release builds require every one to have migration source coverage. The schema-3 payload manifest omits this redundant list because `migrations.json` owns source recognition; the shell metadata retains hash-only `legacy-game` records for its legacy test/install path. Unknown DLL hashes remain refused during staging, and legacy payloads are never shipped. Other new targets require explicit builder and migration-catalog support driven by validated release metadata. The generated shell install-all manifest also requires one variant containing every game component.

## Check, apply, and restore on a test game

Back up and use a disposable v1.2 game folder. `--game-dir` is the folder containing `prism3d.exe`; repeat `--mod` in the intended order:

```sh
python3 tools/htmod.py check --game-dir "/path/to/test game" \
  --mod config --mod camera --mod physics --mod ai --mod ui_keyboard
python3 tools/htmod.py apply --game-dir "/path/to/test game" \
  --mod config --mod camera --mod physics --mod ai --mod ui_keyboard
python3 tools/htmod.py restore --game-dir "/path/to/test game" \
  --mod config --mod camera --mod physics --mod ai --mod ui_keyboard
python3 tools/htmod.py check --game-dir "/path/to/test game" --mod dx8_vsync
```

These commands validate the actual requested selection, including data-component requirements, and then load only registered binary handlers. Before any action, each target must either match the manifest's pinned pristine `source_sha256` or match an **exact recognized whole-file state**. For a non-pristine target, the SDK restores the selected mods in memory and requires the pinned pristine hash, then applies every selected-mod subset (bounded at four target mods) to that verified pristine image and compares complete bytes. Local PATCHED anchors or appended unknown data are not sufficient. To add a mod to an already patched target, select both existing and new mods; selecting only the new one does not recognize the existing state.

Apply and restore skip mods already in the requested state, so valid composed installs and reruns are idempotent. Before commit, every selected mod must be PATCHED after apply or ORIGINAL after restore; FOREIGN or the opposite valid state fails with a nonzero CLI result and no commit. Builder input pins remain global and unchanged.

Data is materialized into the release by manifests. End users install both data and binary components through Windows `Setup.bat` or the generated install-all `INSTALL.sh` on Linux/Wine.

## Live Wine smokes

Prerequisites: Wine with 32-bit application support, Xvfb, openbox, xdotool, scrot, Python 3, and a disposable full game copy. Use a fresh/dedicated prefix and an unused display for each run. The harness starts only its own Xvfb/openbox/Wine processes, records their PIDs in `owned-pids`, terminates only those PIDs, and scopes `wineserver -k` to the caller's dedicated prefix. It never uses `pkill` or name-based cleanup. Owned Xvfb/openbox/Wine liveness is checked before dependent startup, window search, and scrot; an already-used or dead display aborts before xdotool/scrot interaction and triggers owned-process cleanup.

Install `config` (plus `camera` for the camera smoke, or `ui_keyboard` for the accessibility and Credits smokes) into the test copy with the generated release Setup first. Set `uset r_mode` in that copy's `game/usr/config.cfg` to match `--resolution`. The camera smoke also requires these **test-copy-only** bindings; they are not added to the shipped config, and the harness refuses to run without them:

```text
bind_imp y "mleft"
bind_imp o "mright"
bind_imp i "mlookup"
bind_imp k "mlookdown"
```

Then run, for example:

```sh
python3 tests/live/quick_setup.py \
  --game "/tmp/htmod-live/game" --prefix "/tmp/htmod-live/prefix-ui" \
  --output "/tmp/htmod-live/ui-1024" --display :121 --resolution 1024x768

python3 tests/live/camera.py \
  --game "/tmp/htmod-live/game" --prefix "/tmp/htmod-live/prefix-camera" \
  --output "/tmp/htmod-live/camera-1920" --display :122 --resolution 1920x1080

python3 tests/live/accessibility.py \
  --game "/tmp/htmod-live/game" --prefix "/tmp/htmod-live/prefix-accessibility" \
  --output "/tmp/htmod-live/accessibility-1920" --display :123 --resolution 1920x1080

python3 tests/live/credits.py \
  --game "/tmp/htmod-live/game" --prefix "/tmp/htmod-live/prefix-credits" \
  --output "/tmp/htmod-live/credits-1024" --display :124 --resolution 1024x768 \
  --page-wait 4
```

Each script writes screenshots, Wine/Xvfb/openbox logs, and `summary.json`. The camera smoke captures stock cabin level/left/right views, vertical-input cases that must leave the cabin view unmodified, separate working orbit down/up/reset cases, external truck views, and cameras 1–7. It locates the parked player through its validated `game.dll` vtable/mass/weight, reads `/proc/<owned-Wine-PID>/mem` without writing, saves raw samples plus `truck-samples.json`, and fails if the 64-byte physical transform changes. Discovery briefly uses `ptrace`; Linux ptrace permissions must allow the harness parent to inspect its child. This check supplements rather than replaces visual review.

Process and log checks do not prove visual correctness: inspect menu/Quick Setup clipping, stock cabin left/right behavior, absence of cabin vertical modification, working orbit direction/reset, and camera switches yourself. For accessibility, inspect both caller-selected resolutions (1024x768 and 1920x1080): main Tab/Shift+Tab focus, Enter opening Options, the larger text/targets, `>` focus and `[x]` selection on Graphics/Sound/Controls, the dark Quick panel, and company→owner→OK focus. The smoke types `A`/`B` into the disposable copy's company/owner fields so OK is enabled and its focus highlight is observable. The Quick Setup flow uses `map west` and `ui t cosetup`; **it never presses OK**, because that action is known to crash in the headless flow. Options uses `ui t` only to hide the active subpage before keyboard radio navigation. Only this accessibility smoke allows the known headless `No modes found` Graphics log line; other unexpected errors still fail.

The Credits smoke selects Credits from the main menu and captures each of the five timed pages. Run it at both 1024x768 and 1920x1080, and visually inspect every capture for persistent **Hard Truck Modern** and **MOD SDK: LeonKaarneem** lines, stock page text and final thanks, and no clipping or overlap. `--page-wait` controls the interval between page captures (default 3 seconds); the measured acceptance values were 4 at 1024x768 and 3 at 1920x1080, so adjust and rerun if a transition is captured instead of a stable page. The log gate requires `ui/mcredits.sc` to load but does not require GAME state. Live evidence showed that the initially proposed ID3005/`tabX` group does not render; the source therefore repeats the same two-line block in visible groups ID3000–ID3004 and leaves ID3005 stock.

The accessibility smoke does not test generic Space activation, screen readers, reduced motion, runtime scaling, generated binding rows, device-dependent joystick controls, native pointer/edge activation, or native Windows. Those remain limitations rather than implied passes. Wine/Xvfb mouse activation and post-console deadlocks can still prevent a run; report those as limitations, not passes. Reusing a caller prefix can shut down its other Wine processes during cleanup, so a dedicated prefix is mandatory.

## Crash-report contract

`Play Modded.bat` and `CrashReport.bat` forward all arguments to `Setup.ps1 -Action Play` and `-Action CrashReport`; the same actions are Setup menu options 5 and 6, while option 4 remains Quit. Direct interactive Play on Windows first shows a fixed centered DPI-scaled WinForms dialog with **MOD SDK: LeonKaarneem**. Play/Enter continues; Cancel/Escape/window close exits before creating a process or report. Menu Play and noninteractive execution bypass the dialog. Accepted Play uses the game directory as its working directory, passes `-game game -icon game.ico -title "Hard Truck Modern"`, and waits for exit. Normal exit creates no report; a nonzero exit or newer crash file offers a default-Yes report. Manual reporting asks for confirmation. Neither action intentionally crashes the game. Native Windows dialog appearance, keyboard interaction, and DPI rendering are source-contract tested only and remain unverified from Linux.

Reports go to Desktop with package-folder fallback and contain only sanitized engine crash text, at most the last 4000 engine log lines, `report.json`, and `README.txt`. Required metadata covers schema/UTC time, available launch/exit details, executable/DLL hashes, payload schema, SDK-manifest/backup-derived component state, basic OS/GPU data, and log presence. Every entry redacts username, computer name, home/game/package paths, and IP addresses. Saves, profiles, backup payloads, memory dumps, registry exports, dxdiag/MSInfo, and arbitrary documents are excluded; there is no automatic upload.

`tests/test_crash_reporting.py` inspects real ZIPs for paths with spaces, missing/corrupt logs, unpatched games, Desktop fallback, required metadata, the 4000-line bound, all-entry privacy (including IPv4/IPv6), and batch/menu compatibility. When `HTMOD_RELEASE_ZIP` is set, these tests extract and exercise that exact release instead of an in-tree build. Use fake executables for these tests; never intentionally crash a real game for a demonstration.

## Release rules

Keep the Python-free end-user experience unchanged. `tools/templates/Setup.ps1` consumes generated component metadata; do not duplicate labels, dependencies, conflicts, data ownership, component IDs, or variant-selection rules in PowerShell. Preserve schema-1 backup compatibility, known-hash gates, backups, rollback, uninstall history, save/unrelated-file guards, and automatic detection. `tools/templates/INSTALL.sh` consumes generated `install-manifest.tsv` for a simple Linux/Wine install-all path with payload validation, per-run backups, and reverse-order rollback on ordinary mid-install failure; retain manual recovery instructions for catastrophic interruption and do not add a second payload mapping. Windows `Setup.bat` remains unchanged.

The experimental FOV attempt remains outside manifests, registry, release payloads, and supported author examples. Native Windows PowerShell 5.1 and visual live checks require separate evidence on environments where they actually ran.
