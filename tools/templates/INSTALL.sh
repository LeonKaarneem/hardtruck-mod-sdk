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
    if [ "$kind" = "legacy-game" ]; then
        if [ "$GAME_HASH" = "$expected" ]; then GAME_RECOGNIZED=1; fi
        continue
    fi
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

while IFS="$TAB" read -r kind relative expected; do
    case $kind in
        install-game) destination=$GAME/game/sys/game.dll ;;
        install-dx8) destination=$GAME/sys/dx8.dll ;;
        data) destination=$GAME/game/usr/${relative#data/} ;;
        *) continue ;;
    esac
    if [ -L "$destination" ]; then
        echo "error: destination is a symlink; refusing to replace it: $destination" >&2
        exit 1
    fi
    if [ -e "$destination" ] && [ ! -f "$destination" ]; then
        echo "error: destination exists but is not a regular file: $destination" >&2
        exit 1
    fi
done < "$MANIFEST"

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
INSTALL_SUCCESS=0

rollback() {
    printf 'Install failed; rolling back using %s\n' "$BACKUP" >&2
    rollback_errors=0
    reverse=$BACKUP/.rollback-order
    if ! awk '{ lines[NR] = $0 } END { for (i = NR; i >= 1; i--) print lines[i] }' "$ENTRIES" > "$reverse"; then
        rollback_errors=1
        : > "$reverse"
    fi
    while IFS="$TAB" read -r action relative original installed backup_relative; do
        destination=$GAME/$relative
        current=
        if [ -f "$destination" ]; then
            current=$(hash_file "$destination") || current=
        fi
        case $action in
            created)
                if [ -n "$current" ] && [ "$current" != "$installed" ]; then
                    printf 'Rollback preserving externally changed file: %s\n' "$destination" >&2
                    rollback_errors=1
                elif [ -f "$destination" ] && ! rm -f "$destination"; then
                    rollback_errors=1
                fi
                ;;
            replaced)
                backup_file=$BACKUP/$backup_relative
                if [ "$current" = "$original" ]; then
                    continue
                fi
                if [ -n "$current" ] && [ "$current" != "$installed" ]; then
                    printf 'Rollback preserving externally changed file: %s\n' "$destination" >&2
                    rollback_errors=1
                    continue
                fi
                if [ ! -f "$backup_file" ]; then
                    printf 'Rollback backup missing: %s\n' "$backup_file" >&2
                    rollback_errors=1
                    continue
                fi
                restore_temporary=$destination.htmod-rollback-$$
                rm -f "$restore_temporary"
                if ! mkdir -p "$(dirname "$destination")" || ! cp -p "$backup_file" "$restore_temporary" || ! mv -f "$restore_temporary" "$destination"; then
                    rollback_errors=1
                    continue
                fi
                restored=$(hash_file "$destination") || restored=
                if [ "$restored" != "$original" ]; then
                    printf 'Rollback hash mismatch: %s\n' "$destination" >&2
                    rollback_errors=1
                fi
                ;;
        esac
    done < "$reverse"
    rm -f "$reverse"
    if [ "$rollback_errors" -ne 0 ]; then
        printf 'Rollback incomplete; manual recovery required from %s and installation.tsv.\n' "$BACKUP" >&2
        return 1
    fi
    printf 'Rollback complete; recovery manifest retained: %s\n' "$BACKUP" >&2
}

on_exit() {
    exit_status=$?
    trap - EXIT
    if [ "$INSTALL_SUCCESS" -ne 1 ]; then
        set +e
        rollback
        if [ "$?" -ne 0 ] || [ "$exit_status" -eq 0 ]; then
            exit_status=1
        fi
    fi
    exit "$exit_status"
}

trap on_exit EXIT
trap 'exit 1' HUP INT TERM

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

INSTALL_SUCCESS=1
printf 'Install all complete. Backup: %s\n' "$BACKUP"
printf 'Start a NEW profile: old saves are incompatible with the definition changes.\n'
