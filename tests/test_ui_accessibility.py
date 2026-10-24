import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.runner import materialize_data
import tempfile


UI_ROOT = ROOT / "modpack/mods/config/files/ui"
EXPECTED_TABS = {
    "mmain.sc": {
        "2": 1,
        "3": 2,
        "4": 3,
        "5": 4,
        "10": 5,
        "11": 6,
        "1": 7,
    },
    "cosetup.sc": {
        "10": 1,
        "11": 2,
        "1": 3,
    },
    "moptions.sc": {
        "10": 1,
        "11": 2,
        "12": 3,
        "3": 5,
        "1": 6,
        "2": 7,
    },
    "options/mgraphics.sc": {
        "501": 1,
        "502": 2,
        "503": 3,
        "506": 4,
        "504": 5,
        "505": 6,
        "1": 101,
        "2": 102,
        "3": 103,
        "4": 104,
        "5": 105,
        "6": 106,
        "7": 107,
        "16": 108,
        "17": 109,
        "18": 110,
    },
}
CONTROLS_BASE_TABS = {"2": 1, "3": 2, "4": 3, "6": 4}
CONTROLS_JOYSTICK_TABS = {
    "700": 1,
    "701": 2,
    "704": 3,
    "705": 4,
    "702": 5,
    "706": 6,
    "503": 7,
    "707": 8,
    "513": 9,
    "708": 10,
    "523": 11,
}
CONFIG_UI_DESTINATIONS = [
    "data/ui/mmain.sc",
    "data/ui/cosetup.sc",
    "data/ui/moptions.sc",
    "data/ui/options/mgraphics.sc",
    "data/ui/options/mcontrols.sc",
]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def controls(relative):
    path = UI_ROOT / relative
    if not path.is_file():
        return []
    content = path.read_bytes()
    result = []
    pattern = re.compile(
        rb'spawn\s+(btn_common|inp_common|sld_common)\s*\{(.*?)\r?\n\s*\}',
        re.DOTALL,
    )
    for match in pattern.finditer(content):
        body = match.group(2)
        identifier = re.search(rb'(?m)^\s*id:"([0-9]+)"', body)
        tab = re.search(rb'(?m)^\s*tab:"(-?[0-9]+)"', body)
        result.append({
            "class": match.group(1).decode("ascii"),
            "id": identifier.group(1).decode("ascii") if identifier else None,
            "tab": int(tab.group(1)) if tab else None,
        })
    return result


class ControlMap(dict):
    def __missing__(self, key):
        return {"tab": None}


def by_id(relative):
    return ControlMap({control["id"]: control for control in controls(relative)})


class NativeTabOrderTests(unittest.TestCase):
    def test_main_quick_setup_options_and_graphics_tabs(self):
        for relative, expected in EXPECTED_TABS.items():
            with self.subTest(script=relative):
                actual = by_id(relative)
                self.assertEqual(
                    {identifier: actual[identifier]["tab"] for identifier in expected},
                    expected,
                )

    def test_static_controls_base_tabs_are_unique_and_ascending(self):
        actual = by_id("options/mcontrols.sc")
        self.assertEqual(
            {identifier: actual[identifier]["tab"] for identifier in CONTROLS_BASE_TABS},
            CONTROLS_BASE_TABS,
        )
        self.assertEqual(
            [actual[identifier]["tab"] for identifier in CONTROLS_BASE_TABS],
            [1, 2, 3, 4],
        )

    def test_static_controls_joystick_tabs_are_unique_and_ascending(self):
        actual = by_id("options/mcontrols.sc")
        self.assertEqual(
            {identifier: actual[identifier]["tab"] for identifier in CONTROLS_JOYSTICK_TABS},
            CONTROLS_JOYSTICK_TABS,
        )
        self.assertEqual(
            [actual[identifier]["tab"] for identifier in CONTROLS_JOYSTICK_TABS],
            list(range(1, 12)),
        )

    def test_dynamic_binding_template_has_no_generated_row_tab_claim(self):
        actual = by_id("options/mcontrols.sc")
        self.assertIsNone(actual["91"]["tab"])


class NativePreservationTests(unittest.TestCase):
    def test_scripts_are_crlf_and_match_exact_goldens(self):
        golden = json.loads(
            (ROOT / "tests/golden/ui_accessibility.json").read_text(encoding="utf-8")
        )
        actual = {}
        for relative in EXPECTED_TABS.keys() | {"options/mcontrols.sc"}:
            path = UI_ROOT / relative
            if not path.is_file():
                actual[relative] = None
                continue
            content = path.read_bytes()
            with self.subTest(script=relative):
                self.assertGreater(content.count(b"\r\n"), 0)
                self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))
            actual[relative] = sha256(path)
        self.assertEqual(actual, golden["files"])

    def test_config_manifest_owns_and_materializes_every_native_script(self):
        release = load_release(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            components = materialize_data(release, Path(temporary))
        self.assertEqual(components["config"], ["data/config.cfg", *CONFIG_UI_DESTINATIONS])


if __name__ == "__main__":
    unittest.main()
