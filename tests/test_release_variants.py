import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod import manifest as manifest_module
from htmod import runner as runner_module
from tools import build_windows_release


class ReleaseDerivedVariantTests(unittest.TestCase):
    def make_fourth_release(self, parent):
        modpack = Path(parent) / "modpack"
        shutil.copytree(ROOT / "modpack", modpack)
        release_path = modpack / "release.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        release["mods"].remove("ui_keyboard")
        shutil.rmtree(modpack / "mods/ui_keyboard")
        release["mods"].insert(-1, "fourth")
        release["game_variants"] = {
            f"{number:04b}": {
                "file": f"game-{number:04b}.dll",
                "components": [
                    component
                    for bit, component in enumerate(("camera", "physics", "ai", "fourth"))
                    if number & (1 << bit)
                ],
            }
            for number in range(16)
        }
        release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
        fourth = modpack / "mods/fourth"
        fourth.mkdir()
        manifest = json.loads((modpack / "mods/ai/mod.json").read_text(encoding="utf-8"))
        manifest.update(id="fourth", handler="fourth")
        manifest["setup"] = {"label": "Fourth", "description": "Synthetic fourth component", "default": False}
        (fourth / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        original = build_windows_release.default_test_base_file(Path("game/sys/game.dll")).read_bytes()[0x2000]
        (fourth / "patch.py").write_text(
            "from htmod.patch import BinaryMod, BytePatch\n"
            "class FourthMod(BinaryMod):\n"
            "    def __init__(self):\n"
            "        super().__init__('fourth', 'game/sys/game.dll', ())\n"
            "    def patches_for(self, image):\n"
            f"        return (BytePatch('synthetic fourth', 0x2000, bytes(({original},)), bytes(({original ^ 1},))),)\n"
            "MOD = FourthMod()\n",
            encoding="utf-8",
        )
        return load_release(Path(parent))

    def test_four_game_components_generate_all_curated_variants_and_installer_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(manifest_module, "BINARY_HANDLERS", manifest_module.BINARY_HANDLERS | {"fourth"}), \
                    mock.patch.object(runner_module, "REGISTERED_BINARY_HANDLERS", runner_module.REGISTERED_BINARY_HANDLERS | {"fourth"}):
                release = self.make_fourth_release(temporary)
                self.assertEqual(len(release.game_variants), 16)
                self.assertEqual(release.game_variants["1000"].components, ("fourth",))
                payload = Path(temporary) / "payload"
                work = Path(temporary) / "work"
                payload.mkdir()
                work.mkdir()
                with mock.patch.object(build_windows_release, "load_release", return_value=release):
                    variants = build_windows_release.build_game_variants(
                        build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                        payload,
                        work,
                    )
                self.assertEqual(len(variants), 16)
                self.assertEqual(variants["1000"]["components"], ["fourth"])
                self.assertEqual(variants["1000"]["file"], "game-1000.dll")
                self.assertNotEqual(variants["1000"]["sha256"], variants["0000"]["sha256"])
                self.assertEqual(
                    hashlib.sha256((payload / "game-1000.dll").read_bytes()).hexdigest(),
                    variants["1000"]["sha256"],
                )
                installer = build_windows_release.build_payload_manifest(
                    release,
                    payload,
                    variants,
                    dx8={},
                    game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                    dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
                )
                self.assertEqual(len(installer["game_variants"]), 16)
                self.assertEqual(installer["game_variants"]["1000"]["components"], ["fourth"])
                self.assertEqual(installer["components"]["fourth"]["target"], "game/sys/game.dll")

                direct = Path(temporary) / "direct"
                direct.mkdir()
                mods = runner_module.load_binary_mods(release, ("camera", "physics", "ai", "fourth"))
                for name, record in variants.items():
                    with self.subTest(direct_variant=name):
                        target = direct / "game/sys/game.dll"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes((payload / record["file"]).read_bytes())
                        recognized = runner_module.runner_from_directory(direct, mods)
                        self.assertEqual(recognized.images["game/sys/game.dll"], target.read_bytes())
                target.write_bytes((payload / "game-1000.dll").read_bytes() + b"unknown suffix")
                with self.assertRaisesRegex(Exception, "whole-file|unrecognized"):
                    runner_module.runner_from_directory(direct, mods)

    def test_builder_loads_all_handlers_but_validates_each_curated_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            modpack = Path(temporary) / "modpack"
            shutil.copytree(ROOT / "modpack", modpack)
            release_path = modpack / "release.json"
            value = json.loads(release_path.read_text(encoding="utf-8"))
            value["game_variants"] = {
                name: value["game_variants"][name]
                for name in ("0001", "0010", "0100")
            }
            release_path.write_text(json.dumps(value), encoding="utf-8")
            camera_path = modpack / "mods/camera/mod.json"
            camera = json.loads(camera_path.read_text(encoding="utf-8"))
            camera["requires"] = ["config"]
            camera_path.write_text(json.dumps(camera), encoding="utf-8")
            physics_path = modpack / "mods/physics/mod.json"
            physics = json.loads(physics_path.read_text(encoding="utf-8"))
            physics["conflicts"] = ["ai"]
            physics_path.write_text(json.dumps(physics), encoding="utf-8")
            release = load_release(Path(temporary))
            payload = Path(temporary) / "payload"
            work = Path(temporary) / "work"
            payload.mkdir()
            work.mkdir()
            with mock.patch.object(build_windows_release, "load_release", return_value=release):
                variants = build_windows_release.build_game_variants(
                    build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                    payload,
                    work,
                )
            self.assertEqual(list(variants), ["0001", "0010", "0100"])

            value["game_variants"] = {
                "0110": {"file": "game-0110.dll", "components": ["physics", "ai"]}
            }
            release_path.write_text(json.dumps(value), encoding="utf-8")
            invalid = load_release(Path(temporary))
            with mock.patch.object(build_windows_release, "load_release", return_value=invalid), \
                    self.assertRaisesRegex(Exception, "conflicts"):
                build_windows_release.build_game_variants(
                    build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                    payload,
                    work,
                )

            from htmod import cli as cli_module
            with mock.patch("htmod.cli.load_release", return_value=release):
                result = cli_module.main(["check", "--game-dir", str(temporary), "--mod", "camera"])
            self.assertEqual(result, 1)

    def test_variant_width_and_component_bits_must_match_registered_game_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(manifest_module, "BINARY_HANDLERS", manifest_module.BINARY_HANDLERS | {"fourth"}), \
                    mock.patch.object(runner_module, "REGISTERED_BINARY_HANDLERS", runner_module.REGISTERED_BINARY_HANDLERS | {"fourth"}):
                release = self.make_fourth_release(temporary)
                release_path = release.root / "release.json"
                value = json.loads(release_path.read_text(encoding="utf-8"))
                value["game_variants"]["001"] = {"file": "game-001.dll", "components": []}
                release_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(Exception, "bit length"):
                    load_release(release.root.parent)
                value["game_variants"].pop("001")
                value["game_variants"]["1000"]["components"] = ["camera"]
                release_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(Exception, "does not match component bits"):
                    load_release(release.root.parent)


if __name__ == "__main__":
    unittest.main()
