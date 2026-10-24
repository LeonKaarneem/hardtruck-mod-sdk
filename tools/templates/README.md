# Extract ZIP -> run Setup.bat -> Install all

Close the game, run `Setup.bat`, choose **Install all**, and enter the game folder containing `prism3d.exe`. No Python installation is needed.

**Start a new profile after Install all.** Its definition changes make existing saves incompatible. Setup preserves save files but cannot make old saves load.

To install only some mods, choose **Choose components** and answer each prompt. Selecting handling, economy, or driver definitions also requires a new profile. **Uninstall** restores backed-up files when they have not been edited since installation.

The installer supports pristine Hard Truck v1.2 and its own installed variants. Restore the stock `sys/dx8.dll` before setup if you use d3d8to9 or another wrapper. Keep the extracted `installer` folder: it contains the payloads and backups.
