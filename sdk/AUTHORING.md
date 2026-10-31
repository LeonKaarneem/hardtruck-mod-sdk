# Authoring data mods

## Quickstart: add a visible Credits line

This example installs one data override directly into a disposable copy of your legally owned Hard Truck v1.2 game. It needs Python 3, but it does not need a release build, PowerShell, a frozen patcher, or original game files from the repository.

The example uses its own modpack root. This is required because the shipped `config` component already owns `game/usr/ui/mcredits.sc`, and a release cannot have two owners for one destination.

1. Choose an empty directory outside the repository and create the component layout:

   ```sh
   QUICKSTART=/path/to/credits-quickstart
   mkdir -p "$QUICKSTART/mods/credits_note/files/ui"
   ```

2. Copy the stock `ui/mcredits.sc` from your own game to `$QUICKSTART/mods/credits_note/files/ui/mcredits.sc`. If the game has no loose stock file, extract `ui/mcredits.sc` from its `game/patch.zipfs`. Use `game/base.zipfs` only when the patch archive does not contain that file. Do not start with an empty UI file and do not copy the modified file from `modpack/mods/config`.

3. Open the copied file in an editor that preserves its original encoding and CRLF line endings. In the group with `id:"3000"` and `name:"tab1"`, insert this block immediately after `name:"tab1"`:

   ```text
   			spawn txt_common {
   				look:"22"
   				value:"MY FIRST DATA MOD"
   				area:"306.000000 680.000000 718.000000 712.000000"
   				coords:"306.000000 680.000000 718.000000 712.000000"
   			}
   ```

   Keep every stock block unchanged. This adds text near the bottom of the first Credits page.

4. Create `$QUICKSTART/mods/credits_note/mod.json`:

   ```json
   {
     "schema_version": 1,
     "id": "credits_note",
     "version": "1.0.0",
     "kind": "data",
     "setup": {
       "label": "Credits note",
       "description": "Adds a visible line to the first Credits page",
       "default": false
     },
     "requires": [],
     "conflicts": [],
     "new_profile": false,
     "files": [
       {"source": "ui/mcredits.sc", "destination": "game/usr/ui/mcredits.sc"}
     ]
   }
   ```

5. Create `$QUICKSTART/release.json`:

   ```json
   {
     "schema_version": 1,
     "mods": ["credits_note"],
     "game_variants": {}
   }
   ```

6. From the repository root, validate the component and inspect a disposable game copy. Set `GAME` to the directory containing `prism3d.exe`:

   ```sh
   GAME="/path/to/disposable game"
   python3 tools/htmod.py --root "$QUICKSTART" validate
   python3 tools/htmod.py --root "$QUICKSTART" status \
     --game-dir "$GAME" --migrations modpack/migrations.json
   python3 tools/htmod.py --root "$QUICKSTART" plan \
     --game-dir "$GAME" --migrations modpack/migrations.json --mod credits_note
   ```

   `status` and `plan` are read-only. The plan should describe one data write for `game/usr/ui/mcredits.sc`.

7. Apply the plan, then inspect it again:

   ```sh
   python3 tools/htmod.py --root "$QUICKSTART" apply \
     --game-dir "$GAME" --migrations modpack/migrations.json --mod credits_note
   python3 tools/htmod.py --root "$QUICKSTART" plan \
     --game-dir "$GAME" --migrations modpack/migrations.json --mod credits_note
   ```

   Successful apply prints one JSON result with `"status":"installed"`. The second plan is a no-op. Start the game, open Credits, and look for **MY FIRST DATA MOD** on the first page. The stock credits remain present.

The game mounts loose files under `game/usr` above the packed archives. The installed `game/usr/ui/mcredits.sc` therefore overrides the stock `ui/mcredits.sc`; the modpack source directory is not a game mount.

This text-only change has `new_profile: false` and does not require a new profile. Definition changes that alter save compatibility must use `new_profile: true`; players should start a new profile because old saves cannot be migrated by Setup.

### Remove or restore the quickstart

Choose the removal path that matches the desired result. For exact incoming-byte restoration, including restoration of a loose `mcredits.sc` that existed before apply, run whole-history removal directly after apply:

```sh
python3 tools/htmod.py --root "$QUICKSTART" remove \
  --game-dir "$GAME" --migrations modpack/migrations.json --all
```

This is the recommended restoration path for the quickstart. Whole removal replays recognized installation records and exact backups. Use it only when you intend to remove all recognized installation history on that game copy.

Selective removal is the alternative when no pre-existing loose file needs restoration and the goal is only to delete the installed override:

```sh
python3 tools/htmod.py --root "$QUICKSTART" remove \
  --game-dir "$GAME" --migrations modpack/migrations.json \
  --mod credits_note --dry-run
python3 tools/htmod.py --root "$QUICKSTART" remove \
  --game-dir "$GAME" --migrations modpack/migrations.json --mod credits_note
```

Selective removal deletes the override only when its bytes still match the installed payload. The packed stock Credits file becomes visible again. A later edit is preserved and reported as `skipped_modified` with a partial result. Selective data removal does not restore a pre-existing loose file. Do not run it before whole-history removal when exact incoming-byte restoration is required; use direct whole-history removal immediately after apply instead.

## Data component rules

The source of truth is `modpack/mods/<id>/mod.json`, files under that component's `files/` directory, and the ordered `mods` list in `modpack/release.json`. Every destination must remain under `game/usr` and have exactly one component owner, compared case-insensitively.

Saves, music, logs, bytecode, unsafe paths, Windows reserved or device names, colons, trailing dots or spaces, Windows-special characters, and control characters are rejected. Dependencies and conflicts refer to other component IDs. The only current data transform is `strip_r_renderer`, used by `config`.

When adding a data component to the main modpack, check existing ownership first. In particular, move or extend the existing `config` ownership deliberately instead of adding a second `mcredits.sc` destination record.

## Supported public API boundary

The supported author-facing interfaces are:

- the documented component, release, and migration JSON formats;
- `tools/htmod.py` commands documented in these guides;
- `ManifestError`, `PatchError`, `Release`, `Selection`, and `load_release` exported by `htmod`;
- `generate_splices`, `normalize_to_pristine`, and `load_catalog` in `htmod.migration` for migration tooling;
- the binary classes and `PEImage` methods listed in [BINARY_MODDING.md](BINARY_MODDING.md);
- static handler and transform registration in `sdk/htmod/registry.py`.

Other modules and generated installer records are internal implementation details. JSON never names a Python module path, and no plugin directory is scanned. The SDK uses only the Python standard library at author runtime.

## Lifecycle commands

`status`, `plan`, `apply`, and `remove` require a migration catalog and emit one schema-1 JSON object on success. `recover` needs only the release root and game directory. Errors return 1 with no partial JSON. An executed removal or recovery that preserves modified files returns a partial result and exit status 1. A valid `remove --dry-run` only prints its declarative plan and returns 0, including when that plan lists modified files that an executed removal would skip.

The default plan is additive: it retains exact current binary components and matching data, then adds requested components and requirements. `--exact` starts from an empty selection and permits full-target replacement from recognized legacy or migration sources. It never deletes unselected data. Missing or foreign binary sources cannot be reconstructed. A data-only additive plan leaves unknown binaries untouched.

For the config-owned `game/usr/ui/cosetup.sc`, `plan.selected` determines the enabled-component text and expected hash. Status derives that selection from exact current binary membership and matching data before recognizing the rendered file. Apply materializes the same selection-specific bytes; legacy generic text and text for the wrong selection are modified rather than matching. Plans, status, and results remain declarative and never contain payload bytes.

Lifecycle apply validates final hashes, locks the game, records exact incoming backups and a journal under `<game>/.htmod/backups/`, and rolls back ordinary failures in reverse order. The per-game lock uses nonblocking POSIX `flock` or Windows byte-range locking and is released automatically on process death. `transaction.lock` is safe persistent metadata, not proof that a transaction is live, so recovery can proceed after a crash while live transactions remain mutually exclusive. Selective binary removal rebuilds an exact retained curated variant. Whole removal restores exact incoming bytes from recognized lifecycle, schema-1 JSON, or shell TSV records. Once current content already matches the final replayed baseline across all generations, repeated whole removal is a no-op without a new backup generation. Recovery rolls back an interrupted transaction without creating another generation:

```sh
python3 tools/htmod.py recover --game-dir "/path/to/test game"
python3 tools/htmod.py recover --game-dir "/path/to/test game" \
  --transaction-id TRANSACTION_ID
```

Neither the POSIX nor Windows-compatible mutation path can provide an atomic compare-and-rename against an arbitrary hostile writer. Rollback and recovery use hash guards so later content is not overwritten merely to complete recovery.

## Author a migration source

Use `migration-add` to add one known source DLL to an existing schema-1 catalog. Both the new source and the canonical pristine reference come from your legally owned files:

```sh
python3 tools/htmod.py migration-add \
  --target game/sys/game.dll \
  --source /path/to/source-game.dll \
  --pristine /path/to/pristine-game.dll \
  --migrations /path/to/migrations.json \
  --output /path/to/migrations.json
```

For DX8, use `--target sys/dx8.dll` and the matching source and pristine DLLs. `--pristine` is required because a schema-1 catalog contains hashes and splice records, not the pristine DLL bytes needed to author a new mapping.

The command validates the existing catalog, target, regular input files, pristine length and hash, and output containment. It generates only a `length` and `changes` record, then proves that normalization returns the exact pristine bytes. It refuses full-image deltas and results over the existing 512 KiB catalog budget. No DLL or separate binary delta is copied.

Output is deterministic, sorted, two-space JSON with a trailing newline and is replaced atomically. An identical existing record returns `"status":"no_op"` without creating or replacing output. A conflicting duplicate, unknown target, unsafe path, corrupt input, normalization failure, or oversized result returns 1 without changing the source, input catalog, or existing output. Success returns one JSON object with `schema_version`, `status`, `target`, and `source_sha256`.

Run focused migration checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_migration_add tests.test_migration -v
```

See [RELEASING.md](RELEASING.md) for the complete test-prerequisite matrix and package verification.
