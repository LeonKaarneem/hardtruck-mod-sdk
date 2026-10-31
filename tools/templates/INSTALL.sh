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

INSTALL_GAME=
INSTALL_GAME_HASH=
INSTALL_DX8=
INSTALL_DX8_HASH=
INSTALL_MODS=
INSTALL_MODS_COUNT=0
PATCHER=
CATALOG=
DATA_COUNT=0

while IFS= read -r line; do
    case $line in
        install-mods"$TAB"*"$TAB""$TAB"*|install-mods"$TAB"*"$TAB")
            echo "error: malformed install-mods selection." >&2
            exit 1
            ;;
    esac
done < "$MANIFEST"

while IFS="$TAB" read -r kind relative expected; do
    [ -n "$kind" ] || continue
    case $kind in
        variant|legacy-game|stock-dx8)
            continue
            ;;
        install-game)
            INSTALL_GAME=$relative
            INSTALL_GAME_HASH=$expected
            continue
            ;;
        install-dx8)
            INSTALL_DX8=$relative
            INSTALL_DX8_HASH=$expected
            continue
            ;;
        install-mods)
            INSTALL_MODS_COUNT=$((INSTALL_MODS_COUNT + 1))
            if [ "$INSTALL_MODS_COUNT" -ne 1 ] || [ "$relative" != "-" ] || [ -z "$expected" ]; then
                echo "error: install-mods requires exactly one nonempty internal selection." >&2
                exit 1
            fi
            case $expected in
                *"$TAB"*)
                    echo "error: malformed install-mods selection." >&2
                    exit 1
                    ;;
            esac
            INSTALL_MODS=$expected
            continue
            ;;
    esac
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
        patcher)
            PATCHER=$source
            ;;
        migrations)
            CATALOG=$source
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

if [ -z "$INSTALL_GAME" ] || [ -z "$INSTALL_DX8" ] || [ -z "$PATCHER" ] || [ -z "$CATALOG" ] || [ "$DATA_COUNT" -eq 0 ]; then
    echo "error: incomplete generated install manifest." >&2
    exit 1
fi
if [ "$INSTALL_MODS_COUNT" -ne 1 ] || [ -z "$INSTALL_MODS" ]; then
    echo "error: install-mods requires exactly one nonempty internal selection." >&2
    exit 1
fi
set -f
set --
for MOD_ID in $INSTALL_MODS; do
    case $MOD_ID in
        ''|[0-9_]*|*[!a-z0-9_]*)
            echo "error: malformed install-mods component ID: $MOD_ID" >&2
            exit 1
            ;;
    esac
    set -- "$@" --mod "$MOD_ID"
done
set +f
if [ "$#" -eq 0 ]; then
    echo "error: install-mods requires exactly one nonempty internal selection." >&2
    exit 1
fi
if ! command -v wine >/dev/null 2>&1 || ! command -v winepath >/dev/null 2>&1; then
    echo "error: wine and winepath are required for local DLL staging." >&2
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

STAGING=$PACKAGE/installer/.staging-$$
if [ -e "$STAGING" ]; then
    echo "error: staging path already exists: $STAGING" >&2
    exit 1
fi
printf 'Staging selected binary components locally; no original DLL payloads are shipped.\n'
if ! wine "$PATCHER" stage \
    --game-dir "$(winepath -w "$GAME")" \
    --output-dir "$(winepath -w "$STAGING")" \
    --migrations "$(winepath -w "$CATALOG")" \
    "$@" \
    --expect "game/sys/game.dll=$INSTALL_GAME_HASH" \
    --expect "sys/dx8.dll=$INSTALL_DX8_HASH"; then
    rm -rf "$STAGING"
    echo "error: frozen patcher staging failed; no files were changed." >&2
    exit 1
fi
if [ "$(hash_file "$STAGING/game/sys/game.dll")" != "$INSTALL_GAME_HASH" ] || \
        [ "$(hash_file "$STAGING/sys/dx8.dll")" != "$INSTALL_DX8_HASH" ]; then
    rm -rf "$STAGING"
    echo "error: staged DLL hash verification failed; no files were changed." >&2
    exit 1
fi
if ! wine "$PATCHER" render-quick-setup \
    --output-dir "$(winepath -w "$STAGING")" \
    "$@"; then
    rm -rf "$STAGING"
    echo "error: frozen Quick Setup rendering failed; no files were changed." >&2
    exit 1
fi
QUICK_SETUP=$STAGING/game/usr/ui/cosetup.sc
if [ ! -f "$QUICK_SETUP" ]; then
    rm -rf "$STAGING"
    echo "error: frozen Quick Setup rendering did not produce ui/cosetup.sc." >&2
    exit 1
fi
QUICK_SETUP_HASH=$(hash_file "$QUICK_SETUP")

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
    rm -rf "$STAGING"
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
            install_file "$STAGING/game/sys/game.dll" "$GAME/game/sys/game.dll" "game/sys/game.dll" "$expected"
            ;;
        install-dx8)
            install_file "$STAGING/sys/dx8.dll" "$GAME/sys/dx8.dll" "sys/dx8.dll" "$expected"
            ;;
        data)
            data_relative=${relative#data/}
            if [ "$relative" = "data/ui/cosetup.sc" ]; then
                install_file "$QUICK_SETUP" "$GAME/game/usr/$data_relative" "game/usr/$data_relative" "$QUICK_SETUP_HASH"
            else
                install_file "$PAYLOAD/$relative" "$GAME/game/usr/$data_relative" "game/usr/$data_relative" "$expected"
            fi
            ;;
    esac
done < "$MANIFEST"

while IFS="$TAB" read -r kind relative expected; do
    case $kind in
        install-game) destination=$GAME/game/sys/game.dll ;;
        install-dx8) destination=$GAME/sys/dx8.dll ;;
        data)
            destination=$GAME/game/usr/${relative#data/}
            if [ "$relative" = "data/ui/cosetup.sc" ]; then
                expected=$QUICK_SETUP_HASH
            fi
            ;;
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
