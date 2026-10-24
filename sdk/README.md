# Hard Truck Mod SDK

The SDK is the only active authoring and release path for Hard Truck Modern. Players do not need Python: they extract the release ZIP and run `Setup.bat`. Authors use Python 3 and the repository's standard-library-only SDK.

The source of truth is:

- `modpack/mods/<id>/mod.json` - one component's metadata and owned data or registered binary handler.
- `modpack/mods/<id>/files/` - data source files.
- `modpack/mods/<id>/patch.py` - binary implementation, exposing `MOD`.
- `modpack/release.json` - ordered release components and explicitly curated game variants.
- `sdk/htmod/manifest.py` and `sdk/htmod/runner.py` - static handler allowlists. JSON never names a Python path and no plugin directory is scanned.

Do not add a payload copy map, standalone patcher, or release-only special case. Experimental FOV code under `modpack/patches/patch_fov_horplus.py` and `modpack/src/htd8/` is excluded research, not an SDK component and not a safe release feature.

## Author workflow

1. Add or change a manifest-owned mod and, for a binary mod, its statically registered handler.
2. Add focused tests and update golden hashes only when a payload change is intentional.
3. Validate and list the release:
   ```sh
   python3 tools/htmod.py validate
   python3 tools/htmod.py list
   ```
4. Run the committed suite from the repository root:
   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
   ```
   Installer runtime tests need PowerShell 7 on the test machine. Set `PWSH=/path/to/pwsh` when it is not on `PATH`; without PowerShell those tests skip explicitly. Pinned binary fixtures also need the repository's normal `test-base` input or `HTMOD_TEST_BASE`.
5. Optionally run a committed live smoke on a disposable installed game copy (see below).
6. Build twice into separate empty directories and compare every file:
   ```sh
   python3 tools/build_windows_release.py --output /tmp/htmod-build-a
   python3 tools/build_windows_release.py --output /tmp/htmod-build-b
   diff -r /tmp/htmod-build-a /tmp/htmod-build-b
   ```
   The builder uses pinned pristine v1.2 `game.dll` and `sys/dx8.dll`; override their locations with `--game-dll` and `--dx8-dll` when necessary.
7. Package the `HardTruckModern` directory, fresh-extract that exact ZIP, and run the installer suite against it before publishing:
   ```sh
   PWSH=/path/to/pwsh HTMOD_RELEASE_ZIP=/path/to/HardTruckModern-v3.zip \
     python3 -m unittest discover -s tests -p 'test_installer_runtime.py' -v
   ```
   Check ZIP integrity and forbidden files (`__pycache__`, Python bytecode, research, test prefixes, logs, saves, backups, and other non-release content). Never publish merely because an in-tree build passed.

## Example: data mod

Create `modpack/mods/example_data/files/ui/example.sc`, then `modpack/mods/example_data/mod.json`:

```json
{
  "schema_version": 1,
  "id": "example_data",
  "version": "1.0.0",
  "kind": "data",
  "setup": {
    "label": "Example data",
    "description": "Example UI override",
    "default": false
  },
  "requires": ["config"],
  "conflicts": [],
  "new_profile": false,
  "files": [
    {"source": "ui/example.sc", "destination": "game/usr/ui/example.sc"}
  ]
}
```

Add `example_data` to `modpack/release.json`'s ordered `mods` list. Destinations must stay under `game/usr`; saves, music, logs, bytecode, unsafe paths, and case-insensitive duplicate ownership are rejected. The only current transform is `strip_r_renderer`, used by `config`. A definition-changing mod should set `new_profile` to `true` so Setup warns about incompatible old saves.

## Example: anchored operand patch

Use `BytePatch` for a small replacement and anchors for the surrounding known bytes. Resolve RVAs through `PEImage` instead of assuming file offsets. A minimal handler has this shape:

```python
from htmod.errors import SignatureError
from htmod.patch import Anchor, BinaryMod, BytePatch


class ExampleOperandMod(BinaryMod):
    def __init__(self):
        super().__init__("example_operand", "game/sys/game.dll", ())

    def patches_for(self, image):
        site = image.rva_to_offset(0x12340, 4)
        context = image.rva_to_offset(0x12338, 8)
        if site is None or context is None:
            raise SignatureError("example patch site is not mapped")
        return (BytePatch(
            "example operand",
            site,
            bytes.fromhex("34120000"),
            bytes.fromhex("78560000"),
            (Anchor(context, bytes.fromhex("9090909090909090")),),
        ),)


MOD = ExampleOperandMod()
```

The addresses and bytes above are illustrative, not a patch for the game. For a real mod, add a unique context signature as in `modpack/mods/physics/patch.py`, pin the pristine source SHA-256, and test ORIGINAL, PATCHED, FOREIGN, apply/check/restore, and composition. Ambiguous or foreign input must be refused without mutation.

For a bounded immediate change whose original and patched instruction can occur at the same site, follow `modpack/mods/dx8_vsync/patch.py`: bound the search to one function and require exactly one complete state.

## Example: code hook

A code hook is a stateful `BinaryMod`, not an arbitrary script run by the builder. `modpack/mods/camera/patch.py` is the complete reference for:

1. finding and validating inline hook sites;
2. generating helper code and relative `call`/`jmp` operands;
3. appending an owned section with `PEImage.append_section`;
4. recording imported absolute addresses with `append_highlow_relocations`;
5. inspecting the complete original/patched state and reversing with `remove_highlow_relocations` and `remove_section`.

A relative branch helper is only this small part of the implementation:

```python
import struct


def jump(source_va, destination_va):
    displacement = destination_va - (source_va + 5)
    return b"\xe9" + struct.pack("<i", displacement)
```

The handler must also verify section flags/layout, relocation ownership, padding, file length, resume addresses, and foreign/trailing data. `PatchRunner` applies selected mods to in-memory copies and restores in reverse order; its file commit verifies preflight and post-write bytes and retains recovery backups after incomplete rollback. Do not write directly to a game DLL from a handler.

## Register a binary mod

Using `example_operand` as the ID:

1. Put the implementation in `modpack/mods/example_operand/patch.py` and expose `MOD` with matching `id` and `target`.
2. Add `example_operand` to both `BINARY_HANDLERS` in `sdk/htmod/manifest.py` and `REGISTERED_BINARY_HANDLERS` in `sdk/htmod/runner.py`.
3. Create its binary `mod.json` with `handler: "example_operand"`, a target such as `game/sys/game.dll`, and the pinned pristine `source_sha256`.
4. Add the ID to `modpack/release.json`. Dependencies and conflicts reference other registered IDs:
   ```json
   "requires": ["config"],
   "conflicts": ["example_other"]
   ```
   `Selection.validate` rejects missing selections and conflicts before file changes; generated schema-2 installer metadata carries the same rules to Setup.
5. For a `game.dll` component, deliberately extend the curated variant list and `GAME_COMPONENTS` in `tools/build_windows_release.py`. Preserve existing `game-000` through `game-111` names and bit meanings; never silently generate an exponential matrix. Other new targets require an explicit builder payload path driven by the validated release metadata.

## Check, apply, and restore on a test game

Back up and use a disposable v1.2 game folder. `--game-dir` is the folder containing `prism3d.exe`; repeat `--mod` in the intended order:

```sh
python3 tools/htmod.py check --game-dir "/path/to/test game" \
  --mod camera --mod physics --mod ai
python3 tools/htmod.py apply --game-dir "/path/to/test game" \
  --mod camera --mod physics --mod ai
python3 tools/htmod.py restore --game-dir "/path/to/test game" \
  --mod camera --mod physics --mod ai
python3 tools/htmod.py check --game-dir "/path/to/test game" --mod dx8_vsync
```

These commands handle registered binary mods only. Data is materialized into the release by manifests; end users install both data and binary components through `Setup.bat`.

## Live Wine smokes

Prerequisites: Wine with 32-bit application support, Xvfb, openbox, xdotool, scrot, Python 3, and a disposable full game copy. Use a fresh/dedicated prefix and an unused display for each run. The harness starts only its own Xvfb/openbox/Wine processes, records their PIDs in `owned-pids`, terminates only those PIDs, and scopes `wineserver -k` to the caller's dedicated prefix. It never uses `pkill` or name-based cleanup.

Install `config` (and `camera` for the camera smoke) into the test copy with the generated release Setup first. Set `uset r_mode` in that copy's `game/usr/config.cfg` to match `--resolution`. The camera smoke also requires these **test-copy-only** bindings; they are not added to the shipped config, and the harness refuses to run without them:

```text
bind_imp y "mleft"
bind_imp o "mright"
bind_imp i "mlookup"
bind_imp k "mlookdown"
```

Then run, for example:

```sh
python3 tests/live/quick_setup.py \
  --game "/tmp/htmod-live/game" --prefix "/tmp/htmod-live/prefix-ui" \
  --output "/tmp/htmod-live/ui-1024" --display :121 --resolution 1024x768

python3 tests/live/camera.py \
  --game "/tmp/htmod-live/game" --prefix "/tmp/htmod-live/prefix-camera" \
  --output "/tmp/htmod-live/camera-1920" --display :122 --resolution 1920x1080
```

Each script writes screenshots, Wine/Xvfb/openbox logs, and `summary.json`. Process and log checks do not prove visual correctness: inspect menu/Quick Setup clipping and camera direction, clamping, banking, and camera switches yourself. The Quick Setup flow uses `map west` and `ui t cosetup`; **it never presses OK**, because that action is known to crash in the headless flow. Wine/Xvfb mouse activation and post-console deadlocks can still prevent a run; report those as limitations, not passes. Reusing a caller prefix can shut down its other Wine processes during cleanup, so a dedicated prefix is mandatory.

## Release rules

Keep the Python-free end-user experience unchanged. `tools/templates/Setup.ps1` consumes generated component metadata; do not duplicate labels, dependencies, conflicts, data ownership, or component IDs in PowerShell. Preserve schema-1 backup compatibility, known-hash gates, backups, rollback, uninstall history, save/unrelated-file guards, and automatic detection.

The experimental FOV attempt remains outside manifests, registry, release payloads, and supported author examples. Native Windows PowerShell 5.1 and visual live checks require separate evidence on environments where they actually ran.
