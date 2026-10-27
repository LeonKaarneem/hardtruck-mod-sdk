# Hard Truck Mod SDK and Modern Modpack

**Hard Truck Mod SDK** is an unofficial modding toolkit and quality-of-life modpack for **18 Wheels of Steel: Hard Truck v1.2** by SCS Software. It provides reusable mod-development tools and modern gameplay improvements, including truck physics and handling fixes, an ETS-style chase camera, traffic AI cleanup, economy improvements, keyboard-accessible menus, and privacy-conscious crash reports.

Players can install the finished Hard Truck mod without Python. Mod authors can use the SDK's manifests, PE patching, migration support, deterministic builds, and test harnesses to create their own Hard Truck mods.

You must own a legal copy of the game. The modpack does not include the game, and the original game remains the property of its respective owners.

## Windows installation

1. Extract `HardTruckModern-v3.zip` completely and close the game.
2. Run `Setup.bat` in the extracted folder.
3. Choose **Install all** for the complete modpack, or **Choose components** to answer a prompt for each component.
4. Setup checks Steam libraries, common Program Files/GOG locations, and previous installations. Confirm a detected folder, select one from the numbered list, or manually enter the folder containing `prism3d.exe`.
5. Keep the extracted `installer` folder so backups, crash reports, and uninstall support remain available.

**Start a new profile after installing handling, economy, or driver definition changes, including Install all.** Existing saves are incompatible with those definitions. Setup preserves save files but cannot migrate them; back up `game/usr/save/` first.

Players do **not** need Python. The package contains no original game or DX8 DLL payloads. The bundled local `htmod.exe` patcher recognizes the installed files and stages the selected variants on your computer before Setup backs up and installs them. Unknown `game.dll` or wrapped `sys/dx8.dll` files are refused. Restore the stock DX8 DLL before setup if you use d3d8to9 or another wrapper.

## Components

| Component | Effect | New profile? |
|---|---|---|
| Configuration | Console, controls, display defaults, menu presentation, and in-game mod credits | No |
| Handling definitions | Quicker steering, stronger brakes, and torque recovery | Yes |
| Economy definitions | Higher cargo pay and reduced damage penalties | Yes |
| Driver definitions | Lower AI-driver delay chance, salary, and share demand | Yes |
| Camera | Mouse-orbit chase camera with the stock cabin camera | No |
| Physics | Fewer shoulder/tunnel-edge dead stops and corrected trailer mass | No |
| AI | Traffic despawn cleanup and reduced log spam | No |
| Keyboard navigation | Tab focus, Enter activation, and keyboard-adjusted sliders | No |
| DX8 vsync | Native vsync for the stock DirectX 8 renderer | No |

## Controls and display

- `~` opens the console.
- `1` selects the stock cabin camera; `2` selects the mouse-orbit chase camera; `3`–`7` retain their stock camera roles.
- `F6` teleports to service, `F7` to gas, and `F8` to a weigh station.
- `F9` toggles the debug camera, `F10` toggles tickets, and `F12` takes a screenshot.
- Tab and Shift+Tab move through native menu controls, Enter activates the focused control, and Left/Right adjusts focused sliders.

**Cap FPS at 60 or lower** to avoid the original high-frame-rate physics bugs. Use either d3d8to9 or the native DX8 vsync component, not both. Widescreen uses Vert-; use a 4:3 resolution with GPU aspect-ratio scaling for the original field of view.

## Manual crash reports

After a failure, run `CrashReport.bat`, or run `Setup.bat` and choose **Create crash report** (menu option 4). Confirm the prompt to create `HardTruckCrashReport-<timestamp>.zip` on your Desktop, with the extracted package folder as a fallback. The tool does not intentionally crash the game and does not monitor or launch it.

The report contains only sanitized `prism3d.crash` and the last 4000 lines of sanitized `prism3d.log` when available, plus `report.json` and `README.txt`. Metadata includes executable/DLL hashes, component state and versions, basic OS/GPU details, and log-presence flags. Usernames, computer names, paths, and IP addresses are redacted. Saves, profiles, backups, memory dumps, registry exports, dxdiag/MSInfo output, and arbitrary documents are excluded.

Nothing is uploaded automatically. Review the ZIP and decide yourself whether and where to share it.

## Uninstall

Close the game, run `Setup.bat`, and choose **Uninstall** (menu option 3). Setup restores backed-up files and removes installer-created files when they still match the installed payloads. Files edited after installation are reported and skipped rather than overwritten. Saves and unrelated files are preserved.

## Linux / Wine

Use a 64-bit Wine prefix for the bundled patcher, extract the release ZIP, and run:

```sh
./INSTALL.sh "/path/to/18 Wheels of Steel Hard Truck"
```

Use `./INSTALL.sh --yes "/path/to/game"` for unattended installation. The script installs all components and requires POSIX shell utilities, `sha256sum`, and Wine with `winepath`; Python and PowerShell are not required. It stages recognized DLL variants locally, validates payloads, and stores originals with `installation.tsv` under `installer/backups/shell-*`. Start a new profile after installation.

The Linux path has no component selection or automatic uninstaller. Ordinary installation failures roll back earlier changes. After a catastrophic interruption, restore manually from the retained `installation.tsv` and `files/` backups.

For Wine, the OpenGL renderer with `uset r_device "gl"` and `uset gl_sync "1"` in `game/usr/config.cfg` provides a useful 60 FPS cap.

## Credits

**MOD SDK: LeonKaarneem**

The in-game Credits retain the original SCS Software credits and include the Hard Truck Modern and MOD SDK attribution. See `modpack/README.md` for component details and `sdk/README.md` for authoring and release documentation.
