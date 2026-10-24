#!/bin/sh
set -eu

if [ "${1:-}" = "--yes" ]; then
    ASSUME_YES=1
    shift
else
    ASSUME_YES=0
fi
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 [--yes] <game folder containing prism3d.exe>" >&2
    exit 2
fi

GAME=$1
PACKAGE=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
PAYLOAD=$PACKAGE/installer/payload
MANIFEST=$PAYLOAD/install-manifest.tsv
TAB=$(printf '\t')

if ! command -v sha256sum >/dev/null 2>&1; then
    echo "error: sha256sum is required." >&2
    exit 1
fi
for required in "$GAME/prism3d.exe" "$GAME/game/sys/game.dll" "$GAME/sys/dx8.dll" "$MANIFEST"; do
    if [ ! -f "$required" ]; then
        echo "error: required file not found: $required" >&2
        exit 1
    fi
done

hash_file() {
    set -- $(sha256sum "$1")
    printf '%s\n' "$1"
}

GAME_HASH=$(hash_file "$GAME/game/sys/game.dll")
DX8_HASH=$(hash_file "$GAME/sys/dx8.dll")
GAME_RECOGNIZED=0
DX8_RECOGNIZED=0
INSTALL_GAME=
INSTALL_GAME_HASH=
INSTALL_DX8=
INSTALL_DX8_HASH=
DATA_COUNT=0

while IFS="$TAB" read -r kind relative expected; do
    [ -n "$kind" ] || continue
    source=$PAYLOAD/$relative
    if [ ! -f "$source" ]; then
        echo "error: payload missing: $source" >&2
        exit 1
    fi
    actual=$(hash_file "$source")
    if [ "$actual" != "$expected" ]; then
        echo "error: corrupt payload SHA-256: $source" >&2
        exit 1
    fi
    case $kind in
        variant)
            if [ "$GAME_HASH" = "$expected" ]; then GAME_RECOGNIZED=1; fi
            ;;
        install-game)
            INSTALL_GAME=$relative
            INSTALL_GAME_HASH=$expected
            ;;
        install-dx8)
            INSTALL_DX8=$relative
            INSTALL_DX8_HASH=$expected
            if [ "$DX8_HASH" = "$expected" ]; then DX8_RECOGNIZED=1; fi
            ;;
        stock-dx8)
            if [ "$DX8_HASH" = "$expected" ]; then DX8_RECOGNIZED=1; fi
            ;;
        data)
            DATA_COUNT=$((DATA_COUNT + 1))
            ;;
        *)
            echo "error: unknown install manifest record: $kind" >&2
            exit 1
            ;;
    esac
done < "$MANIFEST"

if [ "$GAME_RECOGNIZED" -ne 1 ]; then
    echo "error: unknown or unsupported game.dll; restore pristine v1.2 or a recognized installed variant." >&2
    exit 1
fi
if [ "$DX8_RECOGNIZED" -ne 1 ]; then
    echo "error: unknown, wrapped, or unsupported dx8.dll; restore the stock v1.2 DLL." >&2
    exit 1
fi
if [ -z "$INSTALL_GAME" ] || [ -z "$INSTALL_DX8" ] || [ "$DATA_COUNT" -eq 0 ]; then
    echo "error: incomplete generated install manifest." >&2
    exit 1
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Install ALL Hard Truck Modern components into %s? Definition changes require a NEW profile. [y/N] ' "$GAME"
    read -r REPLY
    case $REPLY in
        y|Y|yes|YES) ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

BACKUP=$PACKAGE/installer/backups/shell-$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$BACKUP/files"
ENTRIES=$BACKUP/installation.tsv
: > "$ENTRIES"

install_file() {
    source=$1
    destination=$2
    relative=$3
    expected=$4
    if [ -f "$destination" ]; then
        current=$(hash_file "$destination")
        if [ "$current" = "$expected" ]; then
            printf 'Already correct: %s\n' "$relative"
            return
        fi
        backup_file=$BACKUP/files/$relative
        mkdir -p "$(dirname "$backup_file")"
        cp -p "$destination" "$backup_file"
        printf 'replaced\t%s\t%s\t%s\tfiles/%s\n' "$relative" "$current" "$expected" "$relative" >> "$ENTRIES"
    else
        printf 'created\t%s\t-\t%s\t-\n' "$relative" "$expected" >> "$ENTRIES"
    fi
    mkdir -p "$(dirname "$destination")"
    temporary=$destination.htmod-shell-$$
    rm -f "$temporary"
    cp "$source" "$temporary"
    actual=$(hash_file "$temporary")
    if [ "$actual" != "$expected" ]; then
        rm -f "$temporary"
        echo "error: staged copy SHA-256 mismatch: $destination" >&2
        exit 1
    fi
    mv -f "$temporary" "$destination"
    printf 'Installed: %s\n' "$relative"
}

while IFS="$TAB" read -r kind relative expected; do
    case $kind in
        install-game)
            install_file "$PAYLOAD/$relative" "$GAME/game/sys/game.dll" "game/sys/game.dll" "$expected"
            ;;
        install-dx8)
            install_file "$PAYLOAD/$relative" "$GAME/sys/dx8.dll" "sys/dx8.dll" "$expected"
            ;;
        data)
            data_relative=${relative#data/}
            install_file "$PAYLOAD/$relative" "$GAME/game/usr/$data_relative" "game/usr/$data_relative" "$expected"
            ;;
    esac
done < "$MANIFEST"

while IFS="$TAB" read -r kind relative expected; do
    case $kind in
        install-game) destination=$GAME/game/sys/game.dll ;;
        install-dx8) destination=$GAME/sys/dx8.dll ;;
        data) destination=$GAME/game/usr/${relative#data/} ;;
        *) continue ;;
    esac
    actual=$(hash_file "$destination")
    if [ "$actual" != "$expected" ]; then
        echo "error: post-install SHA-256 mismatch: $destination" >&2
        exit 1
    fi
done < "$MANIFEST"

printf 'Install all complete. Backup: %s\n' "$BACKUP"
printf 'Start a NEW profile: old saves are incompatible with the definition changes.\n'
