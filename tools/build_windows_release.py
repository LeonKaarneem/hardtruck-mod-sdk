#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "sdk"))

from htmod import PatchError, Selection, load_release
from htmod.patch import PatchRunner, PatchState
from htmod.runner import load_binary_mods, materialize_data

TEMPLATE_ROOT = PROJECT_ROOT / "tools/templates"
PRISTINE_GAME_SHA256 = "eb01589a2f6c05e67623a26994b0167df20536e976012f0d470ed526e54292e3"
PRISTINE_DX8_SHA256 = "7dcdca3c8c01dd03650507da1264d5322f8d8f3587f731201dc0e0e49dbbd66c"


class BuildError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_game_variants(source, payload, work):
    release = load_release(PROJECT_ROOT)
    game_components = tuple(
        mod_id for mod_id, mod in release.mods.items()
        if mod.kind == "binary" and mod.target == "game/sys/game.dll"
    )
    non_game_components = tuple(mod_id for mod_id in release.mods if mod_id not in game_components)
    game_mods = load_binary_mods(release, game_components)
    variants = {}
    hashes = set()
    pristine_hash = sha256(source)

    for name, variant in release.game_variants.items():
        selected = variant.components
        Selection(selected + non_game_components).validate(release.mods)
        selected_mods = [mod for mod in game_mods if mod.id in selected]
        generated = work / f"generated-{name}.dll"
        reference = work / f"reference-{name}.dll"
        shutil.copyfile(source, generated)
        images = {"game/sys/game.dll": bytearray(generated.read_bytes())}
        runner = PatchRunner(images)
        if selected_mods:
            runner.apply(selected_mods)
        generated.write_bytes(images["game/sys/game.dll"])

        states = runner.check(game_mods)
        for mod in game_mods:
            expected = PatchState.PATCHED if mod.id in selected else PatchState.ORIGINAL
            if states[mod.id] != expected:
                raise BuildError(f"game variant {name} has unexpected {mod.id} state {states[mod.id].value}")

        images = {"game/sys/game.dll": bytearray(generated.read_bytes())}
        if selected_mods:
            PatchRunner(images).restore(selected_mods)
        reference.write_bytes(images["game/sys/game.dll"])
        if sha256(reference) != pristine_hash:
            raise BuildError(f"game variant {name} did not restore to pristine bytes")

        destination = payload / f"game-{name}.dll"
        shutil.copyfile(generated, destination)
        payload_hash = sha256(destination)
        hashes.add(payload_hash)
        variants[name] = {
            "file": destination.name,
            "sha256": payload_hash,
            "components": list(selected),
        }

    if len(hashes) != len(release.game_variants):
        raise BuildError("curated game variant payloads are not distinct")
    return variants


def build_dx8_payloads(source, payload, work):
    release = load_release(PROJECT_ROOT)
    mod = load_binary_mods(release, ("dx8_vsync",))[0]
    stock = payload / "dx8-stock.dll"
    vsync = payload / "dx8-vsync.dll"
    shutil.copyfile(source, stock)
    images = {"sys/dx8.dll": bytearray(source.read_bytes())}
    runner = PatchRunner(images)
    runner.apply([mod])
    vsync.write_bytes(images["sys/dx8.dll"])
    if PatchRunner({"sys/dx8.dll": bytearray(stock.read_bytes())}).check([mod])[mod.id] != PatchState.ORIGINAL:
        raise BuildError("DX8 stock payload failed original-state verification")
    if runner.check([mod])[mod.id] != PatchState.PATCHED:
        raise BuildError("DX8 vsync payload failed patched-state verification")

    restored = work / "dx8-restored.dll"
    runner.restore([mod])
    restored.write_bytes(images["sys/dx8.dll"])
    if restored.read_bytes() != source.read_bytes():
        raise BuildError("DX8 vsync patch did not restore to pristine bytes")

    return {
        "stock": {"file": stock.name, "sha256": sha256(stock)},
        "vsync": {"file": vsync.name, "sha256": sha256(vsync)},
    }


def copy_data(release, payload):
    return materialize_data(release, payload)


def build_payload_manifest(release, payload, game_variants, dx8, game_dll, dx8_dll):
    components = {}
    data_components = {}
    for mod in release.mods.values():
        components[mod.id] = {
            "version": mod.version,
            "label": mod.setup["label"],
            "description": mod.setup["description"],
            "default": mod.setup["default"],
            "requires": list(mod.requires),
            "conflicts": list(mod.conflicts),
            "new_profile": mod.new_profile,
            "kind": mod.kind,
            "target": mod.target,
        }
        if mod.kind == "data":
            data_components[mod.id] = [
                "data/" + record.destination.removeprefix("game/usr/")
                for record in mod.files
            ]
    payloads = {
        path.relative_to(payload).as_posix(): sha256(path)
        for path in sorted(payload.rglob("*"))
        if path.is_file()
    }
    return {
        "schema_version": 2,
        "components": components,
        "sources": {
            "game_dll": {
                "path": "game/sys/game.dll",
                "sha256": sha256(game_dll),
            },
            "dx8_dll": {
                "path": "sys/dx8.dll",
                "sha256": sha256(dx8_dll),
            },
        },
        "game_variants": game_variants,
        "legacy_game_sha256": list(release.legacy_game_sha256),
        "dx8": dx8,
        "data_components": data_components,
        "payloads": payloads,
    }


def build_shell_manifest(release, payload, game_variants, dx8):
    records = []

    def add(kind, relative, expected):
        if any(character in relative for character in ("\t", "\r", "\n")):
            raise BuildError(f"shell manifest path is not representable: {relative!r}")
        records.append("\t".join((kind, relative, expected)))

    for variant in game_variants.values():
        add("variant", variant["file"], variant["sha256"])
    for legacy_hash in release.legacy_game_sha256:
        add("legacy-game", "-", legacy_hash)
    game_components = tuple(
        mod_id for mod_id, mod in release.mods.items()
        if mod.kind == "binary" and mod.target == "game/sys/game.dll"
    )
    install_all = [
        variant for variant in game_variants.values()
        if tuple(variant["components"]) == game_components
    ]
    if len(install_all) != 1:
        raise BuildError("shell installer requires one curated all-game-components variant")
    add("install-game", install_all[0]["file"], install_all[0]["sha256"])
    add("install-dx8", dx8["vsync"]["file"], dx8["vsync"]["sha256"])
    add("stock-dx8", dx8["stock"]["file"], dx8["stock"]["sha256"])
    for mod in release.mods.values():
        for record in mod.files:
            relative = record.destination.removeprefix("game/usr/")
            add("data", "data/" + relative, sha256(payload / "data" / relative))
    return "\n".join(records) + "\n"


def default_test_base_file(relative):
    candidates = [PROJECT_ROOT / "test-base" / relative]
    if PROJECT_ROOT.parent.name == ".worktrees":
        candidates.append(PROJECT_ROOT.parent.parent / "test-base" / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def parse_args():
    parser = argparse.ArgumentParser(description="Build the Hard Truck Modern Windows payload")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--game-dll",
        type=Path,
        default=default_test_base_file(Path("game/sys/game.dll")),
    )
    parser.add_argument(
        "--dx8-dll",
        type=Path,
        default=default_test_base_file(Path("sys/dx8.dll")),
    )
    return parser.parse_args()


def build(args):
    release = load_release(PROJECT_ROOT)
    if not args.game_dll.is_file():
        raise BuildError(f"pristine game DLL not found: {args.game_dll}")
    if not args.dx8_dll.is_file():
        raise BuildError(f"pristine DX8 DLL not found: {args.dx8_dll}")
    for path, expected, label in (
        (args.game_dll, PRISTINE_GAME_SHA256, "game DLL"),
        (args.dx8_dll, PRISTINE_DX8_SHA256, "DX8 DLL"),
    ):
        actual = sha256(path)
        if actual != expected:
            raise BuildError(
                f"{label} SHA-256 mismatch for {path}: expected {expected}, got {actual}"
            )
    for template in (
        TEMPLATE_ROOT / "Setup.bat",
        TEMPLATE_ROOT / "Play Modded.bat",
        TEMPLATE_ROOT / "CrashReport.bat",
        TEMPLATE_ROOT / "Setup.ps1",
        TEMPLATE_ROOT / "INSTALL.sh",
        TEMPLATE_ROOT / "README.md",
    ):
        if not template.is_file():
            raise BuildError(f"installer template not found: {template}")

    output = args.output
    if output.exists() and not output.is_dir():
        raise BuildError(f"release output is not a directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.build-", dir=output.parent) as release_temporary:
        release_temporary = Path(release_temporary)
        release_staging = release_temporary / "release"
        if output.exists():
            shutil.copytree(output, release_staging)
        else:
            release_staging.mkdir()

        installer = release_staging / "installer"
        installer.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATE_ROOT / "Setup.bat", release_staging / "Setup.bat")
        shutil.copyfile(TEMPLATE_ROOT / "Play Modded.bat", release_staging / "Play Modded.bat")
        shutil.copyfile(TEMPLATE_ROOT / "CrashReport.bat", release_staging / "CrashReport.bat")
        shutil.copyfile(TEMPLATE_ROOT / "Setup.ps1", installer / "Setup.ps1")
        shutil.copyfile(TEMPLATE_ROOT / "INSTALL.sh", release_staging / "INSTALL.sh")
        (release_staging / "INSTALL.sh").chmod(0o755)
        shutil.copyfile(TEMPLATE_ROOT / "README.md", release_staging / "README.md")
        with tempfile.TemporaryDirectory(prefix=".payload-build-", dir=installer) as temporary:
            temporary = Path(temporary)
            staging = temporary / "payload"
            work = temporary / "work"
            staging.mkdir()
            work.mkdir()

            variants = build_game_variants(args.game_dll, staging, work)
            dx8 = build_dx8_payloads(args.dx8_dll, staging, work)
            copy_data(release, staging)
            manifest = build_payload_manifest(
                release,
                staging,
                game_variants=variants,
                dx8=dx8,
                game_dll=args.game_dll,
                dx8_dll=args.dx8_dll,
            )
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (staging / "install-manifest.tsv").write_text(
                build_shell_manifest(release, staging, variants, dx8),
                encoding="utf-8",
            )

            destination = installer / "payload"
            if destination.exists():
                shutil.rmtree(destination)
            staging.replace(destination)

        previous = release_temporary / "previous"
        try:
            if output.exists():
                output.replace(previous)
            release_staging.replace(output)
        except OSError as replacement_error:
            if previous.exists() and not output.exists():
                try:
                    previous.replace(output)
                except OSError as rollback_error:
                    recovery_root = Path(tempfile.mkdtemp(
                        prefix=f".{output.name}.recovery-",
                        dir=output.parent.resolve(),
                    ))
                    recovery = recovery_root / "release"
                    previous.replace(recovery)
                    raise BuildError(
                        f"release replacement failed ({replacement_error}); "
                        f"previous release restore failed ({rollback_error}); "
                        f"previous release preserved at {recovery}"
                    ) from rollback_error
            raise replacement_error

    game_component_count = sum(
        mod.kind == "binary" and mod.target == "game/sys/game.dll"
        for mod in release.mods.values()
    )
    print(f"Built {output / 'installer/payload'}")
    print(
        f"Verified {len(variants)} distinct game variants, "
        f"{len(variants) * game_component_count} game checks, "
        f"{len(variants)} restore cycles, and 2 DX8 checks"
    )


def main():
    args = parse_args()
    try:
        build(args)
    except (BuildError, PatchError, OSError) as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
