import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.cli import stage
from htmod.lifecycle import build_plan, inspect_status
from htmod.migration import load_catalog, normalize_to_pristine
from htmod.patch import PatchRunner, PatchState, StateError
from htmod.pe import PEImage
from htmod.runner import load_binary_mods
from tools import build_windows_release

TARGET = "prism3d.exe"
PRISTINE = "5a8f94da924b05620e2a69daa09682dae3cab6b301ddc463b54f5750b514bddd"
ESTONIAN = "4f7b19abd89dca5c993300f3fa94063e4bfd7ac7cc80f15d8a15d6189311c16e"
CAPPED = "38bd905e7493326788d5e02ff790ce92eac43c7b533b1f9e8dc185ae5de92a63"
ESTONIAN_PRISM_CHANGES = (
    {"offset": 240, "original_hex": "1b94cf", "replacement_hex": "f685d3"},
    {"offset": 245172, "original_hex": "1b94cf", "replacement_hex": "f685d3"},
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prism_source():
    return build_windows_release.default_test_base_file(Path(TARGET)).read_bytes()


def estonian_prism_source():
    from tests.estonian_variants import invert_splices
    data = invert_splices(prism_source(), ESTONIAN_PRISM_CHANGES)
    assert sha(data) == ESTONIAN
    return data


class FrameCapBinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = load_release(ROOT)
        cls.mod = load_binary_mods(cls.release, ("frame_cap",))[0]
        cls.original = prism_source()

    def apply(self, data=None):
        images = {TARGET: bytearray(self.original if data is None else data)}
        PatchRunner(images).apply([self.mod])
        return images[TARGET]

    def test_manifest_registry_and_exact_round_trip(self):
        manifest = self.release.mods["frame_cap"]
        self.assertEqual(manifest.version, "0.3.0")
        self.assertEqual(manifest.kind, "binary")
        self.assertEqual(manifest.target, TARGET)
        self.assertEqual(manifest.source_sha256, PRISTINE)
        self.assertTrue(manifest.setup["default"])
        self.assertEqual(self.release.pristine_sha256[TARGET], PRISTINE)
        self.assertEqual(sha(self.original), PRISTINE)
        self.assertEqual(self.mod.inspect_bytes(self.original), PatchState.ORIGINAL)
        patched = self.apply()
        self.assertEqual(sha(patched), CAPPED)
        self.assertEqual(self.mod.inspect_bytes(patched), PatchState.PATCHED)
        image = PEImage.parse(patched)
        self.assertEqual(image.section(".htfps").flags, 0xE0000020)
        PatchRunner({TARGET: patched}).restore([self.mod])
        self.assertEqual(patched, self.original)

    def test_foreign_states_refuse_mutation(self):
        cases = []
        hook = bytearray(self.original)
        image = PEImage.parse(hook)
        offset = image.va_to_offset(0x40607C)
        hook[offset] ^= 1
        cases.append(hook)
        patched = self.apply()
        cases.append(patched + b"foreign")
        unrelated = bytearray(patched)
        unrelated[PEImage.parse(unrelated).va_to_offset(0x401000)] ^= 1
        cases.append(unrelated)
        payload = bytearray(patched)
        image = PEImage.parse(payload)
        payload[image.section(".htfps").raw_offset + 3] ^= 1
        cases.append(payload)
        for candidate in cases:
            with self.subTest(sha256=sha(candidate)):
                before = candidate[:]
                self.assertEqual(self.mod.inspect_bytes(candidate), PatchState.FOREIGN)
                with self.assertRaises(StateError):
                    PatchRunner({TARGET: candidate}).restore([self.mod])
                self.assertEqual(candidate, before)

    def test_reference_timing_vectors(self):
        from modpack.mods.frame_cap.patch import DeadlineSchedule, INTERVAL_US, LONG_GAP_US
        self.assertEqual(INTERVAL_US, 16667)
        schedule = DeadlineSchedule()
        self.assertEqual(schedule.frame([1_000_000]), (1_000_000, 1_016_667))
        schedule = DeadlineSchedule()
        self.assertEqual([schedule.frame(samples) for samples in (
            [0], [10_000, 16_667], [20_000, 30_000, 33_334], [40_000, 50_001],
        )], [(0, 16667), (16667, 33334), (33334, 50001), (50001, 66668)])
        schedule = DeadlineSchedule()
        schedule.frame([0])
        self.assertEqual(schedule.frame([20_000]), (20_000, 36_667))
        self.assertEqual(schedule.frame([100_000]), (100_000, 116_667))
        schedule = DeadlineSchedule()
        schedule.frame([100_000])
        self.assertEqual(
            schedule.frame([100_000 + LONG_GAP_US + 1]),
            (100_000 + LONG_GAP_US + 1, 100_000 + LONG_GAP_US + 1 + INTERVAL_US),
        )
        schedule.initialized = True
        schedule.last = 2**64 - 10_000
        schedule.next = 2**64 - 1_000
        self.assertEqual(schedule.frame([5_000]), (5_000, 21_667))
        schedule.last = 2**64 - 20_000
        schedule.next = 2**64 - 10_000
        self.assertEqual(schedule.frame([2**64 - 15_000, 5_000]), (5_000, 21_667))

    def test_reference_deadline_addition_wraps_at_64_bits(self):
        from modpack.mods.frame_cap.patch import DeadlineSchedule
        schedule = DeadlineSchedule()
        self.assertEqual(schedule.frame([2**64 - 10_000]), (2**64 - 10_000, 6_667))
        self.assertEqual(schedule.frame([2**64 - 5_000]), (2**64 - 5_000, 11_667))

    @unittest.skipUnless(
        shutil.which("i686-w64-mingw32-gcc") and shutil.which("wine"),
        "i686-w64-mingw32-gcc and Wine are required",
    )
    def test_emitted_wrapper_executes_with_controlled_clock_sequences(self):
        from modpack.mods.frame_cap import patch as frame_cap
        patched = self.apply()
        section = PEImage.parse(patched).section(".htfps")
        base = PEImage.parse(patched).image_base + section.virtual_address
        wrapper = frame_cap.build_wrapper(base)
        constants = {
            "WRAPPER_BASE": base,
            "WRAPPER_CODE_SIZE": len(wrapper),
            "CLOCK_VA": frame_cap.CLOCK_VA,
            "SYS_FRAME_VA": frame_cap.SYS_FRAME_VA,
            "RESUME_VA": frame_cap.RESUME_VA,
            "STATE_OFFSET": frame_cap.STATE_OFFSET,
            "COUNTER_OFFSET": frame_cap.COUNTER_OFFSET,
            "INITIALIZED_OFFSET": frame_cap.INITIALIZED_OFFSET,
            "NEXT_OFFSET": frame_cap.NEXT_OFFSET,
            "LAST_OFFSET": frame_cap.LAST_OFFSET,
            "NOW_OFFSET": frame_cap.NOW_OFFSET,
            "INTERVAL_US": frame_cap.INTERVAL_US,
            "LONG_GAP_US": frame_cap.LONG_GAP_US,
        }
        definitions = "".join(
            f"#define {name} 0x{value:X}u\n" for name, value in constants.items()
        )
        byte_rows = []
        for offset in range(0, len(wrapper), 12):
            byte_rows.append(", ".join(f"0x{byte:02x}" for byte in wrapper[offset:offset + 12]))
        header = definitions
        engine_base = frame_cap.SYS_FRAME_VA & ~0xFFF
        resume_base = frame_cap.RESUME_VA & ~0xFFF
        assembly = f""".section .engine,\"xr\"
.org 0x{frame_cap.SYS_FRAME_VA - engine_base:x}
ret
.org 0x{frame_cap.CLOCK_VA - engine_base:x}
jmp _clock_stub
.section .resume,\"xr\"
.org 0x{frame_cap.RESUME_VA - resume_base:x}
ret
.section .wrapper,\"xrw\"
"""
        assembly += "\n".join(".byte " + row for row in byte_rows) + "\n"
        with tempfile.TemporaryDirectory(prefix="ht-frame-cap-wrapper-") as temporary:
            root = Path(temporary)
            (root / "frame_cap_wrapper.h").write_text(header, encoding="ascii")
            stubs = root / "frame_cap_stubs.S"
            stubs.write_text(assembly, encoding="ascii")
            executable = root / "frame_cap_wrapper_harness.exe"
            compiled = subprocess.run(
                [
                    "i686-w64-mingw32-gcc",
                    "-std=c99",
                    "-O0",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wl,--image-base,0x400000",
                    "-Wl,--section-start,.text=0x600000",
                    f"-Wl,--section-start,.engine=0x{engine_base:x}",
                    f"-Wl,--section-start,.resume=0x{resume_base:x}",
                    f"-Wl,--section-start,.wrapper=0x{base:x}",
                    "-I",
                    str(root),
                    str(ROOT / "tests/frame_cap_wrapper_harness.c"),
                    str(stubs),
                    "-o",
                    str(executable),
                ],
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            environment = os.environ.copy()
            environment.update({
                "WINEPREFIX": str(root / "wineprefix"),
                "WINEARCH": "win64",
                "WINEDLLOVERRIDES": "mscoree,mshtml=",
                "WINEDEBUG": "-all",
            })
            executed = subprocess.run(
                ["wine", str(executable)],
                text=True,
                capture_output=True,
                timeout=120,
                env=environment,
            )
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
            self.assertIn("machine-code-executed scenarios=7", executed.stdout)
            self.assertIn(f"wrapper-base=0x{base:x}", executed.stdout)
            self.assertIn(f"wrapper-bytes={len(wrapper)}", executed.stdout)
            print(executed.stdout.strip())


class FrameCapTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "game"
        self.output = self.root / "output"
        (self.game / "game/sys").mkdir(parents=True)
        (self.game / "sys").mkdir()
        self.release = load_release(ROOT)
        self.catalog_path = ROOT / "modpack/migrations.json"
        self.catalog = load_catalog(self.catalog_path)
        self.golden = json.loads((ROOT / "tests/golden/v3.json").read_text())

    def write_sources(self, executable):
        (self.game / TARGET).write_bytes(executable)
        (self.game / "game/sys/game.dll").write_bytes(
            build_windows_release.default_test_base_file(Path("game/sys/game.dll")).read_bytes()
        )
        (self.game / "sys/dx8.dll").write_bytes(
            build_windows_release.default_test_base_file(Path("sys/dx8.dll")).read_bytes()
        )

    def test_canonical_and_estonian_stage_stock_cap_and_composed_mods(self):
        for lineage, source in (("canonical", prism_source()), ("estonian", estonian_prism_source())):
            for selected, expected in (([], PRISTINE), (["frame_cap"], CAPPED)):
                with self.subTest(lineage=lineage, selected=selected):
                    self.write_sources(source)
                    before = (self.game / TARGET).read_bytes()
                    stage(self.release, self.game, self.output, self.catalog_path, selected, {TARGET: expected})
                    self.assertEqual(sha((self.output / TARGET).read_bytes()), expected)
                    self.assertEqual((self.game / TARGET).read_bytes(), before)
                    self.assertEqual(normalize_to_pristine(source, TARGET, self.catalog), prism_source())
            self.write_sources(source)
            selected = list(self.release.mods)
            stage(
                self.release, self.game, self.output, self.catalog_path, selected,
                {
                    TARGET: CAPPED,
                    "game/sys/game.dll": self.golden["game_variants"]["1111"]["sha256"],
                    "sys/dx8.dll": self.golden["dx8"]["vsync"],
                },
            )
            self.assertEqual(sha((self.output / TARGET).read_bytes()), CAPPED)
            self.assertEqual(sha((self.output / "game/sys/game.dll").read_bytes()), self.golden["game_variants"]["1111"]["sha256"])
            self.assertEqual(sha((self.output / "sys/dx8.dll").read_bytes()), self.golden["dx8"]["vsync"])

    def test_stock_cap_estonian_foreign_status_and_plan(self):
        for data, state, variant, components in (
            (prism_source(), "current", "stock", ()),
            (self.apply_cap(), "current", "cap", ("frame_cap",)),
            (estonian_prism_source(), "migration", None, None),
            (b"foreign", "foreign", None, None),
        ):
            with self.subTest(state=state, variant=variant):
                self.write_sources(data)
                report = inspect_status(self.release, self.game, self.catalog)
                target = next(item for item in report.targets if item.target == TARGET)
                self.assertEqual((target.state, target.variant, target.components), (state, variant, components))
        self.write_sources(prism_source())
        plan = build_plan(self.release, self.game, self.catalog, ("frame_cap",), exact=True)
        change = next(item for item in plan.binary_changes if item.target == TARGET)
        self.assertEqual((change.variant, change.components, change.expected_sha256), ("cap", ("frame_cap",), CAPPED))

    def test_lifecycle_apply_and_whole_remove_restore_exact_incoming(self):
        from htmod.lifecycle import Lifecycle
        for lineage, source in (("canonical", prism_source()), ("estonian", estonian_prism_source())):
            with self.subTest(lineage=lineage):
                shutil.rmtree(self.game)
                (self.game / "game/sys").mkdir(parents=True)
                (self.game / "sys").mkdir()
                self.write_sources(source)
                service = Lifecycle(self.release, self.game, self.catalog)
                applied = service.apply(requested=("frame_cap",), exact=True)
                self.assertEqual(applied.status, "installed")
                self.assertEqual(sha((self.game / TARGET).read_bytes()), CAPPED)
                removed = service.remove(all=True)
                self.assertEqual(removed.status, "removed")
                self.assertEqual((self.game / TARGET).read_bytes(), source)

    def apply_cap(self):
        mod = load_binary_mods(self.release, ("frame_cap",))[0]
        images = {TARGET: bytearray(prism_source())}
        PatchRunner(images).apply([mod])
        return bytes(images[TARGET])

    def test_release_builder_emits_stock_cap_and_required_sources(self):
        payloads = build_windows_release.build_prism3d_payloads(
            build_windows_release.default_test_base_file(Path(TARGET))
        )
        self.assertEqual(payloads, {
            "stock": {"sha256": PRISTINE},
            "cap": {"sha256": CAPPED},
        })
        build_windows_release.validate_migration_catalog(
            self.catalog,
            self.release,
            build_windows_release.default_test_base_file(Path("game/sys/game.dll")),
            build_windows_release.default_test_base_file(Path("sys/dx8.dll")),
            build_windows_release.build_game_variants(build_windows_release.default_test_base_file(Path("game/sys/game.dll"))),
            build_windows_release.build_dx8_payloads(build_windows_release.default_test_base_file(Path("sys/dx8.dll"))),
            payloads,
            build_windows_release.default_test_base_file(Path(TARGET)),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
