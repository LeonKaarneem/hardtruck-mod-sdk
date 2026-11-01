# Building and verifying releases

Player packages remain Python-free: users extract the ZIP and run `Setup.bat`, or use `INSTALL.sh` on Linux/Wine. Authors and release maintainers use Python 3 and the standard-library-only SDK.

## Test-prerequisite matrix

| Coverage | Prerequisites and selection |
|---|---|
| Synthetic manifest, PE, migration, lifecycle, and installer logic | Python 3 only. Run the committed suite; environment-dependent cases skip explicitly. |
| Pinned real binary fixtures | Provide canonical v1.2 `prism3d.exe`, `game/sys/game.dll`, and `sys/dx8.dll` under `test-base`, or set `HTMOD_TEST_BASE` to that root. These files are test inputs, never package content. |
| Real frozen patcher | Set `HTMOD_TEST_PATCHER_EXE=/path/to/htmod.exe`. Real execution also needs Wine and `winepath` at the paths used by the tests. Shell installer tests use the same variable. |
| PowerShell installer | Install PowerShell 7 and set `PWSH=/path/to/pwsh` when it is not on `PATH`. Runtime cases also need `HTMOD_TEST_PATCHER_EXE` or `HTMOD_RELEASE_ZIP`. |
| Exact release ZIP | Set `HTMOD_RELEASE_ZIP=/path/to/HardTruckModern-0.3.0.zip`. PowerShell and shell tests fresh-extract and exercise that exact ZIP, including its bundled patcher. |
| Live Wine smokes | Use a disposable full game, a fresh dedicated Wine prefix, Wine with 32-bit application support, Xvfb, openbox, xdotool, scrot, and Python 3. Visual review is still required. |

Run the standard suite from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Run an exact-ZIP suite with all available installer runtimes selected explicitly:

```sh
PWSH=/path/to/pwsh HTMOD_RELEASE_ZIP=/path/to/HardTruckModern-0.3.0.zip \
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

## Validate and build

1. Add or change a manifest-owned component and, for a binary component, its statically registered handler.
2. Run focused tests and update golden hashes only when a payload change is intentional.
3. Validate and list the release:

   ```sh
   python3 tools/htmod.py validate
   python3 tools/htmod.py list
   ```

4. Run the committed suite with the required fixtures from the matrix.
5. Build the frozen patcher, then build twice into separate empty directories with that same executable and compare every file:

   ```sh
   python3 tools/build_windows_patcher.py --work-dir /tmp/htmod-patcher-build \
     --output /tmp/htmod-build-htmod.exe
   python3 tools/build_windows_release.py --patcher-exe /tmp/htmod-build-htmod.exe \
     --output /tmp/htmod-build-a
   python3 tools/build_windows_release.py --patcher-exe /tmp/htmod-build-htmod.exe \
     --output /tmp/htmod-build-b
   diff -r /tmp/htmod-build-a /tmp/htmod-build-b
   ```

The patcher build recreates its dedicated Wine prefix and installs hash-locked requirements. The release builder uses pinned pristine v1.2 `prism3d.exe`, `game.dll`, and `sys/dx8.dll` only for verification; override their locations with `--prism3d-exe`, `--game-dll`, and `--dx8-dll` when necessary.

Schema-3 output contains `htmod.exe`, `migrations.json`, data, and expected hashes, but no original or localized game executable or DLL payloads. Installers accept canonical v1.2 and official Estonian v1.2 sources. Estonian binaries normalize locally to canonical v1.2 before selected mods. Unknown binaries are refused.

A custom `--migrations` catalog must contain exactly all three canonical targets, pristine lengths and hashes matching the build inputs, and every required current, released legacy, official Estonian, DX8, and prism3d stock/cap source state. Use the `migration-add` command in [AUTHORING.md](AUTHORING.md) to author individual source records without copying executables, DLLs, or full binary deltas.

6. Package the `HardTruckModern` directory, fresh-extract that exact ZIP, and run the complete suite against it. Check ZIP integrity and forbidden files, including `__pycache__`, Python bytecode, research, test prefixes, logs, saves, backups, and other non-release content. Never publish merely because an in-tree build or only the PowerShell suite passed.

## Installer compatibility rules

`tools/templates/Setup.ps1` consumes generated schema-3 component metadata and invokes the bundled frozen patcher before creating backups. Do not duplicate labels, dependencies, conflicts, data ownership, component IDs, variant-selection rules, or Quick Setup text generation in PowerShell. Staged executables and DLLs then enter the existing atomic copy jobs. The frozen `render-quick-setup --output-dir ... --mod ...` operation uses the shared SDK renderer to stage `game/usr/ui/cosetup.sc`; Setup copies that file and records its actual hash for the selected plan.

Preserve schema-1 backup compatibility, exact incoming backup bytes, rollback, uninstall records, save and unrelated-file guards, and automatic detection. `tools/templates/INSTALL.sh` consumes generated `install-manifest.tsv`, invokes the same executable through Wine and `winepath` before its backup transaction, and uses the same rendered Quick Setup file and actual hash in `installation.tsv`. It retains per-run backups plus reverse-order rollback on ordinary mid-install failure. Retain manual recovery instructions for catastrophic interruption and do not add a second payload mapping. Windows `Setup.bat` remains unchanged.

Native Windows execution without Python, PowerShell 5.1 installer and staging behavior, SmartScreen and antivirus response, WinForms DPI behavior where applicable, and gameplay require separate verification on native Windows. Linux, Wine, and PowerShell 7 results do not establish those native Windows results.

## Live Wine smokes

Install `config`, plus `camera` for the camera smoke or `ui_keyboard` for accessibility and Credits smokes, into the disposable test copy with the generated release Setup first. Set `uset r_mode` in that copy's `game/usr/config.cfg` to match `--resolution`.

The camera smoke also requires these test-copy-only bindings, which are not shipped config defaults:

```text
bind_imp y "mleft"
bind_imp o "mright"
bind_imp i "mlookup"
bind_imp k "mlookdown"
```

Use a fresh dedicated prefix and an unused display for each run:

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

Each script writes screenshots, logs, and `summary.json`. The harness starts only its own Xvfb, openbox, and Wine processes, records their PIDs, terminates only those PIDs, and scopes `wineserver -k` to the dedicated prefix. It never uses name-based cleanup. Owned-process liveness and an unused display are checked before interaction. Reusing a caller prefix can shut down its other Wine processes, so a dedicated prefix is mandatory.

The camera smoke captures stock cabin views, vertical-input cases, orbit direction and reset, external truck views, and cameras 1 through 7. It locates the parked player through the validated `game.dll` data, reads process memory without writing it, and fails if the 64-byte physical transform changes. Linux ptrace permissions must allow this read-only inspection. This supplements rather than replaces visual review.

For accessibility, inspect both 1024x768 and 1920x1080: main Tab and Shift+Tab focus, Enter opening Options, larger text and targets, `>` focus and `[x]` selection on option pages, the dark Quick panel, and company to owner to OK focus. The smoke types only into the disposable copy. The Quick Setup flow never presses OK because that action is known to crash in the headless flow.

The Credits smoke captures all five timed pages. Run it at both supported test resolutions and visually inspect persistent Hard Truck Modern and MOD SDK lines, stock text and final thanks, and absence of clipping or overlap. Adjust `--page-wait` if a capture lands during a transition. The log gate requires `ui/mcredits.sc` to load but does not require GAME state.

Process and log checks do not prove visual correctness. The accessibility smoke does not test generic Space activation, screen readers, reduced motion, runtime scaling, generated binding rows, device-dependent joystick controls, native pointer or edge activation, or native Windows. Wine/Xvfb mouse activation and post-console deadlocks can still prevent a run. Report those limitations rather than implying passes.

## Crash-report verification

`CrashReport.bat` forwards to `Setup.ps1 -Action CrashReport`, also available through the Setup menu. Manual reporting asks for confirmation and does not intentionally crash the game.

Reports contain only sanitized engine crash text, at most the last 4000 engine log lines, `report.json`, and `README.txt`. They exclude saves, profiles, backup payloads, memory dumps, registry exports, dxdiag/MSInfo, and arbitrary documents. Metadata redacts usernames, computer names, paths, and IP addresses. There is no automatic upload.

`tests/test_crash_reporting.py` covers real ZIPs, paths with spaces, missing or corrupt logs, unpatched games, Desktop fallback, metadata, line limits, privacy, and batch/menu compatibility. Use fake executables for these tests; never intentionally crash a real game for a demonstration.
