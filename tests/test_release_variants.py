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
from htmod import registry as registry_module
from htmod import runner as runner_module
from tools import build_windows_release


class ReleaseDerivedVariantTests(unittest.TestCase):
    def make_appended_tail_release(self, parent):
        modpack = Path(parent) / "modpack"
        shutil.copytree(ROOT / "modpack", modpack)
        release_path = modpack / "release.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        release["mods"].insert(-1, "third")
        release["game_variants"] = {
            f"{number:05b}": {
                "file": f"game-{number:05b}.dll",
                "components": [
                    component
                    for bit, component in enumerate(
                        ("camera", "physics", "ai", "ui_keyboard", "third")
                    )
                    if number & (1 << bit)
                ],
            }
            for number in range(32)
        }
        release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
        third = modpack / "mods/third"
        third.mkdir()
        manifest = json.loads((modpack / "mods/ai/mod.json").read_text(encoding="utf-8"))
        manifest.update(id="third", handler="third")
        manifest["setup"] = {"label": "Third", "description": "Synthetic appended section", "default": False}
        (third / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (third / "patch.py").write_text(
            "from htmod.errors import PEError, StateError\n"
            "from htmod.patch import BinaryMod, PatchState\n"
            "from htmod.pe import align\n"
            "NAME = '.third'\n"
            "SIZE = 0x1000\n"
            "FLAGS = 0x40000040\n"
            "PAYLOAD = bytes(range(256)) * 16\n"
            "class ThirdMod(BinaryMod):\n"
            "    def __init__(self):\n"
            "        super().__init__('third', 'game/sys/game.dll', ())\n"
            "    def inspect(self, image):\n"
            "        try:\n"
            "            if not any(section.name == NAME for section in image.sections):\n"
            "                return PatchState.ORIGINAL\n"
            "            section = image.validate_appended_tail(NAME)\n"
            "            if (section.virtual_size, section.raw_size, section.flags) != (SIZE, SIZE, FLAGS):\n"
            "                return PatchState.FOREIGN\n"
            "            if image.data[section.raw_offset:section.raw_offset + SIZE] != PAYLOAD:\n"
            "                return PatchState.FOREIGN\n"
            "            return PatchState.PATCHED\n"
            "        except PEError:\n"
            "            return PatchState.FOREIGN\n"
            "    def apply(self, image):\n"
            "        if self.inspect(image) != PatchState.ORIGINAL:\n"
            "            raise StateError('third is not ORIGINAL; refusing apply')\n"
            "        working = bytearray(image.data)\n"
            "        candidate = type(image).parse(working)\n"
            "        candidate.append_section(NAME, PAYLOAD, FLAGS)\n"
            "        if self.inspect(candidate) != PatchState.PATCHED:\n"
            "            raise StateError('third failed post-apply verification')\n"
            "        image.data[:] = working\n"
            "    def restore(self, image):\n"
            "        if self.inspect(image) == PatchState.ORIGINAL and self.allow_original_restore:\n"
            "            return\n"
            "        if self.inspect(image) != PatchState.PATCHED:\n"
            "            raise StateError('third is not PATCHED; refusing restore')\n"
            "        working = bytearray(image.data)\n"
            "        candidate = type(image).parse(working)\n"
            "        section = candidate.section(NAME)\n"
            "        candidate.remove_section(\n"
            "            NAME, original_eof=section.raw_offset,\n"
            "            original_image_size=align(section.virtual_address, candidate.section_alignment),\n"
            "        )\n"
            "        if self.inspect(candidate) != PatchState.ORIGINAL:\n"
            "            raise StateError('third failed post-restore verification')\n"
            "        image.data[:] = working\n"
            "MOD = ThirdMod()\n",
            encoding="utf-8",
        )
        return load_release(Path(parent))

    def make_modified_pristine_release(self, parent, target):
        modpack = Path(parent) / "modpack"
        shutil.copytree(ROOT / "modpack", modpack)
        for path in modpack.glob("mods/*/mod.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("kind") == "binary" and manifest.get("target") == target:
                manifest["source_sha256"] = "0" * 64
                path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return load_release(Path(parent))

    def test_game_builder_rejects_modified_manifest_pristine_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = self.make_modified_pristine_release(temporary, "game/sys/game.dll")
            with mock.patch.object(build_windows_release, "load_release", return_value=release), \
                    self.assertRaisesRegex(Exception, "pristine.*SHA-256|SHA-256.*pristine"):
                build_windows_release.build_game_variants(
                    build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                )

    def test_dx8_builder_rejects_modified_manifest_pristine_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = self.make_modified_pristine_release(temporary, "sys/dx8.dll")
            with mock.patch.object(build_windows_release, "load_release", return_value=release), \
                    self.assertRaisesRegex(Exception, "pristine.*SHA-256|SHA-256.*pristine"):
                build_windows_release.build_dx8_payloads(
                    build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
                )

    def test_five_game_components_generate_distinct_curated_variants_and_installer_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                    registry_module, "BINARY_HANDLERS", registry_module.BINARY_HANDLERS | {"third"}):
                release = self.make_appended_tail_release(temporary)
                self.assertEqual(len(release.game_variants), 32)
                self.assertEqual(release.game_variants["10000"].components, ("third",))
                payload = Path(temporary) / "payload"
                payload.mkdir()
                with mock.patch.object(build_windows_release, "load_release", return_value=release):
                    variants = build_windows_release.build_game_variants(
                        build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                    )
                self.assertEqual(len(variants), 32)
                self.assertEqual(len({record["sha256"] for record in variants.values()}), 32)
                self.assertEqual(variants["10000"]["components"], ["third"])
                self.assertNotIn("file", variants["10000"])
                self.assertNotEqual(variants["10000"]["sha256"], variants["00000"]["sha256"])
                installer = build_windows_release.build_payload_manifest(
                    release,
                    payload,
                    variants,
                    dx8={},
                    prism3d={},
                    game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                    dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
                    prism3d_exe=build_windows_release.default_test_base_file(Path("prism3d.exe")),
                )
                self.assertEqual(len(installer["game_variants"]), 32)
                self.assertEqual(installer["game_variants"]["10000"]["components"], ["third"])
                self.assertEqual(installer["components"]["third"]["target"], "game/sys/game.dll")

                from htmod.patch import PatchRunner, PatchState
                mods = runner_module.load_binary_mods(
                    release, ("camera", "physics", "ai", "ui_keyboard", "third")
                )
                pristine = build_windows_release.default_test_base_file(Path("game/sys/game.dll")).read_bytes()
                for name, record in variants.items():
                    with self.subTest(direct_variant=name):
                        images = {"game/sys/game.dll": bytearray(pristine)}
                        selected = [mod for mod in mods if mod.id in record["components"]]
                        runner = PatchRunner(images)
                        if selected:
                            runner.apply(selected)
                        states = runner.check(mods)
                        for mod in mods:
                            expected = PatchState.PATCHED if mod in selected else PatchState.ORIGINAL
                            self.assertEqual(states[mod.id], expected)
                        self.assertEqual(
                            hashlib.sha256(images["game/sys/game.dll"]).hexdigest(),
                            record["sha256"],
                        )
                        if selected:
                            runner.restore(selected)
                        self.assertEqual(images["game/sys/game.dll"], pristine)

    def test_three_appended_handlers_compose_and_restore_in_reverse_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                    registry_module, "BINARY_HANDLERS", registry_module.BINARY_HANDLERS | {"third"}):
                release = self.make_appended_tail_release(temporary)
                from htmod.patch import PatchRunner, PatchState
                from htmod.pe import PEImage
                mods = runner_module.load_binary_mods(release, ("camera", "ui_keyboard", "third"))
                pristine = build_windows_release.default_test_base_file(Path("game/sys/game.dll")).read_bytes()
                images = {"game/sys/game.dll": bytearray(pristine)}
                runner = PatchRunner(images)
                runner.apply(mods)
                self.assertEqual(runner.check(mods), {mod.id: PatchState.PATCHED for mod in mods})
                image = PEImage.parse(images["game/sys/game.dll"])
                self.assertEqual(
                    [section.name for section in image.sections[-3:]],
                    [".htcam", ".uitab", ".third"],
                )

                direct = Path(temporary) / "direct"
                target = direct / "game/sys/game.dll"
                target.parent.mkdir(parents=True)
                target.write_bytes(images["game/sys/game.dll"])
                recognized = runner_module.runner_from_directory(direct, mods)
                self.assertEqual(recognized.images["game/sys/game.dll"], target.read_bytes())
                target.write_bytes(target.read_bytes() + b"unknown suffix")
                with self.assertRaisesRegex(Exception, "whole-file|unrecognized|cannot restore"):
                    runner_module.runner_from_directory(direct, mods)

                before = images["game/sys/game.dll"][:]
                with self.assertRaisesRegex(Exception, "last section"):
                    PatchRunner(images).restore([mods[1]])
                self.assertEqual(images["game/sys/game.dll"], before)
                self.assertEqual(runner.check(mods), {mod.id: PatchState.PATCHED for mod in mods})
                runner.restore(mods)
                self.assertEqual(images["game/sys/game.dll"], pristine)

    def test_three_appended_handlers_refuse_foreign_tail_data_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                    registry_module, "BINARY_HANDLERS", registry_module.BINARY_HANDLERS | {"third"}):
                release = self.make_appended_tail_release(temporary)
                import struct
                from htmod.patch import PatchRunner, PatchState, StateError
                from htmod.pe import PEImage, align
                mods = runner_module.load_binary_mods(release, ("camera", "ui_keyboard", "third"))
                pristine = build_windows_release.default_test_base_file(Path("game/sys/game.dll")).read_bytes()
                patched = bytearray(pristine)
                PatchRunner({"game/sys/game.dll": patched}).apply(mods)
                for mutation in (
                    "camera-payload", "ui-payload", "third-payload", "nonzero-padding",
                    "noncanonical-offsets", "trailing",
                ):
                    with self.subTest(mutation=mutation):
                        candidate = patched[:]
                        image = PEImage.parse(candidate)
                        if mutation.endswith("-payload"):
                            name = {
                                "camera-payload": ".htcam",
                                "ui-payload": ".uitab",
                                "third-payload": ".third",
                            }[mutation]
                            section = image.section(name)
                            candidate[section.raw_offset] ^= 0xFF
                        elif mutation == "nonzero-padding":
                            section = image.section(".third")
                            candidate[section.raw_offset:section.raw_offset] = (
                                b"\x01" + bytes(image.file_alignment - 1)
                            )
                            header = image.section_table + 40 * (len(image.sections) - 1)
                            struct.pack_into(
                                "<I", candidate, header + 20,
                                section.raw_offset + image.file_alignment,
                            )
                        elif mutation == "noncanonical-offsets":
                            section = image.section(".third")
                            header = image.section_table + 40 * (len(image.sections) - 1)
                            virtual_address = section.virtual_address + image.section_alignment
                            struct.pack_into("<I", candidate, header + 12, virtual_address)
                            struct.pack_into(
                                "<I", candidate, image.optional_offset + 56,
                                align(virtual_address + section.virtual_size, image.section_alignment),
                            )
                        else:
                            candidate += b"foreign"
                        before = candidate[:]
                        runner = PatchRunner({"game/sys/game.dll": candidate})
                        states = runner.check(mods)
                        if mutation.endswith("-payload"):
                            foreign = {
                                "camera-payload": "camera",
                                "ui-payload": "ui_keyboard",
                                "third-payload": "third",
                            }[mutation]
                            for mod in mods:
                                expected = PatchState.FOREIGN if mod.id == foreign else PatchState.PATCHED
                                self.assertEqual(states[mod.id], expected)
                        else:
                            self.assertEqual(states, {mod.id: PatchState.FOREIGN for mod in mods})
                        with self.assertRaises(StateError):
                            runner.restore(mods)
                        self.assertEqual(candidate, before)

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
            with mock.patch.object(build_windows_release, "load_release", return_value=release):
                variants = build_windows_release.build_game_variants(
                    build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
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
                )

            from htmod import cli as cli_module
            with mock.patch("htmod.cli.load_release", return_value=release):
                result = cli_module.main(["check", "--game-dir", str(temporary), "--mod", "camera"])
            self.assertEqual(result, 1)

    def test_variant_width_and_component_bits_must_match_registered_game_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                    registry_module, "BINARY_HANDLERS", registry_module.BINARY_HANDLERS | {"third"}):
                release = self.make_appended_tail_release(temporary)
                release_path = release.root / "release.json"
                value = json.loads(release_path.read_text(encoding="utf-8"))
                value["game_variants"]["001"] = {"file": "game-001.dll", "components": []}
                release_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(Exception, "bit length"):
                    load_release(release.root.parent)
                value["game_variants"].pop("001")
                value["game_variants"]["10000"]["components"] = ["camera"]
                release_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(Exception, "does not match component bits"):
                    load_release(release.root.parent)


if __name__ == "__main__":
    unittest.main()
