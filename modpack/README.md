# Hard Truck: 18 Wheels of Steel - Modern v3

A quality-of-life mod for **18 Wheels of Steel: Hard Truck v1.2** (SCS Software, 2002). The supported player package is `HardTruckModern-v3.zip`: it installs manifest-owned data and registered binary components with backups and uninstall support, without requiring Python.

> **Start a new profile after installing definition changes.** Existing saves are version-checked against those definitions and will not load. Setup preserves save files but cannot migrate them; back up `game/usr/save/` first.

## Install on Windows

1. Extract `HardTruckModern-v3.zip` completely.
2. Close the game and run `Setup.bat` in the extracted folder.
3. Choose **Install all**, or **Choose components** for a custom install.
4. Setup checks Steam libraries, common Program Files/GOG locations, and previous installations. Confirm a detected folder, choose from the numbered list, or enter the folder containing `prism3d.exe` manually.
5. Start a **new profile** if you installed handling, economy, or driver definitions.

Keep the extracted `installer` folder so Setup can retain backups and uninstall later. To restore the pre-install state, run `Setup.bat` and choose **Uninstall**. Files changed after installation are reported and skipped rather than overwritten. Saves and unrelated files are preserved.

Setup accepts pristine v1.2 and its own known binary variants. It refuses unknown `game.dll` or wrapped `sys/dx8.dll` files instead of overwriting them. Restore the stock DX8 DLL before setup if you use d3d8to9.

## Components

| Stable ID | Effect | New profile? |
|---|---|---|
| `config` | Console and controls, 1920×1080 default, main-menu mod marker, Quick Setup information | No |
| `handling_defs` | Quicker steering, stronger brakes, torque recovery across the fleet | Yes |
| `economy_def` | Cargo pay +40%; damage penalty reduced to 60% | Yes |
| `driver_def` | Lower AI-driver delay chance, salary, and share demand | Yes |
| `camera` | Mouse-orbit chase camera (key 2); cabin vertical look clamped at ±35° | No |
| `physics` | Reduces shoulder/tunnel-edge dead stops, velocity snap, and trailer-mass error | No |
| `ai` | Fixes menu/demo traffic despawn cleanup and its log spam/pool leak | No |
| `dx8_vsync` | Native DX8 vsync for the stock renderer | No |

With Configuration installed, the main menu shows **Hard Truck Modern v3** at lower right. The native Set Up Company screen also identifies the unofficial mod, summarizes improvements, and notes that features depend on Setup choices without moving stock controls.

## Controls and display

Press `~` to open the console. Key 2 selects the mouse-orbit chase camera; keys 1 and 3–7 retain their stock camera roles. F6 teleports to service, F7 to gas, F8 to a weigh station, F9 toggles the debug camera, F10 toggles tickets, and F12 takes a screenshot.

**Cap FPS at 60 or lower** to avoid the original high-frame-rate physics bugs. On Windows 10/11, either:

- use [d3d8to9](https://github.com/crosire/d3d8to9) with your driver's vsync, restoring stock `sys/dx8.dll` before running Setup; or
- select the native `dx8_vsync` component without a wrapper.

Do not use both: they replace the same DLL. The launcher offers only resolutions up to 1024×768; run `prism3d.exe` directly or edit `uset r_mode "WIDTHxHEIGHTx32"` in `game/usr/config.cfg`.

Widescreen is Vert- (cropped vertically). For the original field of view, use a 4:3 resolution with GPU aspect-ratio scaling for pillarbox bars.

## Linux / Wine

Use a 32-bit Wine prefix and install the game normally. Generate the release on a machine with the author prerequisites, then run its PowerShell installer where available, or perform binary changes through the SDK on a disposable copy and materialize data through a release build. There is no supported raw `modpack/game` directory to copy.

For playing under Wine, prefer these settings in `game/usr/config.cfg`:

```text
uset r_device "gl"
uset gl_sync "1"
```

The OpenGL renderer provides a useful 60 FPS cap under Wine. DX8 also works through WineD3D. Keep FPS at 60 or lower and start a new profile after definition changes.

## Known limitations

- Physics fixes do not repair broken collision geometry at some bridges, weigh stations, and sector joins. Save before known trouble spots.
- AI cleanup does not fix every traffic stop: junction-counter gridlock and some stopped-car freezes remain.
- Definition changes require a new profile. There is no save migration path.
- The Hor+ FOV attempt remains excluded experimental research. Renderer integrity checks and the engine's software-transformed vertex path prevented a safe patch. See `research/fov-notes.md`; do not install `patch_fov_horplus.py` or the reference `src/htd8/` proxy as release mods.
- If startup or a mod fails, inspect `prism3d.log` and confirm Setup selected the folder containing `prism3d.exe`.

## Authors and advanced recovery

All active additions use manifests and the static SDK registry. The retired raw-copy `INSTALL.bat`/`INSTALL.sh` workflow and standalone camera/physics/AI/DX8 patchers have been removed; there is no second active implementation to drift from the release.

See **`sdk/README.md`** for data mods, operand patches, code hooks, dependencies/conflicts, tests, live smokes, and release packaging. Registered binary components can be inspected or reversed on a backed-up test game:

```sh
python3 tools/htmod.py check --game-dir "/path/to/game" --mod camera --mod physics --mod ai
python3 tools/htmod.py restore --game-dir "/path/to/game" --mod camera --mod physics --mod ai
python3 tools/htmod.py restore --game-dir "/path/to/game" --mod dx8_vsync
```

Use Setup's **Uninstall** for a normal installation because it also restores manifest-owned data and backup history.

## Repository layout

```text
modpack/
  release.json          ordered components and curated game variants
  mods/<id>/mod.json    component metadata and ownership
  mods/<id>/files/      manifest-owned data sources
  mods/<id>/patch.py    registered binary implementations
  patches/              excluded experimental FOV research only
  src/htd8/             excluded reference proxy from that research
sdk/                    SDK library and author guide
tests/                  unit, golden, installer, and live-smoke harnesses
tools/htmod.py          author CLI
tools/build_windows_release.py
research/               historical RE notes and evidence
```
