import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.runner import materialize_data
from tools import build_windows_release


QUICK_SETUP = ROOT / "modpack/mods/config/files/ui/cosetup.sc"
QUICK_SETUP_VALUE = (
    "UNOFFICIAL MOD - Hard Truck Modern v3<br>"
    "Better physics, cameras and handling<br>"
    "Improved economy and AI cleanup<br>"
    "Features depend on Setup choices"
)
QUICK_SETUP_BLOCK = (
    '\tspawn txt_common {\r\n'
    '\t\tlook:"3"\r\n'
    f'\t\tvalue:"{QUICK_SETUP_VALUE}"\r\n'
    '\t\tarea:"262.000000 625.000000 762.000000 735.000000"\r\n'
    '\t\tcoords:"262.000000 625.000000 762.000000 735.000000"\r\n'
    '\t}\r\n'
).encode("utf-8")
STOCK_QUICK_SETUP_SHA256 = "2506947443d594a16cc4f34f9aa5186d4cb838fe1c1c6aae64ed83cb96cb2f62"


class GeneratedInstallerMetadataTests(unittest.TestCase):
    def test_schema_two_carries_ordered_setup_ownership_and_curated_variants(self):
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            data_components = build_windows_release.copy_data(release, payload)
            manifest = build_windows_release.build_payload_manifest(
                release,
                payload,
                game_variants={},
                dx8={},
                game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
            )

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(list(manifest["components"]), list(release.mods))
        for component_id, mod in release.mods.items():
            with self.subTest(component=component_id):
                component = manifest["components"][component_id]
                self.assertEqual(component["label"], mod.setup["label"])
                self.assertEqual(component["description"], mod.setup["description"])
                self.assertEqual(component["default"], mod.setup["default"])
                self.assertEqual(component["requires"], list(mod.requires))
                self.assertEqual(component["conflicts"], list(mod.conflicts))
                self.assertEqual(component["new_profile"], mod.new_profile)
                self.assertEqual(component["kind"], mod.kind)
                self.assertEqual(component["target"], mod.target)
        self.assertEqual(manifest["data_components"], data_components)
        self.assertIn("data/ui/cosetup.sc", manifest["data_components"]["config"])
        self.assertEqual(manifest["game_variants"], {})
        self.assertEqual(manifest["dx8"], {})

    def test_generated_release_manifest_is_schema_two_with_all_curated_variants(self):
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload"
            work = Path(temporary) / "work"
            payload.mkdir()
            work.mkdir()
            variants = build_windows_release.build_game_variants(
                build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                payload,
                work,
            )
            dx8 = build_windows_release.build_dx8_payloads(
                build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
                payload,
                work,
            )
            build_windows_release.copy_data(release, payload)
            manifest = build_windows_release.build_payload_manifest(
                release,
                payload,
                variants,
                dx8,
                build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
            )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(list(manifest["components"]), list(release.mods))
        self.assertEqual(
            {name: record["components"] for name, record in manifest["game_variants"].items()},
            {name: list(record.components) for name, record in release.game_variants.items()},
        )
        self.assertEqual(manifest["data_components"]["config"], [
            "data/config.cfg",
            "data/ui/mmain.sc",
            "data/ui/cosetup.sc",
            "data/ui/moptions.sc",
            "data/ui/options/mgraphics.sc",
            "data/ui/options/mcontrols.sc",
        ])


class QuickSetupTests(unittest.TestCase):
    def test_stock_patch_12_file_is_preserved_except_for_native_root_panel(self):
        self.assertTrue(QUICK_SETUP.is_file(), "config-owned Quick Setup override is missing")
        content = QUICK_SETUP.read_bytes()
        self.assertEqual(content.count(QUICK_SETUP_BLOCK), 1)
        self.assertGreater(content.count(b"\r\n"), 0)
        self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))
        self.assertNotIn(b"\n\tspawn txt_common {\n", content)
        stock = content.replace(QUICK_SETUP_BLOCK, b"")
        stock = stock.replace(b'\t\ttab:"1"\r\n\t\tid:"10"', b'\t\tid:"10"')
        stock = stock.replace(b'\t\ttab:"2"\r\n\t\tid:"11"', b'\t\tid:"11"')
        stock = stock.replace(b'\t\ttab:"3"\r\n\t\tid:"1"', b'\t\ttab:"8"\r\n\t\tid:"1"')
        self.assertEqual(hashlib.sha256(stock).hexdigest(), STOCK_QUICK_SETUP_SHA256)
        frame_end = stock.index(b"\t}\r\n", stock.index(b'\tspawn frm_common {')) + len(b"\t}\r\n")
        self.assertEqual(content[frame_end:frame_end + len(QUICK_SETUP_BLOCK)], QUICK_SETUP_BLOCK)

    def test_materialized_config_owns_exact_quick_setup_bytes(self):
        self.assertTrue(QUICK_SETUP.is_file(), "config-owned Quick Setup override is missing")
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            components = materialize_data(release, payload)
            generated = payload / "data/ui/cosetup.sc"
            self.assertTrue(generated.is_file())
            self.assertEqual(generated.read_bytes(), QUICK_SETUP.read_bytes())
            self.assertIn("data/ui/cosetup.sc", components["config"])


if __name__ == "__main__":
    unittest.main()
