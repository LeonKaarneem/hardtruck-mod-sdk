# Binary modding

Use binary handlers only for changes that cannot be represented by data overrides. Handlers transform verified in-memory PE images; they never write directly to a game DLL. Work on a disposable v1.2 game copy and keep the original source SHA-256 pinned in every manifest for the same target.

## Supported handler API

A handler exposes `MOD`, an instance of `htmod.patch.BinaryMod`, with an ID and target matching its manifest. The supported building blocks are:

- `Anchor`, `BytePatch`, `Signature`, `find_unique`, `BinaryMod`, `PatchRunner`, and `PatchState` in `htmod.patch`;
- `PEImage.parse`, `rva_to_offset`, `append_section`, `validate_appended_tail`, `append_highlow_relocations`, `remove_highlow_relocations`, and `remove_section` in `htmod.pe`;
- `PatchError`, `PEError`, `SignatureError`, `StateError`, and `TransactionError` for expected failures.

Resolve RVAs through `PEImage` instead of assuming file offsets. Bound searches, require unique complete states, and add anchors for surrounding known bytes. Ambiguous, partial, or foreign input must be refused without mutation.

## Anchored operand example

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

The addresses and bytes above are illustrative, not a game patch. For a real handler, use a unique context signature as in `modpack/mods/physics/patch.py`. For a bounded immediate whose original and patched instruction can occur at the same site, follow `modpack/mods/dx8_vsync/patch.py`: bound the search to one function and require exactly one complete state.

## Code hooks

`modpack/mods/camera/patch.py` is the complete reference for a stateful code hook:

1. find and validate inline hook sites;
2. generate helper code and relative `call` or `jmp` operands;
3. append an owned section with `PEImage.append_section`;
4. validate the appended tail with `PEImage.validate_appended_tail`;
5. record imported absolute addresses with `append_highlow_relocations`;
6. inspect the complete state and reverse it with `remove_highlow_relocations` and `remove_section`.

A relative branch helper has this shape:

```python
import struct


def jump(source_va, destination_va):
    displacement = destination_va - (source_va + 5)
    return b"\xe9" + struct.pack("<i", displacement)
```

`validate_appended_tail(first_name)` validates canonical virtual addresses and raw offsets, raw sizes aligned to virtual sizes, zero inter-section padding, no overlaps or trailing data, and SizeOfImage from the actual last section. Each handler validates only its own payload, flags, sizes, and hook sites. `remove_section` is end-only, so composed handlers restore in reverse order. HIGHLOW relocation blocks are DWORD-padded with a zero entry when the real entry count is odd.

## Register a handler

Using `example_operand` as the ID:

1. Put the implementation in `modpack/mods/example_operand/patch.py` and expose `MOD` with matching `id` and `target`. Loading rejects either identity mismatch.
2. Add `example_operand` to `BINARY_HANDLERS` in `sdk/htmod/registry.py`. This one registry enables manifest validation and static loading.
3. Create `modpack/mods/example_operand/mod.json` with `kind: "binary"`, `handler: "example_operand"`, a supported target, and the pinned pristine `source_sha256`. Include the common setup, dependency, conflict, and `new_profile` fields.
4. Add the ID to the ordered `mods` list in `modpack/release.json`.
5. For a `game.dll` component, deliberately extend `game_variants`. Bit positions follow release order, each variant must match its component bits, and every final binary tuple must be curated. Preserve prior payload streams and retain released hashes in `legacy_game_sha256` with migration source coverage.

Every binary manifest for one target must pin the same pristine hash, even when the components conflict. Release loading exposes the target map as `release.pristine_sha256`. Builders and staging verify this pin before applying mods or writing output. Unknown DLL hashes remain refused. Other targets require explicit builder and migration-catalog support driven by validated release metadata.

`Selection.validate` rejects missing requirements, duplicate selections, and conflicts before file changes. Handler loading itself is identity-only so builders can load every game handler before validating each curated selection.

## Test and debug

Add focused tests for ORIGINAL, PATCHED, FOREIGN, apply/check/restore, exact reverse restoration, and composition with other handlers. Update golden hashes only for an intentional payload change. Run the related suite and validate the release:

```sh
python3 tools/htmod.py validate
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_binary_regressions tests.test_release_variants tests.test_migration -v
```

The pinned real-DLL tests need `test-base` or `HTMOD_TEST_BASE`. See [RELEASING.md](RELEASING.md) for all fixture and runtime prerequisites.

Use the lifecycle commands in [AUTHORING.md](AUTHORING.md) for normal data and binary installation. Direct canonical-only binary debugging remains available by omitting `--migrations`:

```sh
python3 tools/htmod.py check --game-dir "/path/to/test game" \
  --mod config --mod camera --mod physics
python3 tools/htmod.py apply --game-dir "/path/to/test game" \
  --mod config --mod camera --mod physics
python3 tools/htmod.py restore --game-dir "/path/to/test game" \
  --mod config --mod camera --mod physics
python3 tools/htmod.py check --game-dir "/path/to/test game" --mod dx8_vsync
```

Direct commands accept only the manifest's pinned pristine image or an exact recognized whole-file selected-subset state. To add a mod to an already patched target, select both existing and new mods. Apply and restore skip mods already in the requested state, verify every final state before commit, and refuse FOREIGN or incomplete transitions without a commit.

Lifecycle apply also normalizes recognized legacy and official Estonian sources. Direct check, apply, and restore do not consult migration catalogs. Selective lifecycle removal rebuilds a retained curated variant; whole removal replays exact incoming backups rather than using direct binary restore.

Do not add a payload copy map, standalone patcher, or release-only handler path. Experimental FOV modifications are not SDK components or supported release features. No original game or DX8 DLL payload may be added to the repository or package.
