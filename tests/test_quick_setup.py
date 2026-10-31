import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.errors import PatchError
from htmod.quick_setup import QUICK_SETUP_DESTINATION, render_quick_setup
from htmod.runner import materialize_data_bytes
from tests.test_installer_metadata import (
    QUICK_SETUP,
    QUICK_SETUP_BLOCK,
    QUICK_SETUP_ENABLED_BLOCK,
    QUICK_SETUP_VALUE,
)


class QuickSetupRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = load_release(ROOT)
        cls.template = QUICK_SETUP.read_bytes()

    def test_full_selection_has_exact_three_enabled_lines(self):
        content = render_quick_setup(self.template, tuple(self.release.mods))
        self.assertEqual(
            content,
            self.template.replace(QUICK_SETUP_BLOCK, QUICK_SETUP_ENABLED_BLOCK),
        )
        self.assertIn(
            b"Enabled: configuration, camera, physics,<br>"
            b"handling, economy, driver, AI,<br>"
            b"keyboard, DX8 vsync",
            content,
        )
        self.assertNotIn(b"Features depend on Setup choices", content)

    def test_config_and_keyboard_use_one_compact_line(self):
        content = render_quick_setup(self.template, ("ui_keyboard", "config"))
        self.assertIn(b'value:"UNOFFICIAL MOD - Hard Truck Modern 0.2.0<br>', content)
        self.assertIn(b"<br>Enabled: configuration, keyboard\"\r\n", content)
        self.assertNotIn(b"<br>Enabled: configuration, keyboard<br>", content)

    def test_custom_selection_is_ordered_and_future_ids_have_safe_fallbacks(self):
        content = render_quick_setup(
            self.template,
            ("economy_def", "future_mod", "camera", "config"),
        )
        self.assertIn(
            b"<br>Enabled: configuration, camera, economy,<br>future mod\"\r\n",
            content,
        )
        self.assertNotIn(b"future_mod", content)
        with self.assertRaisesRegex(PatchError, "unsafe"):
            render_quick_setup(self.template, ("config", 'bad"><br>injected'))

    def test_long_future_fallbacks_are_truncated_to_existing_line_widths(self):
        content = render_quick_setup(
            self.template,
            ("future_" + "a" * 80, "future_" + "b" * 80),
        )
        value = content.split(b'\t\tvalue:"', 1)[1].split(b'"\r\n', 1)[0]
        lines = value.split(b"<br>")[1:]
        self.assertEqual(len(lines), 2)
        self.assertEqual([len(line) for line in lines], [40, 30])
        self.assertTrue(lines[0].endswith(b"...,"))
        self.assertTrue(lines[1].endswith(b"..."))
        self.assertLessEqual(len(lines[0]), 40)
        self.assertLessEqual(len(lines[1]), 30)
        content.decode("ascii")

    def test_renderer_preserves_crlf_panel_coordinates_and_all_other_bytes(self):
        content = render_quick_setup(self.template, ("config", "ui_keyboard"))
        self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))
        expected_value = (
            b"UNOFFICIAL MOD - Hard Truck Modern 0.2.0<br>"
            b"Enabled: configuration, keyboard"
        )
        before, separator, after = self.template.partition(QUICK_SETUP_VALUE.encode("utf-8"))
        self.assertTrue(separator)
        self.assertEqual(content, before + expected_value + after)
        self.assertEqual(content.count(b"<br>"), self.template.count(b"<br>") - 2)
        self.assertIn(b'\t\tlook:"25"\r\n', content)
        self.assertIn(b'\t\tarea:"262.000000 625.000000 762.000000 735.000000"\r\n', content)
        self.assertIn(b'\t\tcoords:"262.000000 625.000000 762.000000 735.000000"\r\n', content)

    def test_materialization_uses_selected_membership(self):
        components = materialize_data_bytes(self.release, ("ui_keyboard", "config"))
        content = components["config"][QUICK_SETUP_DESTINATION]
        self.assertIn(b"<br>Enabled: configuration, keyboard\"\r\n", content)
        self.assertNotIn(b"camera", content.split(b"Enabled:", 1)[1])


class QuickSetupCliTests(unittest.TestCase):
    def test_render_operation_writes_selected_bytes_and_actual_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "safe staging"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/htmod.py"),
                    "--root",
                    str(ROOT),
                    "render-quick-setup",
                    "--output-dir",
                    str(output),
                    "--mod",
                    "ui_keyboard",
                    "--mod",
                    "config",
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rendered = output / QUICK_SETUP_DESTINATION
            content = rendered.read_bytes()
            self.assertIn(b"<br>Enabled: configuration, keyboard\"\r\n", content)
            digest = hashlib.sha256(content).hexdigest()
            self.assertEqual(result.stdout.strip(), f"{QUICK_SETUP_DESTINATION}: {digest}")

    def test_invalid_selection_and_unsafe_output_do_not_create_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            cases = (
                ("unknown", output, "missing"),
                ("unsafe", root / "regular-file", "config"),
            )
            (root / "regular-file").write_bytes(b"preserve")
            for name, destination, mod_id in cases:
                with self.subTest(case=name):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "tools/htmod.py"),
                            "--root",
                            str(ROOT),
                            "render-quick-setup",
                            "--output-dir",
                            str(destination),
                            "--mod",
                            mod_id,
                        ],
                        text=True,
                        capture_output=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertFalse((destination / QUICK_SETUP_DESTINATION).exists())
            self.assertEqual((root / "regular-file").read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
