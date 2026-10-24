#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "sdk"))

from htmod import PatchError, load_release
from htmod.runner import materialize_data

PATCHER_ROOT = PROJECT_ROOT / "modpack/patches"
TEMPLATE_ROOT = PROJECT_ROOT / "tools/templates"
GAME_PATCHERS = (
    ("camera", PATCHER_ROOT / "patch_camera_mods.py"),
    ("physics", PATCHER_ROOT / "patch_physics_fixes.py"),
    ("ai", PATCHER_ROOT / "patch_ai_fixes.py"),
)
DX8_PATCHER = PATCHER_ROOT / "patch_dx8_vsync.py"
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


def run_patcher(patcher, dll, mode=None):
    command = [sys.executable, str(patcher), str(dll)]
    if mode:
        command.append(mode)
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise BuildError(
            f"{patcher.name} {mode or 'patch'} failed for {dll}:\n"
            f"{result.stdout}{result.stderr}"
        )


def selected_patchers(number):
    return [
        (component, patcher)
        for bit, (component, patcher) in enumerate(GAME_PATCHERS)
        if number & (1 << bit)
    ]


def build_game_variants(source, payload, work):
    variants = {}
    hashes = set()
    pristine_hash = sha256(source)

    for number in range(8):
        name = f"{number:03b}"
        selected = selected_patchers(number)
        generated = work / f"generated-{name}.dll"
        reference = work / f"reference-{name}.dll"
        shutil.copyfile(source, generated)
        shutil.copyfile(source, reference)

        for _, patcher in selected:
            run_patcher(patcher, generated)
        for _, patcher in GAME_PATCHERS:
            run_patcher(patcher, generated, "--check")
        for _, patcher in selected:
            run_patcher(patcher, reference)

        if generated.read_bytes() != reference.read_bytes():
            raise BuildError(f"game variant {name} differs from direct sequential patching")
        for _, patcher in reversed(selected):
            run_patcher(patcher, reference, "--restore")
        if sha256(reference) != pristine_hash:
            raise BuildError(f"game variant {name} did not restore to pristine bytes")

        destination = payload / f"game-{name}.dll"
        shutil.copyfile(generated, destination)
        payload_hash = sha256(destination)
        hashes.add(payload_hash)
        variants[name] = {
            "file": destination.name,
            "sha256": payload_hash,
            "components": [component for component, _ in selected],
        }

    if len(hashes) != 8:
        raise BuildError("the eight game variants are not distinct")
    return variants


def build_dx8_payloads(source, payload, work):
    stock = payload / "dx8-stock.dll"
    vsync = payload / "dx8-vsync.dll"
    shutil.copyfile(source, stock)
    shutil.copyfile(source, vsync)
    run_patcher(DX8_PATCHER, vsync)
    run_patcher(DX8_PATCHER, stock, "--check")
    run_patcher(DX8_PATCHER, vsync, "--check")

    restored = work / "dx8-restored.dll"
    shutil.copyfile(vsync, restored)
    run_patcher(DX8_PATCHER, restored, "--restore")
    if restored.read_bytes() != source.read_bytes():
        raise BuildError("DX8 vsync patch did not restore to pristine bytes")

    return {
        "stock": {"file": stock.name, "sha256": sha256(stock)},
        "vsync": {"file": vsync.name, "sha256": sha256(vsync)},
    }


def copy_data(release, payload):
    return materialize_data(release, payload)


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
    for _, patcher in (*GAME_PATCHERS, ("dx8", DX8_PATCHER)):
        if not patcher.is_file():
            raise BuildError(f"patcher not found: {patcher}")
    for template in (
        TEMPLATE_ROOT / "Setup.bat",
        TEMPLATE_ROOT / "Setup.ps1",
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
        shutil.copyfile(TEMPLATE_ROOT / "Setup.ps1", installer / "Setup.ps1")
        shutil.copyfile(TEMPLATE_ROOT / "README.md", release_staging / "README.md")
        with tempfile.TemporaryDirectory(prefix=".payload-build-", dir=installer) as temporary:
            temporary = Path(temporary)
            staging = temporary / "payload"
            work = temporary / "work"
            staging.mkdir()
            work.mkdir()

            variants = build_game_variants(args.game_dll, staging, work)
            dx8 = build_dx8_payloads(args.dx8_dll, staging, work)
            data_components = copy_data(release, staging)
            payloads = {
                path.relative_to(staging).as_posix(): sha256(path)
                for path in sorted(staging.rglob("*"))
                if path.is_file()
            }
            manifest = {
                "schema_version": 1,
                "sources": {
                    "game_dll": {
                        "path": "game/sys/game.dll",
                        "sha256": sha256(args.game_dll),
                    },
                    "dx8_dll": {
                        "path": "sys/dx8.dll",
                        "sha256": sha256(args.dx8_dll),
                    },
                },
                "game_variants": variants,
                "dx8": dx8,
                "data_components": data_components,
                "payloads": payloads,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
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

    print(f"Built {output / 'installer/payload'}")
    print("Verified 8 distinct game variants, 24 game checks, 8 restore cycles, and 2 DX8 checks")


def main():
    args = parse_args()
    try:
        build(args)
    except (BuildError, PatchError, OSError) as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
