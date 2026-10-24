import hashlib
import importlib.util
import struct
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "tests"))

from htmod.errors import StateError
from htmod.patch import PatchRunner, PatchState
from htmod.pe import PEImage
from htmod import load_release
from htmod.runner import load_binary_mods
from test_camera_sdk import pristine_game


LEGACY_HASHES = {
    "0000": "eb01589a2f6c05e67623a26994b0167df20536e976012f0d470ed526e54292e3",
    "0001": "30fa4d5fa1d28bb4278973eec4272d5a25886a16b020e9879a4d266ab8c7d3a2",
    "0010": "b27816cfec9d99f877db3da33081b5b059d736ba3a4aad223da9536291e6c6e9",
    "0011": "701d27b7b41b27d89e59657ec928a23fb962f0c6ffd9593a6f5358088f16ee2a",
    "0100": "b24dd16859d02ba13e022399ad3d11ac7174c734fdc0b052da854dc715c02432",
    "0101": "1d1d434f925d0cbbdfe3f63d68be5755376d6ce5a088fe1c4eef70e7c872068f",
    "0110": "a98cb455f390e426054bf092799db6185f0721e6a3781cbf3e55f472f262ab97",
    "0111": "fff9a0b9ece1110f3516f5010405b1e65f2bd11e340fc18aad60020c31877b85",
}
WRAPPER_SHA256 = "1c880a863afd349f3a00e0d4e1cf141baf2ef341ffc852e275b0ee8d0c5ea9ec"


def load_ui_module():
    path = ROOT / "modpack/mods/ui_keyboard/patch.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("ui_keyboard_sdk_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UIKeyboardContractTests(unittest.TestCase):
    def require_module(self):
        module = load_ui_module()
        self.assertIsNotNone(module, "ui_keyboard handler is missing")
        return module

    def test_wrapper_is_exact_78_bytes_with_dynamic_resume_addresses(self):
        module = self.require_module()
        base = 0x100FC000
        wrapper = module.build_wrapper(base)
        self.assertEqual(len(wrapper), 78)
        self.assertEqual(hashlib.sha256(wrapper).hexdigest(), WRAPPER_SHA256)
        self.assertEqual(wrapper[:5], bytes.fromhex("8b4c240456"))
        self.assertEqual(wrapper[0x20:0x25], bytes.fromhex("b840000000"))
        self.assertEqual(wrapper[0x39:0x3E], bytes.fromhex("b880000000"))
        self.assertEqual(
            struct.unpack_from("<i", wrapper, 0x45)[0],
            0x10070E9E - (base + 0x44 + 5),
        )
        self.assertEqual(
            struct.unpack_from("<i", wrapper, 0x4A)[0],
            0x10070E27 - (base + 0x49 + 5),
        )

    def test_manifest_is_required_last_component_and_release_has_16_variants(self):
        release = load_release(ROOT)
        game_components = [
            mod.id for mod in release.mods.values()
            if mod.kind == "binary" and mod.target == "game/sys/game.dll"
        ]
        self.assertEqual(game_components, ["camera", "physics", "ai", "ui_keyboard"])
        self.assertEqual(release.mods["ui_keyboard"].requires, ("config",))
        self.assertEqual(list(release.game_variants), [f"{number:04b}" for number in range(16)])
        for name, expected_hash in LEGACY_HASHES.items():
            with self.subTest(legacy_variant=name):
                record = release.game_variants[name]
                self.assertNotIn("ui_keyboard", record.components)
                self.assertEqual(record.file, f"game-{name}.dll")

    def test_all_variants_apply_check_restore_and_preserve_legacy_hashes(self):
        release = load_release(ROOT)
        for name, record in release.game_variants.items():
            with self.subTest(variant=name):
                mods = load_binary_mods(release, record.components)
                images = {"game/sys/game.dll": bytearray(pristine_game())}
                runner = PatchRunner(images)
                if mods:
                    runner.apply(mods)
                    self.assertEqual(runner.check(mods), {mod.id: PatchState.PATCHED for mod in mods})
                if name in LEGACY_HASHES:
                    self.assertEqual(
                        hashlib.sha256(images["game/sys/game.dll"]).hexdigest(),
                        LEGACY_HASHES[name],
                    )
                if mods:
                    runner.restore(mods)
                self.assertEqual(images["game/sys/game.dll"], pristine_game())

    def test_camera_remains_patched_with_later_uitab_and_restores_after_ui(self):
        self.require_module()
        mods = load_binary_mods(load_release(ROOT), ["camera", "physics", "ai", "ui_keyboard"])
        images = {"game/sys/game.dll": bytearray(pristine_game())}
        runner = PatchRunner(images)
        runner.apply(mods)
        self.assertEqual(runner.check(mods), {mod.id: PatchState.PATCHED for mod in mods})
        image = PEImage.parse(images["game/sys/game.dll"])
        self.assertEqual(image.sections[-2].name, ".htcam")
        self.assertEqual(image.sections[-1].name, ".uitab")
        runner.restore(mods)
        self.assertEqual(images["game/sys/game.dll"], pristine_game())

    def test_cli_validates_data_dependency_but_loads_only_binary_handlers(self):
        from htmod import cli

        mods = load_binary_mods(load_release(ROOT), ["camera", "physics", "ai", "ui_keyboard"])
        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary) / "game"
            target = game / "game/sys/game.dll"
            target.parent.mkdir(parents=True)
            images = {"game/sys/game.dll": bytearray(pristine_game())}
            PatchRunner(images).apply(mods)
            target.write_bytes(images["game/sys/game.dll"])
            result = cli.main([
                "--root", str(ROOT), "check", "--game-dir", str(game),
                "--mod", "config", "--mod", "camera", "--mod", "physics",
                "--mod", "ai", "--mod", "ui_keyboard",
            ])
        self.assertEqual(result, 0)

    def test_foreign_hook_section_and_later_data_are_refused_without_mutation(self):
        self.require_module()
        mod = load_binary_mods(load_release(ROOT), ["ui_keyboard"])[0]
        patched = bytearray(pristine_game())
        PatchRunner({mod.target: patched}).apply([mod])
        for mutation in ("hook", "wrapper", "trailing", "unknown-later-section"):
            with self.subTest(mutation=mutation):
                candidate = patched[:]
                image = PEImage.parse(candidate)
                if mutation == "hook":
                    offset = image.va_to_offset(0x10070E20)
                    candidate[offset] ^= 0xFF
                elif mutation == "wrapper":
                    section = image.section(".uitab")
                    candidate[section.raw_offset + 0x20] ^= 0xFF
                elif mutation == "trailing":
                    candidate += b"later data"
                else:
                    section = image.section(".uitab")
                    header = image.section_table + 40 * (len(image.sections) - 1)
                    candidate[header:header + 8] = b".other\0\0"
                before = candidate[:]
                self.assertEqual(mod.inspect_bytes(candidate), PatchState.FOREIGN)
                with self.assertRaises(StateError):
                    PatchRunner({mod.target: candidate}).restore([mod])
                self.assertEqual(candidate, before)


if __name__ == "__main__":
    unittest.main()
