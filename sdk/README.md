# Hard Truck Mod SDK

The SDK is the authoring and release path for Hard Truck Modern. Players do not need Python: they extract the release ZIP and run `Setup.bat`, or use `INSTALL.sh` on Linux/Wine. Authors use Python 3 and the repository's standard-library-only SDK.

## Start here

- [AUTHORING.md](AUTHORING.md): a working stock-file Credits quickstart, data manifests, `game/usr` overrides, lifecycle status/plan/apply/removal, the public API boundary, and the `migration-add` command.
- [BINARY_MODDING.md](BINARY_MODDING.md): the supported handler API, anchored patches, code hooks, static handler registration, and binary test/debug commands.
- [RELEASING.md](RELEASING.md): the test-prerequisite matrix, deterministic builds, exact-ZIP verification, installer compatibility, live Wine smokes, and native Windows caveats.

For a first data-only mod, begin with the Credits quickstart. It uses the lifecycle commands directly and does not require a release build.

## Source of truth

- `modpack/mods/<id>/mod.json`: one component's metadata and owned data or registered binary handler.
- `modpack/mods/<id>/files/`: data source files.
- `modpack/mods/<id>/patch.py`: binary implementation exposing `MOD`.
- `modpack/release.json`: ordered release components and explicitly curated game variants.
- `modpack/migrations.json`: hash-only source identities and compact normalization splices.
- `sdk/htmod/registry.py`: static handler IDs and transform mapping used by manifest validation and execution.
- `sdk/htmod/quick_setup.py`: the shared selection-aware Quick Setup byte renderer used by lifecycle, release materialization, and the frozen render operation.

JSON never names a Python path and no plugin directory is scanned. Do not add a payload copy map, standalone patcher, release-only special case, original game or DX8 DLL payload, or full binary delta. Experimental FOV modifications are not SDK components or safe release features.

Validate the current release from the repository root:

```sh
python3 tools/htmod.py validate
python3 tools/htmod.py list
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Some tests require real DLL fixtures, a frozen patcher, PowerShell, an exact release ZIP, or live Wine. The [prerequisite matrix](RELEASING.md#test-prerequisite-matrix) lists each selector and its runtime requirements.
