# Hard Truck: 18 Wheels of Steel - Modern QoL Mod Pack v3

A quality-of-life bug-fix mod for **18 Wheels of Steel: Hard Truck v1.2** (SCS Software,
2002, Prism3D engine). Data files use the game's own override mechanism - any file
placed in `game/usr/<path>` replaces the archived copy of `<path>` - so no archives
are repacked and uninstalling is just deleting files. Optional binary patches
(cameras, physics, AI cleanup, dx8 vsync) are applied with the included Python
scripts; every script has `--check`/`--restore` and restores byte-identically. All
game.dll patches compose with each other (verified).

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
| `game/usr/defs/truck_base.def` | `steer_speed` 30→40 deg/s, `brake` 120000→160000, `torque_coef` 0.9→1.35 |
| `game/usr/defs/truck1.def` … `truck8.def` | Every truck steers at 40 deg/s; weak brakes buffed +33% (truck2: 66666→88888, truck8: 30000→40000); `torque_coef` ×1.5 per truck (0.45→0.675 … 3.2→4.8) - recovers the acceleration the 1.2 patch broke |
| `game/usr/defs/driver_base.def` | AI drivers: delay chance 0.0017→0.0005, salary demand 400→300, share demand 10%→7.5% |
| `game/usr/defs/management/base.def` | Cargo pay `cval` +40%, damage penalty `cpen` −40% (to 60%). Built on the 1.2 patch file, so its localization fixes stay intact |
| `patches/patch_camera_mods.py` | **Cameras:** chase cam (key 2) becomes an ETS-style mouse-orbit camera; cab view gets vertical look (±35°) |
| `patches/patch_physics_fixes.py` | **Physics:** kills the "invisible wall" dead-stop feel, removes the velocity snap, fixes the trailer-mass unit bug (÷3→÷5) |
| `patches/patch_ai_fixes.py` | **AI:** fixes the ui_ai_car despawn bug behind the ~9000× "removing me ?? who ?" log spam and menu-traffic pool leak |
| `patches/patch_dx8_vsync.py` | Optional: byte-patches `sys/dx8.dll` for native vsync |

## The three classic complaints, and what this mod does about them

### 1. "Invisible wall" - truck stops dead at nothing

There are **two** bugs with the same symptom; this mod fixes the fixable one:

- **Fixed - the material governor + velocity snap.** Any wheel touching non-road
  material (shoulders, tunnel edges, weigh-station aprons - exactly where players
  report walls) multiplied rolling resistance by up to 400×, and a separate
  snap-to-zero zeroed all velocity whenever net force fell below a threshold.
  Patched: governor 400→40 (a strong off-road penalty remains, but clipping a
  shoulder slows you instead of slamming you), snap threshold 10000→100 (truck
  creeps at force equilibrium instead of dead-stopping). A/B-verified headless:
  stock truck stops dead at the spawn gantry shoulder; patched truck keeps rolling.
- **Not fixable - bad collision at fixed map spots.** A handful of locations
  (Phoenix–Las Vegas bridge area, some weigh-station entrances, Denver–SLC sector
  joins) have broken collision geometry baked into the map's binary `.btr` trees.
  No tool exists to edit those; save before known spots and drive around. Community
  reports agree these existed on 2002 hardware.
- **Already covered - high-FPS physics glitches.** The "stuck on terrain" variant
  happens above ~60 FPS; see the FPS cap notes below.

### 2. Acceleration way too slow with cargo

Three stacked causes, all addressed:

- **Unit bug (code):** the physics converts truck mass with kg÷5 but trailer+cargo
  with kg÷3 - every kilogram of load counted 1.67×. Patched to ÷5 in `game.dll`.
- **The 1.2 regression (data):** first-hand A/B tests (1.0 disc vs Steam 1.2) show
  patch 1.2 made effective torque ~2.5× weaker. `torque_coef` is raised ×1.5 across
  the fleet (on top of the unit fix this lands close to 1.0 feel without going
  arcade). A/B-verified: the stock truck barely leaves the gantry in 8 s; patched it
  clears it in ~1–2 s.
- **`thorse_power` is display-only** (marketing stat for the truck list - the real
  power curve keys off `torque_coef` and rpm), so def edits target the right value.

### 3. AI traffic suddenly stopping

Honest status - the deep RE found three mechanisms; one fixed, two documented:

- **Fixed - ui_ai_car desync bug:** the menu/demo traffic pool used an error stub
  instead of `remove_me` for one virtual (the source of the ~9000×
  `*** ERROR *** removing me ?? who ?` per session and cars never deactivating).
  The vtable slot is repointed to the real cleanup. Verified: spam structurally
  impossible after patch; no new errors across multiple 10-minute soaks.
- **Known, not fixed - junction gridlock leak:** junction occupancy counters are
  only decremented while a car drives through; distance-despawn leaks them, so
  eventually a junction jams permanently (cars wait at green). A safe code patch
  needs a provable "does this car hold the counter" test that the static analysis
  could not establish (all candidate flags go stale after normal exit - patching
  blindly would double-count and make cars run red lights). Needs live debugging
  with real input; mechanism fully documented in `research/ai-notes.md`.
- **Known, not fixed - full-stop flag freeze:** a hard-stopped AI car sets a flag
  that nothing ever clears; unless its (engine-side) controller restarts it, it
  stays frozen. Also documented.

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

Then apply the binary patches you want (all optional, all reversible, order doesn't
matter, they compose):

```
python patches\patch_camera_mods.py  "C:\Games\Hard Truck 18 WoS\game\sys\game.dll"
python patches\patch_physics_fixes.py "C:\Games\Hard Truck 18 WoS\game\sys\game.dll"
python patches\patch_ai_fixes.py     "C:\Games\Hard Truck 18 WoS\game\sys\game.dll"
```

Requires Python 3. After copying, edit `game/usr/config.cfg` if your native
resolution isn't 1920x1080 (`uset r_mode "WIDTHxHEIGHTx32"`).

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
- Revert binary patches with each script's `--restore` (game.dll cameras/physics/AI,
  dx8.dll vsync).
- If the game fails to start after editing, check `game/usr/config.cfg` for typos and
  `prism3d.log` in the game folder for def parse errors.

## Repository layout

```
modpack/
  game/               data overrides (copy into game folder)
  patches/            patch_camera_mods.py, patch_physics_fixes.py,
                      patch_ai_fixes.py, patch_dx8_vsync.py
  src/htd8/           reference d3d8 COM proxy from the FOV attempt (not needed to play)
research/
  camera-notes.md     camera RE + verification evidence
  physics-notes.md    vehicle physics RE + v3 fixes evidence
  ai-notes.md         traffic AI RE + v3 fixes + known-unfixed mechanisms
  fov-notes.md        FOV/diGuard RE + the 3-round Hor+ proof chain
specs/                original v1 design doc
```
