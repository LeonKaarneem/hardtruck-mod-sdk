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
DEF_ROOT = ROOT / "modpack/mods/config/files/defs/ui"
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
    "data/ui/mcredits.sc",
]
NATIVE_FILES = {
    **{relative: UI_ROOT / relative for relative in EXPECTED_TABS},
    "options/mcontrols.sc": UI_ROOT / "options/mcontrols.sc",
    "defs/ui/btn.def": DEF_ROOT / "btn.def",
    "defs/ui/txt.def": DEF_ROOT / "txt.def",
}


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
        look = re.search(rb'(?m)^\s*look:"([0-9]+)"', body)
        area = re.search(rb'(?m)^\s*area:"([^"]+)"', body)
        coords = re.search(rb'(?m)^\s*coords:"([^"]+)"', body)
        result.append({
            "class": match.group(1).decode("ascii"),
            "id": identifier.group(1).decode("ascii") if identifier else None,
            "tab": int(tab.group(1)) if tab else None,
            "look": int(look.group(1)) if look else 0,
            "area": area.group(1).decode("ascii") if area else None,
            "coords": coords.group(1).decode("ascii") if coords else None,
        })
    return result


def definitions(name):
    path = DEF_ROOT / name
    if not path.is_file():
        return {}
    return {
        int(match.group(1)): match.group(2)
        for match in re.finditer(
            rb'(?m)^def template([0-9]+) \{(.*?)\r\n\}',
            path.read_bytes(),
            re.DOTALL,
        )
    }


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


class NativeVisualAccessibilityTests(unittest.TestCase):
    def test_accessible_chrome_is_appended_as_look21_with_native_state_cues(self):
        templates = definitions("btn.def")
        self.assertEqual(list(templates), list(range(22)))
        accessible = templates.get(21, b"")
        self.assertIn(b'name:"Accessible Chrome Long"', accessible)
        states = {}
        for state in ("n", "s", "s2", "p", "d"):
            match = re.search(rb'(?m)^\s*' + state.encode() + rb'_pml:"([^"]+)"', accessible)
            self.assertIsNotNone(match, state)
            states[state] = match.group(1) if match else b""
            self.assertIn(b"xscale=0.5 yscale=0.5", states[state])
            self.assertIn(b"<color value=ff000000>", states[state])
            self.assertIn(b"ui_small.font", states[state])
            self.assertIn(b"but01", states[state])
        self.assertIn(b">%0</font>", states["n"])
        self.assertIn(b"> %0</font>", states["s"])
        self.assertIn(b"> %0</font>", states["s2"])
        self.assertIn(b"[x] %0</font>", states["p"])
        for state, cue in (("s", "> %0"), ("s2", "> %0"), ("p", "[x] %0")):
            with self.subTest(state=state, cue_layers=3):
                self.assertEqual(states[state].count(cue.encode()), 3)
        self.assertIn(b'width:"256"', accessible)
        self.assertIn(b'height:"64"', accessible)
        self.assertIn(b'areaoff:"34 16 34 16"', accessible)

    def test_accessible_panel_is_appended_as_look25_with_white_on_dark(self):
        templates = definitions("txt.def")
        self.assertEqual(list(templates), list(range(26)))
        accessible = templates.get(25, b"")
        self.assertIn(b'name:"Accessible Panel"', accessible)
        self.assertIn(b"backg1.shd color=c0000000 xscale=stretch yscale=stretch", accessible)
        self.assertIn(b"xscale=0.7 yscale=0.7", accessible)
        self.assertIn(b"<color value=ffffffff>%0</font>", accessible)
        self.assertIn(b"hstyle=center vstyle=center", accessible)

    def test_stock_definition_bytes_are_preserved_before_appended_looks(self):
        for name, ordinal, expected in (
            ("btn.def", 21, "e990f429b291d16e5990beada3dfd72d608e8933e504b68bde67f754f149e920"),
            ("txt.def", 25, "297a26ff78233126032ff453a59a3bdb4830ee0242a7097882b9cf215da125f8"),
        ):
            with self.subTest(definition=name):
                content = (DEF_ROOT / name).read_bytes() if (DEF_ROOT / name).is_file() else b""
                stock, separator, _ = content.rpartition(
                    b"\r\ndef template" + f"{ordinal:02d}".encode() + b" {"
                )
                self.assertTrue(separator)
                self.assertEqual(hashlib.sha256(stock).hexdigest(), expected)

    def test_look21_is_opted_in_only_for_main_six_and_options_pages(self):
        expected = {
            *(('mmain.sc', identifier) for identifier in ("2", "3", "4", "5", "10", "11")),
            *(('moptions.sc', identifier) for identifier in ("10", "11", "12")),
        }
        actual = {
            (path.relative_to(UI_ROOT).as_posix(), control["id"])
            for path in UI_ROOT.rglob("*.sc")
            for control in controls(path.relative_to(UI_ROOT).as_posix())
            if control["look"] == 21
        }
        self.assertEqual(actual, expected)
        self.assertEqual(by_id("mmain.sc")["1"]["look"], 5)

    def test_look25_is_opted_in_only_for_quick_setup_info(self):
        content = (UI_ROOT / "cosetup.sc").read_bytes()
        info = re.search(
            rb'spawn txt_common \{(?=[^}]*value:"UNOFFICIAL MOD)(.*?)\r\n\t\}',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(info)
        self.assertIn(b'look:"25"', info.group(1) if info else b"")
        total = sum(path.read_bytes().count(b'look:"25"') for path in UI_ROOT.rglob("*.sc"))
        self.assertEqual(total, 1)

    def test_hit_areas_grow_without_moving_visual_coords(self):
        expected = {
            "2": ("34.000000 156.000000 242.000000 204.000000", "0.000000 140.000000 256.000000 204.000000"),
            "3": ("34.000000 207.000000 242.000000 255.000000", "0.000000 191.000000 256.000000 255.000000"),
            "4": ("34.000000 259.000000 242.000000 307.000000", "0.000000 243.000000 256.000000 307.000000"),
            "5": ("34.000000 353.000000 242.000000 401.000000", "0.000000 337.000000 256.000000 401.000000"),
            "10": ("34.000000 411.000000 242.000000 459.000000", "0.000000 395.000000 256.000000 459.000000"),
            "11": ("34.000000 461.000000 242.000000 509.000000", "0.000000 453.000000 256.000000 517.000000"),
        }
        actual = by_id("mmain.sc")
        for identifier, (area, coords) in expected.items():
            with self.subTest(button=identifier):
                self.assertEqual(actual[identifier]["area"], area)
                self.assertEqual(actual[identifier]["coords"], coords)
        quick_ok = by_id("cosetup.sc")["1"]
        self.assertEqual(quick_ok["area"], "429.000000 556.000000 604.000000 600.000000")
        self.assertEqual(quick_ok["coords"], "449.000000 566.000000 584.000000 590.000000")

    def test_neighboring_main_hit_areas_do_not_overlap(self):
        actual = by_id("mmain.sc")
        rectangles = {
            identifier: tuple(map(float, actual[identifier]["area"].split()))
            for identifier in ("2", "3", "4", "5", "10", "11", "1")
        }
        for first, second in zip(rectangles, list(rectangles)[1:]):
            with self.subTest(first=first, second=second):
                self.assertLessEqual(
                    rectangles[first][3],
                    rectangles[second][1],
                    f"{first} hit area overlaps {second}",
                )
        self.assertEqual(actual["1"]["area"], "94.000000 515.000000 158.000000 579.000000")
        self.assertEqual(actual["1"]["coords"], "94.000000 515.000000 158.000000 579.000000")


class NativePreservationTests(unittest.TestCase):
    def test_scripts_are_crlf_and_match_exact_goldens(self):
        golden = json.loads(
            (ROOT / "tests/golden/ui_accessibility.json").read_text(encoding="utf-8")
        )
        actual = {}
        for relative, path in NATIVE_FILES.items():
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
        self.assertEqual(
            components["config"],
            ["data/config.cfg", "data/defs/ui/btn.def", "data/defs/ui/txt.def", *CONFIG_UI_DESTINATIONS],
        )


if __name__ == "__main__":
    unittest.main()
