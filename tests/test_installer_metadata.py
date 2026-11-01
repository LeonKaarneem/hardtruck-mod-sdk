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
    "UNOFFICIAL MOD - Hard Truck Modern 0.3.0<br>"
    "Better physics, cameras and handling<br>"
    "Improved economy and AI cleanup<br>"
    "Features depend on Setup choices"
)
QUICK_SETUP_BLOCK = (
    '\tspawn txt_common {\r\n'
    '\t\tlook:"25"\r\n'
    f'\t\tvalue:"{QUICK_SETUP_VALUE}"\r\n'
    '\t\tarea:"262.000000 625.000000 762.000000 735.000000"\r\n'
    '\t\tcoords:"262.000000 625.000000 762.000000 735.000000"\r\n'
    '\t}\r\n'
).encode("utf-8")
QUICK_SETUP_ENABLED_VALUE = (
    "UNOFFICIAL MOD - Hard Truck Modern 0.3.0<br>"
    "Enabled: configuration, camera, physics,<br>"
    "handling, economy, driver, AI,<br>"
    "keyboard, frame cap, DX8 vsync"
)
QUICK_SETUP_ENABLED_BLOCK = QUICK_SETUP_BLOCK.replace(
    QUICK_SETUP_VALUE.encode("utf-8"),
    QUICK_SETUP_ENABLED_VALUE.encode("utf-8"),
)
STOCK_QUICK_SETUP_SHA256 = "2506947443d594a16cc4f34f9aa5186d4cb838fe1c1c6aae64ed83cb96cb2f62"
CREDITS = ROOT / "modpack/mods/config/files/ui/mcredits.sc"
STOCK_CREDITS = ROOT / "data/patch_zipfs/ui/mcredits.sc"
CREDITS_BLOCK = (
    '\t\t\tspawn txt_common {\r\n'
    '\t\t\t\tlook:"3"\r\n'
    '\t\t\t\tvalue:"Hard Truck Modern"\r\n'
    '\t\t\t\tarea:"306.000000 680.000000 718.000000 712.000000"\r\n'
    '\t\t\t\tcoords:"306.000000 680.000000 718.000000 712.000000"\r\n'
    '\t\t\t}\r\n'
    '\t\t\tspawn txt_common {\r\n'
    '\t\t\t\tlook:"22"\r\n'
    '\t\t\t\tvalue:"MOD SDK: LeonKaarneem"\r\n'
    '\t\t\t\tarea:"306.000000 716.000000 717.000000 748.000000"\r\n'
    '\t\t\t\tcoords:"306.000000 716.000000 717.000000 748.000000"\r\n'
    '\t\t\t}\r\n'
).encode("ascii")


class GeneratedInstallerMetadataTests(unittest.TestCase):
    def test_schema_three_carries_ordered_setup_ownership_and_curated_variants(self):
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            data_components = build_windows_release.copy_data(release, payload)
            manifest = build_windows_release.build_payload_manifest(
                release,
                payload,
                game_variants={},
                dx8={},
                prism3d={},
                game_dll=build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                dx8_dll=build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
                prism3d_exe=build_windows_release.default_test_base_file(Path("prism3d.exe")),
            )

        self.assertEqual(manifest["schema_version"], 3)
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
        self.assertNotIn("legacy_game_sha256", manifest)
        self.assertEqual(manifest["dx8"], {})
        self.assertEqual(manifest["prism3d"], {})

    def test_generated_release_manifest_is_schema_three_with_all_curated_variants(self):
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload"
            payload.mkdir()
            variants = build_windows_release.build_game_variants(
                build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
            )
            dx8 = build_windows_release.build_dx8_payloads(
                build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
            )
            prism3d = build_windows_release.build_prism3d_payloads(
                build_windows_release.default_test_base_file(Path("prism3d.exe")),
            )
            build_windows_release.copy_data(release, payload)
            manifest = build_windows_release.build_payload_manifest(
                release,
                payload,
                variants,
                dx8,
                prism3d,
                build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
                build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
                build_windows_release.default_test_base_file(Path("prism3d.exe")),
            )
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(list(manifest["components"]), list(release.mods))
        self.assertEqual(
            {name: record["components"] for name, record in manifest["game_variants"].items()},
            {name: list(record.components) for name, record in release.game_variants.items()},
        )
        self.assertEqual(manifest["data_components"]["config"], [
            "data/config.cfg",
            "data/defs/ui/btn.def",
            "data/defs/ui/txt.def",
            "data/ui/mmain.sc",
            "data/ui/cosetup.sc",
            "data/ui/moptions.sc",
            "data/ui/options/mgraphics.sc",
            "data/ui/options/mcontrols.sc",
            "data/ui/mcredits.sc",
        ])


class WindowsLifecyclePlanContractTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "tools/templates/Setup.ps1").read_text(encoding="utf-8")

    def test_helper_validates_artifacts_and_invokes_one_exact_plan(self):
        self.assertIn("function Invoke-LifecyclePlan", self.source)
        start = self.source.index("function Invoke-LifecyclePlan")
        end = self.source.index("\nfunction ", start + 1)
        helper = self.source[start:end]
        self.assertIn('"plan",', helper)
        self.assertIn('"--exact"', helper)
        self.assertIn('Test-Hash $patcher', helper)
        self.assertIn('Test-Hash $catalog', helper)
        self.assertIn("ConvertFrom-Json", helper)

    def test_install_consumes_plan_selection_and_binary_changes(self):
        start = self.source.index("function Install-Mods")
        end = self.source.index("\nfunction ", start + 1)
        install = self.source[start:end]
        self.assertNotIn("Invoke-LifecyclePlan", install)
        self.assertIn("$plan.selected", install)
        self.assertIn("$plan.binary_changes", install)
        self.assertNotIn("$selectedGameComponents", install)
        self.assertNotIn("$Manifest.game_variants", install)

    def test_powershell_dependency_and_curated_selection_policy_is_removed(self):
        self.assertNotIn("function Assert-Selection", self.source)
        self.assertNotIn("No unique curated game.dll variant", self.source)


class CreditsTests(unittest.TestCase):
    def test_stock_patch_12_credits_are_preserved_except_for_five_page_blocks(self):
        self.assertTrue(CREDITS.is_file(), "config-owned Credits override is missing")
        content = CREDITS.read_bytes()
        self.assertEqual(content.count(CREDITS_BLOCK), 5)
        self.assertEqual(content.replace(CREDITS_BLOCK, b""), STOCK_CREDITS.read_bytes())
        self.assertGreater(content.count(b"\r\n"), 0)
        self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))
        for identifier in range(3000, 3005):
            group_start = content.index(
                f'\t\tspawn ui_group {{\r\n\t\t\tid:"{identifier}"'.encode("ascii")
            )
            group_end = content.index(b"\r\n\t\t}\r\n", group_start) + len(b"\r\n\t\t}\r\n")
            group = content[group_start:group_end]
            self.assertEqual(group.count(CREDITS_BLOCK), 1, identifier)
        self.assertEqual(content.count(b'value:"Hard Truck Modern"'), 5)
        self.assertEqual(content.count(b'value:"MOD SDK: LeonKaarneem"'), 5)

    def test_config_owns_and_materializes_exact_credits_bytes(self):
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            components = materialize_data(release, payload)
            generated = payload / "data/ui/mcredits.sc"
            self.assertTrue(generated.is_file())
            self.assertEqual(generated.read_bytes(), CREDITS.read_bytes())
            self.assertIn("data/ui/mcredits.sc", components["config"])


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
        stock = stock.replace(
            b'\t\tarea:"429.000000 556.000000 604.000000 600.000000"',
            b'\t\tarea:"449.000000 566.000000 584.000000 590.000000"',
        )
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
            content = generated.read_bytes()
            self.assertEqual(
                content,
                QUICK_SETUP.read_bytes().replace(QUICK_SETUP_BLOCK, QUICK_SETUP_ENABLED_BLOCK),
            )
            self.assertEqual(content.count(QUICK_SETUP_ENABLED_BLOCK), 1)
            self.assertNotIn(b"Features depend on Setup choices", content)
            self.assertIn("data/ui/cosetup.sc", components["config"])


if __name__ == "__main__":
    unittest.main()
