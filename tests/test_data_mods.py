import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.runner import materialize_data


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GoldenDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(
            (ROOT / "tests/golden/v3.json").read_text(encoding="utf-8")
        )
        cls.release = load_release(ROOT)

    def test_golden_covers_sixteen_variants_dx8_and_pristine_sources(self):
        variants = self.golden["game_variants"]
        self.assertEqual(list(variants), [f"{n:04b}" for n in range(16)])
        self.assertEqual(len({item["sha256"] for item in variants.values()}), 16)
        self.assertEqual(variants["0000"]["sha256"], self.golden["pristine"]["game_dll"])
        self.assertEqual(self.golden["dx8"]["stock"], self.golden["pristine"]["dx8_dll"])
        self.assertNotEqual(self.golden["dx8"]["stock"], self.golden["dx8"]["vsync"])
        for name, expected in variants.items():
            with self.subTest(variant=name):
                self.assertEqual(
                    self.release.game_variants[name].components,
                    tuple(expected["components"]),
                )

    def test_source_data_hashes_and_crlf_are_preserved(self):
        records = [
            (mod, record)
            for mod in self.release.mods.values()
            for record in mod.files
        ]
        self.assertEqual(len(records), len(self.golden["source_data"]))
        for mod, record in records:
            relative = record.destination.removeprefix("game/usr/")
            with self.subTest(path=relative):
                source = mod.source_path(record)
                self.assertEqual(sha256(source), self.golden["source_data"][relative])
                content = source.read_bytes()
                self.assertGreater(content.count(b"\r\n"), 0)
                self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))

    def test_materialized_payload_matches_golden_hashes_and_component_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            components = materialize_data(self.release, payload)
            self.assertEqual(components, self.golden["data_components"])
            actual = {
                path.relative_to(payload).as_posix(): sha256(path)
                for path in payload.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, self.golden["data_payloads"])

    def test_config_transform_removes_renderer_and_matches_legacy_line_endings(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            materialize_data(self.release, payload)
            content = (payload / "data/config.cfg").read_bytes()
            self.assertNotIn(b"r_renderer", content.lower())
            self.assertEqual(content.count(b"\r\n"), 0)
            self.assertGreater(content.count(b"\n"), 0)
            source = self.release.mods["config"].source_path(
                self.release.mods["config"].files[0]
            ).read_bytes()
            self.assertEqual(source.count(b"\n"), source.count(b"\r\n"))
            self.assertEqual(content.count(b"\n"), source.count(b"\n") - 1)

    def test_main_menu_marker_bytes_are_unchanged(self):
        marker = self.golden["marker"]
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            materialize_data(self.release, payload)
            content = (payload / marker["file"]).read_bytes()
            self.assertIn(marker["block_crlf"].encode("utf-8"), content)

    def test_release_builder_consumes_manifest_data(self):
        from tools import build_windows_release

        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            components = build_windows_release.copy_data(self.release, payload)
            self.assertEqual(components, self.golden["data_components"])
            actual = {
                path.relative_to(payload).as_posix(): sha256(path)
                for path in payload.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, self.golden["data_payloads"])


if __name__ == "__main__":
    unittest.main()
