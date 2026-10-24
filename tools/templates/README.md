# Extract ZIP -> run Setup.bat -> Install all

Close the game, run `Setup.bat`, and choose **Install all**. Setup automatically checks Steam libraries, common Program Files/GOG locations, and previous installations. Confirm the detected folder or choose from the numbered list. If no installation is found, enter the folder containing `prism3d.exe` manually. No Python installation is needed.

**Start a new profile after Install all.** Its definition changes make existing saves incompatible. Setup preserves save files but cannot make old saves load.

To install only some mods, choose **Choose components** and answer each prompt. Selecting handling, economy, or driver definitions also requires a new profile. **Uninstall** restores backed-up files when they have not been edited since installation.

The installer supports pristine Hard Truck v1.2 and its own installed variants. Restore the stock `sys/dx8.dll` before setup if you use d3d8to9 or another wrapper. Keep the extracted `installer` folder: it contains the payloads and backups.

## Linux / Wine

Run `./INSTALL.sh "/path/to/game"` with the folder containing `prism3d.exe`. This installs **all** components using only POSIX shell utilities and `sha256sum`; Python and PowerShell are not required. The script validates all prebuilt payloads and current game/DX8 hashes, verifies installed files, and saves originals plus `installation.tsv` under `installer/backups/shell-*`. Use `--yes` before the game path for unattended installation. There is no component selection or automatic shell uninstaller; retain the backup for manual restoration. Start a new profile after installation.

## Controls and display

`~` opens the console; `2` selects the mouse-orbit chase camera. F6 teleports to service, F7 to gas, F8 to a weigh station, F9 toggles the debug camera, F10 toggles tickets, and F12 takes a screenshot.

With Configuration installed, the main menu shows **Hard Truck Modern v3** at lower right. The Set Up Company screen also has a native **Quick Setup** panel identifying the unofficial mod, summarizing the physics/camera/handling and economy/AI improvements, and noting that installed features depend on Setup choices. It leaves the stock company fields and OK control in place.

Cabin yaw/pitch no longer bank the scene, and upward/downward look clamps correctly at ±35°, including negative/downward input.

**Cap FPS at 60 or lower** to avoid high-frame-rate physics bugs. Use either d3d8to9 or the native DX8 vsync patch, not both: they replace the same `sys/dx8.dll` and are incompatible.

Widescreen uses Vert- (a cropped vertical view). For the original field of view, use a 4:3 resolution with GPU aspect-ratio scaling for pillarbox bars.

## Limitations and troubleshooting

Physics fixes do not repair broken fixed map collision at some bridges, weigh stations, and sector joins. Traffic cleanup also does not eliminate all AI stops; some junction and stopped-traffic freezes remain.

If the game fails to start or a mod does not load, check `prism3d.log` in the game folder for launch and definition errors. Confirm Setup selected the folder containing `prism3d.exe`, then close the game before retrying.
