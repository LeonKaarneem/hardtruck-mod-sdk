#!/bin/sh
# Hard Truck 18 WoS QoL modpack installer (Linux/macOS helper).
# Copies the contents of game/ over your game folder.
#
# Usage: ./INSTALL.sh "/path/to/18 Wheels of Steel Hard Truck"
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)/game"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <path-to-game-folder>   (the folder containing prism3d.exe)"
    exit 1
fi

DEST="$1"
if [ ! -d "$DEST" ] || [ ! -f "$DEST/prism3d.exe" ]; then
    echo "error: '$DEST' does not look like the game folder (prism3d.exe not found)."
    exit 1
fi
if [ ! -d "$DEST/game" ]; then
    echo "error: '$DEST/game' not found - wrong folder?"
    exit 1
fi

echo "This will copy the modpack's game/ contents into:"
echo "  $DEST/game"
echo
echo "BACKUP FIRST: your existing savegames will NOT load with this mod."
echo "Copy your '$DEST/game/usr/save' folder somewhere safe, or start a new profile."
echo
printf "Continue? [y/N] "
read -r REPLY
case "$REPLY" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
esac

cp -rv "$SRC_DIR/." "$DEST/game/"
echo
echo "Done. Next:"
echo "  1. Edit $DEST/game/usr/config.cfg (resolution, r_device)."
echo "  2. Start the game and create a NEW profile."
echo "Linux/Wine: prefer uset r_device \"gl\" + uset gl_sync \"1\" for a 60 FPS cap."
