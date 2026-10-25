# Extract ZIP -> run Setup.bat -> Install all

Close the game, run `Setup.bat`, and choose **Install all**. Setup automatically checks Steam libraries, common Program Files/GOG locations, and previous installations. Confirm the detected folder or choose from the numbered list. If no installation is found, enter the folder containing `prism3d.exe` manually. No Python installation is needed. The package ships no original game/DX8 DLLs: the included frozen `htmod.exe` patches recognized local inputs before Setup backs up and installs them.

**Start a new profile after Install all.** Its definition changes make existing saves incompatible. Setup preserves save files but cannot make old saves load.

To install only some mods, choose **Choose components** and answer each prompt. Selecting handling, economy, or driver definitions also requires a new profile. **Uninstall** restores backed-up files when they have not been edited since installation.

The installer supports pristine Hard Truck v1.2, current installed variants, and upgrades from previously released modpack variants. The frozen patcher stages the selected current variant locally, then Setup retains an exact backup of the incoming files. Unknown `game.dll` files are refused before backup. Restore the stock `sys/dx8.dll` before setup if you use d3d8to9 or another wrapper. Keep the extracted `installer` folder: it contains the patcher, migration catalog, data payloads, and backups. Native Windows no-Python staging and PowerShell 5.1 behavior require native Windows verification.

## Play Modded and crash reports

Run **`Play Modded.bat`** to review the **Hard Truck Modern v3** dialog and **MOD SDK: LeonKaarneem** credit. **Play**/Enter launches the game; **Cancel**, Escape, or closing the window starts no game and creates no report. Setup menu option 5 and noninteractive automation retain their direct launch behavior.

After launch, normal exit creates nothing and does not prompt. A nonzero exit or newly updated `prism3d.crash` offers a default-Yes crash report; answering No creates no archive. For a manual report, run **`CrashReport.bat`** or choose Setup menu option 6 and confirm. These actions do not intentionally crash the game. Native Windows dialog appearance and DPI rendering remain unverified from Linux.

The timestamped `HardTruckCrashReport-*.zip` is saved on your Desktop, falling back to the extracted package folder if needed. It contains only sanitized `prism3d.crash` and the last 4000 lines of sanitized `prism3d.log` when those files exist, plus `report.json` and `README.txt`. Metadata records the report schema/UTC time, Play launch and exit details when available, hashes of the game executable and game/DX8 DLLs, payload schema, installed component IDs/statuses/versions, basic OS/GPU details, and log-presence flags.

Username, computer name, home/game/package paths, and IPv4/IPv6 addresses are replaced with placeholders in every ZIP entry. No saves, profiles, backup payloads, memory dumps, registry exports, dxdiag/MSInfo output, or arbitrary documents are included. **Reports are never uploaded automatically**: review the archive and decide yourself whether and where to share it.

## Linux / Wine

Run `./INSTALL.sh "/path/to/game"` with the folder containing `prism3d.exe`. This installs **all** components using POSIX shell utilities, `sha256sum`, and Wine with `winepath`; Python and PowerShell are not required. The script invokes the same frozen patcher as Windows to normalize recognized inputs and stage the all-components variant locally, validates data payloads and staged hashes before recording changes, and refuses unknown DLLs. It rejects symlink destinations and existing non-regular destinations, verifies installed files, and saves originals plus `installation.tsv` under `installer/backups/shell-*`. Use `--yes` before the game path for unattended installation. Ordinary mid-install failures roll back earlier files from those backups. After a crash, power loss, or `kill -9`, manual recovery may be necessary: use the retained `installation.tsv` and `files/` copies. There is no component selection or automatic shell uninstaller. Start a new profile after installation.

## Controls and display

`~` opens the console; `2` selects the mouse-orbit chase camera. F6 teleports to service, F7 to gas, F8 to a weigh station, F9 toggles the debug camera, F10 toggles tickets, and F12 takes a screenshot. Tab and Shift+Tab move through native menu controls, Enter activates the focused control, and Left/Right adjusts focused sliders.

With Configuration and Keyboard navigation installed, the main menu has larger text and targets. Options Graphics/Sound/Controls selectors have larger text, `>` keyboard-focus cues, and `[x]` selected-page cues without enlarged targets. The Set Up Company screen has a dark native **Quick Setup** panel identifying the unofficial mod, summarizing the improvements and installed-feature choices, plus a larger OK target. The stock company fields remain in place; dense option rows and HUD text are unchanged. The main menu shows **Hard Truck Modern v3** at lower right. All five Credits pages retain their stock content and add persistent **Hard Truck Modern** and **MOD SDK: LeonKaarneem** lines below it.

Key 1 retains the stock cabin camera: left/right look behaves as in the unmodified game, and vertical-look inputs do not modify the cabin view. Key 2 retains the modified mouse-orbit chase camera.

**Cap FPS at 60 or lower** to avoid high-frame-rate physics bugs. Use either d3d8to9 or the native DX8 vsync patch, not both: they replace the same `sys/dx8.dll` and are incompatible.

Widescreen uses Vert- (a cropped vertical view). For the original field of view, use a 4:3 resolution with GPU aspect-ratio scaling for pillarbox bars.

## Limitations and troubleshooting

Physics fixes do not repair broken fixed map collision at some bridges, weigh stations, and sector joins. Traffic cleanup also does not eliminate all AI stops; some junction and stopped-traffic freezes remain.

Space is not generic menu activation. There is no generic screen-reader support, reduced-motion mode, or runtime text scaling. Dynamic binding rows are not verified keyboard-navigable, and device-dependent joystick controls need real-hardware testing. Native Windows and native pointer/edge activation remain unverified; accessibility acceptance ran under Wine/Xvfb.

If the game fails to start or a mod does not load, check `prism3d.log` in the game folder for launch and definition errors. Confirm Setup selected the folder containing `prism3d.exe`, then close the game before retrying.
