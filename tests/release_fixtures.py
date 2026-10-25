import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.patch import PatchRunner
from htmod.runner import load_binary_mods
from tools import build_windows_release


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def current_binary_images():
    release = load_release(ROOT)
    game_source = build_windows_release.default_test_base_file(Path("game/sys/game.dll")).read_bytes()
    dx8_source = build_windows_release.default_test_base_file(Path("sys/dx8.dll")).read_bytes()
    game_components = tuple(
        mod_id for mod_id, mod in release.mods.items()
        if mod.kind == "binary" and mod.target == "game/sys/game.dll"
    )
    game_mods = load_binary_mods(release, game_components)
    game = {}
    for name, variant in release.game_variants.items():
        selected = [mod for mod in game_mods if mod.id in variant.components]
        images = {"game/sys/game.dll": bytearray(game_source)}
        if selected:
            PatchRunner(images).apply(selected)
        game[name] = bytes(images["game/sys/game.dll"])
    dx8_mod = load_binary_mods(release, ("dx8_vsync",))[0]
    images = {"sys/dx8.dll": bytearray(dx8_source)}
    PatchRunner(images).apply([dx8_mod])
    dx8 = {"stock": dx8_source, "vsync": bytes(images["sys/dx8.dll"])}
    return game, dx8


def write_current_payload(payload):
    payload = Path(payload)
    payload.mkdir(parents=True, exist_ok=True)
    release = load_release(ROOT)
    game, dx8 = current_binary_images()
    variants = {}
    for name, data in game.items():
        filename = f"game-{name}.dll"
        (payload / filename).write_bytes(data)
        variants[name] = {
            "file": filename,
            "sha256": sha256(data),
            "components": list(release.game_variants[name].components),
        }
    dx8_records = {}
    for name, data in dx8.items():
        filename = f"dx8-{name}.dll"
        (payload / filename).write_bytes(data)
        dx8_records[name] = {"file": filename, "sha256": sha256(data)}
    return variants, dx8_records


def fake_pe64(path):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0x8664)
    Path(path).write_bytes(data)


def build_legacy_installer_fixture(output):
    from tools import build_windows_release as builder

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fake_patcher = output.parent / f"{output.name}-fixture-htmod.exe"
    fake_pe64(fake_patcher)
    builder.build(SimpleNamespace(
        output=output,
        game_dll=builder.default_test_base_file(Path("game/sys/game.dll")),
        dx8_dll=builder.default_test_base_file(Path("sys/dx8.dll")),
        patcher_exe=fake_patcher,
        migrations=ROOT / "modpack/migrations.json",
    ))
    payload = output / "installer/payload"
    variants, dx8 = write_current_payload(payload)
    release = load_release(ROOT)
    manifest_path = payload / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest["game_variants"] = variants
    manifest["dx8"] = dx8
    manifest["legacy_game_sha256"] = list(release.legacy_game_sha256)
    for name in ("htmod.exe", "migrations.json"):
        (payload / name).unlink()
    manifest["payloads"] = {
        path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(payload.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "install-manifest.tsv"}
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    fake_patcher.unlink()
