# Hard Truck: 18 Wheels of Steel - Modern QoL Mod Pack v2

A quality-of-life mod for **18 Wheels of Steel: Hard Truck v1.2** (SCS Software,
2002, Prism3D engine). Data files use the game's own override mechanism - any file
placed in `game/usr/<path>` replaces the archived copy of `<path>` - so no archives
are repacked and uninstalling is just deleting files. Optional binary patches
(game.dll cameras, dx8.dll vsync) are applied with the included Python scripts and
can be reverted byte-identically.

> ## ⚠ START A NEW PROFILE - YOUR OLD SAVES WILL NOT LOAD
>
> Savegames are version-checked against data derived from the `.def` files. This mod
> changes those defs, so **existing `game/usr/save/*.sav` files will be rejected or
> corrupted**. There is no migration path: finish your current run before installing,
> then create a new driver profile. This cannot be stressed enough - back up your
> `game/usr/save/` folder first.

## What's included

| Component | Effect |
|---|---|
| `game/usr/config.cfg` | Console enabled, QoL keybinds (see table), 1920x1080 default, optional-extras comments |
| `game/usr/defs/truck_base.def` | Fleet-wide: `steer_speed` 30→40 deg/s, `brake` 120000→160000 (+33%, same ratio the official 1.2 patch used) |
| `game/usr/defs/truck1.def` … `truck8.def` | Every truck steers at 40 deg/s; weak brakes buffed +33% (truck2: 66666→88888, truck8: 30000→40000). Heavier trucks' stronger brakes kept |
| `game/usr/defs/driver_base.def` | AI drivers: delay chance 0.0017→0.0005, salary demand 400→300, share demand 10%→7.5% |
| `game/usr/defs/management/base.def` | Cargo pay `cval` +40%, damage penalty `cpen` −40% (to 60%). Built on the 1.2 patch file, so its localization fixes stay intact |
| `patches/patch_camera_mods.py` | **Camera mod (recommended):** chase cam (key 2) becomes an ETS-style mouse-orbit camera; cab view gets vertical look (±35°). Patches `game/sys/game.dll` |
| `patches/patch_dx8_vsync.py` | Optional: byte-patches `sys/dx8.dll` for native vsync |

### Camera mod - what changes

- **Chase camera (press 2)**: orbit the truck with the mouse - left/right swings the
  camera around the truck (ETS-style), up/down pitches it. The camera starts level
  behind the truck every time you press 2.
- **Cab camera**: mouse up/down now looks up/down (about ±35°, matched to the cab
  geometry); left/right look still works as before.
- Verified on Wine (see `research/camera-notes.md` for the full evidence chain);
  byte-identical `--restore` is tested on every release of the patch.

Apply it with:

```
python patches\patch_camera_mods.py "C:\Games\Hard Truck 18 WoS\game\sys\game.dll"
```

(`--check` reports status, `--restore` reverts to the pristine DLL.)

## Install

**Copy the contents of `game/` into your game folder** (the one containing
`prism3d.exe`), keeping the folder structure. Windows example:

```
xcopy /e /i "modpack\game" "C:\Games\Hard Truck 18 WoS\game"
```

or just drag `usr` into `…\game\` and merge. Linux/Wine example (see below for prefix setup):

```
cp -r modpack/game/* "$WINEPREFIX/drive_c/Games/18 Wheels of Steel Hard Truck/game/"
```

A `INSTALL.bat` (Windows) and `INSTALL.sh` (Linux/macOS) are provided that do this for a
target directory you pass, with a confirmation prompt. They only copy files.

After copying, edit `game/usr/config.cfg` if your native resolution isn't 1920x1080
(`uset r_mode "WIDTHxHEIGHTx32"`), and run the camera patch script above if wanted.

## Windows 10/11

The stock dx8 renderer is flaky on modern Windows. Two options:

1. **Recommended - d3d8to9**: download `d3d8.dll` from
   <https://github.com/crosire/d3d8to9> (releases page). The game loads its renderer
   from `sys/dx8.dll`, so: rename `sys\dx8.dll` to `sys\dx8.dll.bak`, then place the
   downloaded `d3d8.dll` in `sys\` and rename it to `dx8.dll`. This wraps Direct3D 8 to
   9 and fixes most Windows 10/11 rendering issues. To revert, delete the wrapper and
   rename the `.bak` back.
2. **Native vsync patch** (optional, without d3d8to9 - works on plain Win32):

   ```
   python patches\patch_dx8_vsync.py "C:\Games\Hard Truck 18 WoS\sys\dx8.dll"
   ```

   The script flips `D3DPRESENT_INTERVAL_IMMEDIATE` → `D3DPRESENT_INTERVAL_ONE`
   (a single byte) so Present waits for vblank. `--check` reports status,
   `--restore` undoes it. Requires Python 3 (from python.org or the Microsoft Store).
   On Linux/macOS the same script works with `python3`.

Notes:

- **The launcher caps resolution at 1024x768.** Either skip the launcher and run
  `prism3d.exe` directly, or close the game after the launcher and edit
  `game/usr/config.cfg` (`uset r_mode "1920x1080x32"`) - the engine enumerates real
  modes and anything it lists works.
- **Widescreen FOV (read this before asking):** on 16:9 the engine *vertically*
  crops the view (fixed horizontal FOV). A proper Hor+ fix was attempted
  exhaustively and is **impossible for this engine build**: the projection matrix
  only exists inside the renderer modules, which are protected by a runtime
  integrity check (dlguard) that crashes the game if a single code byte changes,
  and the engine software-transforms its vertices, so no wrapper (d3d8to9,
  dgVoodoo2, or a Wine-side proxy - all tested/designed) ever sees the projection.
  Full three-round proof chain in `research/fov-notes.md`. The practical options
  are: play 16:9 Vert- (slightly zoomed), or force 4:3 (`uset r_mode "1440x1080x32"`
  etc.) with your GPU scaling set to "preserve aspect ratio" (pillarboxed, correct
  FOV).
- **Cap your FPS at ~60.** There is a physics bug at high frame rates (truck can get
  stuck on terrain). Use the vsync patch, d3d8to9 with your driver's vsync, or an
  external limiter.

## Linux / Wine

Recipe (tested style, any recent distro):

```sh
# 32-bit prefix (the game is a 32-bit PE)
export WINEPREFIX="$HOME/.wine-ht18"
wineboot --init            # say yes if it offers to install Mono/Gecko
winetricks -q vcrun6       # MSVC6 runtimes, harmless if already present

# install the modpack (copy game/ contents over the game folder)
cp -r modpack/game/* "$WINEPREFIX/drive_c/18 Wheels of Steel Hard Truck/game/"

# edit resolution + renderer: set in game/usr/config.cfg
#   uset r_mode "1920x1080x32"
#   uset r_device "gl"
#   uset gl_sync "1"      <- vsync = stable 60 FPS cap (wine honors swap interval)
wine "$WINEPREFIX/drive_c/18 Wheels of Steel Hard Truck/prism3d.exe" -game game
```

- Prefer `r_device "gl"` + `gl_sync "1"`: the OpenGL renderer exposes vsync directly
  and Wine honors it, giving a clean 60 FPS cap (the dx8 path goes through WineD3D,
  which cannot vsync). dx8 does work via WineD3D if you prefer it.
- **Keep FPS ≤ 60** - same physics bug as on Windows (see above).
- Widescreen FOV: same Vert- situation as Windows (see the note above - no fix is
  possible; `src/htd8/` contains a complete, working Wine d3d8 COM proxy built
  during the attempt, kept as reference).
- The Linux vsync byte patch (`patch_dx8_vsync.py`) is pointless under Wine - use
  `gl_sync` instead.
- Headless/automated testing notes (Xvfb + xdotool, console-driven: `map west`,
  `ui t cosetup`) are in `research/` - useful if you want to script the game.

## Keybinds

Press **`~`** in game to open the console (enabled by this mod: `g_console 1`).

| Key | Action |
|---|---|
| 2 | Chase camera - **mouse-orbit around the truck** (with camera patch) |
| 3 / 4 / 5 … | Other stock cameras (unchanged) |
| F6 | Teleport to service station (debug impulse) |
| F7 | Teleport to gas station |
| F8 | Teleport to weigh station |
| F9 | Debug camera toggle |
| F10 | Toggle speed/overweight tickets on/off (`cheat noticket`) |
| F12 | Screenshot (stock) |

## Console cheat commands

With the console open (`~`), type `cheat` plus one of:

- `cheat noticket` - toggle speed/overweight tickets
- `cheat money <value>` - add money
- `cheat skill <value>` - set skill
- `cheat contracts flow skill [bonus]` - spawn contract offers
- `cheat goto7 <x> <y> <z>` - teleport
- `cheat dmeet` - meet all drivers
- `cheat truck [1..6]` - switch truck
- `cheat tspeed [float]` - management time-scale modifier

## Jukebox

Drop `.ogg` or `.wav` files into `game/usr/music/jukebox/` and press `j` in game.
**Filenames must be 45 characters or fewer.**

## Uninstall / troubleshooting

- Remove the files you copied into `game/usr/` (or the whole `game/usr/` folder - the
  game regenerates it) to return to stock behavior. Keep your `save/` backup.
- Revert binary patches with each script's `--restore` (game.dll cameras, dx8.dll
  vsync).
- If the game fails to start after editing, check `game/usr/config.cfg` for typos and
  `prism3d.log` in the game folder for def parse errors.

## Repository layout

```
modpack/
  game/               data overrides (copy into game folder)
  patches/            patch_camera_mods.py, patch_dx8_vsync.py
  src/htd8/           reference d3d8 COM proxy from the FOV attempt (not needed to play)
research/
  camera-notes.md     camera RE + full verification evidence
  fov-notes.md        FOV/diGuard RE + the 3-round Hor+ proof chain
specs/                original v1 design doc
```
