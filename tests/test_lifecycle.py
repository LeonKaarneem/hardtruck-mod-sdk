import contextlib
from dataclasses import FrozenInstanceError, replace
import hashlib
import io
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
from htmod.errors import PatchError
from htmod.lifecycle import build_plan, inspect_status, resolve_selection
from htmod.migration import load_catalog
from htmod.runner import materialize_data_bytes
from tests.estonian_variants import estonian_binary_images
from tests.legacy_variants import write_legacy_variant
from tests.release_fixtures import current_binary_images, write_current_payload


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = load_release(ROOT)
        cls.catalog = load_catalog(ROOT / "modpack/migrations.json")
        cls.game_variants, cls.dx8 = current_binary_images()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.payload = Path(cls.temporary.name) / "payload"
        write_current_payload(cls.payload)
        legacy_path = Path(cls.temporary.name) / "legacy-0001.dll"
        write_legacy_variant(cls.payload, "0001", legacy_path)
        cls.legacy = legacy_path.read_bytes()
        cls.estonian_game, cls.estonian_dx8 = estonian_binary_images(cls.payload)
        cls.data_bytes = materialize_data_bytes(cls.release)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.game = self.root / "game dir"
        self.game.mkdir()
        self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())

    def write_target(self, target, data):
        path = self.game / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_data(self, components=None):
        if components is None:
            components = self.data_bytes
        else:
            selected = list(components)
            game_path = self.game / "game/sys/game.dll"
            if game_path.is_file():
                actual = game_path.read_bytes()
                for name, data in self.game_variants.items():
                    if actual == data:
                        selected.extend(self.release.game_variants[name].components)
                        break
            dx8_path = self.game / "sys/dx8.dll"
            if dx8_path.is_file() and dx8_path.read_bytes() == self.dx8["vsync"]:
                selected.append("dx8_vsync")
            rendered = materialize_data_bytes(self.release, selected)
            components = {component: rendered[component] for component in components}
        for component, files in components.items():
            for destination, content in files.items():
                self.write_target(destination, content)

    def status(self):
        return inspect_status(self.release, self.game, self.catalog)

    def plan(self, requested=(), exact=False):
        return build_plan(
            self.release, self.game, self.catalog, requested, exact=exact,
        )

    def target(self, report, target):
        return next(item for item in report.targets if item.target == target)

    def component(self, report, component):
        return next(item for item in report.components if item.id == component)

    def tree_hashes(self, root=None):
        root = self.game if root is None else root
        return {
            path.relative_to(root).as_posix(): sha256(path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_all_current_game_variants_map_exact_membership(self):
        for name, data in self.game_variants.items():
            with self.subTest(variant=name):
                self.write_target("game/sys/game.dll", data)
                report = self.status()
                target = self.target(report, "game/sys/game.dll")
                self.assertEqual(target.state, "current")
                self.assertEqual(target.sha256, sha256(data))
                self.assertEqual(target.variant, name)
                self.assertEqual(target.components, self.release.game_variants[name].components)
                for mod_id in ("camera", "physics", "ai", "ui_keyboard"):
                    expected = "installed" if mod_id in target.components else "not_installed"
                    self.assertEqual(self.component(report, mod_id).state, expected)

    def test_current_wins_shared_legacy_hash(self):
        self.assertIn(sha256(self.game_variants["0000"]), self.release.legacy_game_sha256)
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        target = self.target(self.status(), "game/sys/game.dll")
        self.assertEqual((target.state, target.variant, target.components), ("current", "0000", ()))

    def test_both_dx8_states_map_exact_membership(self):
        for name, components in (("stock", ()), ("vsync", ("dx8_vsync",))):
            with self.subTest(variant=name):
                self.write_target("sys/dx8.dll", self.dx8[name])
                report = self.status()
                target = self.target(report, "sys/dx8.dll")
                self.assertEqual(target.state, "current")
                self.assertEqual(target.variant, name)
                self.assertEqual(target.components, components)
                expected = "not_installed" if name == "stock" else "installed"
                self.assertEqual(self.component(report, "dx8_vsync").state, expected)

    def test_noncurrent_legacy_has_null_membership(self):
        self.assertNotIn(sha256(self.legacy), {sha256(data) for data in self.game_variants.values()})
        self.write_target("game/sys/game.dll", self.legacy)
        report = self.status()
        target = self.target(report, "game/sys/game.dll")
        self.assertEqual((target.state, target.variant, target.components), ("legacy", None, None))
        for mod_id in ("camera", "physics", "ai", "ui_keyboard"):
            self.assertEqual(self.component(report, mod_id).state, "unknown")

    def test_estonian_has_migration_state_without_membership(self):
        self.write_target("game/sys/game.dll", self.estonian_game)
        self.write_target("sys/dx8.dll", self.estonian_dx8)
        report = self.status()
        for target in ("game/sys/game.dll", "sys/dx8.dll"):
            status = self.target(report, target)
            self.assertEqual((status.state, status.variant, status.components), ("migration", None, None))

    def test_unknown_truncated_and_appended_are_foreign_and_missing_is_valid(self):
        current = self.game_variants["1111"]
        for name, data in (
            ("unknown", b"unknown"),
            ("truncated", current[:-1]),
            ("appended", current + b"foreign"),
        ):
            with self.subTest(case=name):
                self.write_target("game/sys/game.dll", data)
                target = self.target(self.status(), "game/sys/game.dll")
                self.assertEqual(target.state, "foreign")
                self.assertEqual(target.sha256, sha256(data))
                self.assertIsNone(target.variant)
                self.assertIsNone(target.components)
        self.write_target("game/sys/game.dll", current)
        (self.game / "game/sys/game.dll").unlink()
        target = self.target(self.status(), "game/sys/game.dll")
        self.assertEqual((target.state, target.sha256), ("missing", None))
        self.assertEqual(self.component(self.status(), "camera").state, "missing")

    def test_unsafe_binary_path_is_an_error(self):
        outside = self.root / "outside.dll"
        outside.write_bytes(self.game_variants["0000"])
        path = self.game / "game/sys/game.dll"
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        with self.assertRaisesRegex(PatchError, "unsafe|symlink"):
            self.status()

    def test_data_missing_matching_modified_and_mixed_states(self):
        report = self.status()
        config = self.component(report, "config")
        self.assertEqual(config.state, "missing")
        self.assertIsNone(config.target)
        self.assertTrue(all(item.state == "missing" and item.actual_sha256 is None for item in config.files))
        config_path = self.release.mods["config"].files[0].destination
        expected_config = sha256(self.data_bytes["config"][config_path])
        self.assertEqual(config.files[0].expected_sha256, expected_config)

        self.write_target("game/sys/game.dll", self.game_variants["1111"])
        self.write_target("sys/dx8.dll", self.dx8["vsync"])
        self.write_data()
        report = self.status()
        self.assertTrue(all(item.state == "matching" for item in report.components if item.kind == "data"))
        self.assertEqual(self.component(report, "config").files[0].state, "matching")

        for destination in self.data_bytes["handling_defs"]:
            self.write_target(destination, b"modified" + destination.encode())
        report = self.status()
        self.assertEqual(self.component(report, "handling_defs").state, "modified")
        self.assertTrue(all(item.state == "modified" for item in self.component(report, "handling_defs").files))

        second = self.release.mods["config"].files[1].destination
        (self.game / second).unlink()
        report = self.status()
        config = self.component(report, "config")
        self.assertEqual(config.state, "partial")
        self.assertEqual([item.state for item in config.files[:2]], ["matching", "missing"])

    def test_custom_quick_setup_status_and_plan_use_derived_selection(self):
        self.write_target("prism3d.exe", (self.payload / "prism3d-stock.exe").read_bytes())
        selected = ("config", "ui_keyboard")
        custom = materialize_data_bytes(self.release, selected)
        for destination, content in custom["config"].items():
            self.write_target(destination, content)
        self.write_target("game/sys/game.dll", self.game_variants["1000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])

        report = self.status()
        config = self.component(report, "config")
        quick_setup = next(item for item in config.files if item.path == "game/usr/ui/cosetup.sc")
        self.assertEqual(config.state, "matching")
        self.assertEqual(quick_setup.state, "matching")
        self.assertEqual(quick_setup.expected_sha256, sha256(custom["config"][quick_setup.path]))
        self.assertEqual(self.component(report, "ui_keyboard").state, "installed")
        self.assertTrue(self.plan(selected, exact=True).no_op)

        self.write_target(quick_setup.path, self.data_bytes["config"][quick_setup.path])
        report = self.status()
        quick_setup = next(
            item for item in self.component(report, "config").files
            if item.path == quick_setup.path
        )
        self.assertEqual(quick_setup.state, "modified")
        self.assertEqual(quick_setup.expected_sha256, sha256(custom["config"][quick_setup.path]))
        plan = self.plan(selected, exact=True)
        change, = plan.data_changes
        self.assertEqual(change.path, "game/usr/ui/cosetup.sc")
        self.assertEqual(change.expected_sha256, quick_setup.expected_sha256)

    def test_legacy_static_quick_setup_is_modified(self):
        selected = ("config", "ui_keyboard")
        custom = materialize_data_bytes(self.release, selected)
        for destination, content in custom["config"].items():
            self.write_target(destination, content)
        source = self.release.mods["config"].source_path(
            next(
                record for record in self.release.mods["config"].files
                if record.destination == "game/usr/ui/cosetup.sc"
            )
        ).read_bytes()
        self.write_target("game/usr/ui/cosetup.sc", source)
        self.write_target("game/sys/game.dll", self.game_variants["1000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        quick_setup = next(
            item for item in self.component(self.status(), "config").files
            if item.path == "game/usr/ui/cosetup.sc"
        )
        self.assertEqual(quick_setup.state, "modified")

    def test_status_report_is_immutable_and_has_declarative_schema(self):
        self.write_target("game/sys/game.dll", self.legacy)
        report = self.status()
        with self.assertRaises(FrozenInstanceError):
            report.targets = ()
        value = report.to_dict()
        self.assertEqual(set(value), {"schema_version", "targets", "components"})
        self.assertEqual(value["schema_version"], 1)
        game = next(item for item in value["targets"] if item["target"] == "game/sys/game.dll")
        self.assertIsNone(game["variant"])
        self.assertIsNone(game["components"])
        self.assertNotIn(str(self.game), json.dumps(value))

    def test_selection_computes_transitive_dependencies_in_release_order(self):
        mods = dict(self.release.mods)
        mods["config"] = replace(mods["config"], requires=("handling_defs",))
        release = replace(self.release, mods=mods)
        self.assertEqual(
            resolve_selection(release, ("ui_keyboard",)),
            ("config", "handling_defs", "ui_keyboard"),
        )
        self.assertEqual(
            resolve_selection(self.release, ("physics",), installed=("config", "camera")),
            ("config", "camera", "physics"),
        )

    def test_selection_rejects_duplicates_unknown_and_final_conflicts(self):
        for requested, message in (
            (("camera", "camera"), "duplicate"),
            (("missing",), "unknown"),
        ):
            with self.subTest(requested=requested), self.assertRaisesRegex(PatchError, message):
                resolve_selection(self.release, requested)
        mods = dict(self.release.mods)
        mods["physics"] = replace(mods["physics"], conflicts=("ai",))
        with self.assertRaisesRegex(PatchError, "conflicts"):
            resolve_selection(replace(self.release, mods=mods), ("physics", "ai"))

    def test_planning_rejects_uncurated_final_game_tuple(self):
        release = replace(
            self.release,
            game_variants={
                name: self.release.game_variants[name]
                for name in ("0000", "0001", "0010")
            },
        )
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        with self.assertRaisesRegex(PatchError, "curated|variant"):
            build_plan(release, self.game, self.catalog, ("camera", "physics"), exact=True)

    def test_additive_camera_physics_and_exact_physics_plan_expected_variants(self):
        self.write_target("prism3d.exe", (self.payload / "prism3d-stock.exe").read_bytes())
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        additive = self.plan(("physics", "camera"))
        self.assertEqual(additive.mode, "additive")
        self.assertEqual(additive.requested, ("camera", "physics"))
        self.assertEqual(additive.selected, ("camera", "physics"))
        self.assertFalse(additive.no_op)
        self.assertEqual(len(additive.binary_changes), 1)
        change = additive.binary_changes[0]
        self.assertEqual(change.target, "game/sys/game.dll")
        self.assertEqual(change.action, "replace")
        self.assertEqual(change.variant, "0011")
        self.assertEqual(change.components, ("camera", "physics"))
        self.assertEqual(change.source_sha256, sha256(self.game_variants["0000"]))
        self.assertEqual(change.expected_sha256, sha256(self.game_variants["0011"]))

        exact = self.plan(("physics",), exact=True)
        self.assertEqual(exact.mode, "exact")
        self.assertEqual(exact.selected, ("physics",))
        self.assertEqual(len(exact.binary_changes), 1)
        self.assertEqual(exact.binary_changes[0].variant, "0010")
        self.assertEqual(exact.binary_changes[0].expected_sha256, sha256(self.game_variants["0010"]))

    def test_additive_binary_from_legacy_or_estonian_requires_exact(self):
        for name, data in (("legacy", self.legacy), ("estonian", self.estonian_game)):
            with self.subTest(source=name):
                self.write_target("game/sys/game.dll", data)
                self.write_target("sys/dx8.dll", self.dx8["stock"])
                with self.assertRaisesRegex(PatchError, "exact"):
                    self.plan(("camera",))
                plan = self.plan(("camera",), exact=True)
                game_change = next(change for change in plan.binary_changes if change.target == "game/sys/game.dll")
                self.assertEqual(game_change.variant, "0001")
                self.assertEqual(game_change.source_sha256, sha256(data))
                self.assertEqual(game_change.expected_sha256, sha256(self.game_variants["0001"]))

    def test_exact_legacy_can_replace_with_empty_current_variant(self):
        self.write_target("game/sys/game.dll", self.legacy)
        self.write_target("sys/dx8.dll", self.dx8["vsync"])
        plan = self.plan((), exact=True)
        game_change = next(change for change in plan.binary_changes if change.target == "game/sys/game.dll")
        dx8_change = next(change for change in plan.binary_changes if change.target == "sys/dx8.dll")
        self.assertEqual((game_change.variant, game_change.components), ("0000", ()))
        self.assertEqual((dx8_change.variant, dx8_change.components), ("stock", ()))

    def test_data_only_additive_plan_leaves_unknown_binaries_untouched(self):
        self.write_target("prism3d.exe", (self.payload / "prism3d-stock.exe").read_bytes())
        self.write_target("game/sys/game.dll", b"unknown")
        self.write_target("sys/dx8.dll", b"unknown")
        plan = self.plan(("config",))
        self.assertEqual(plan.selected, ("config",))
        self.assertEqual(plan.binary_changes, ())
        self.assertEqual(len(plan.data_changes), len(self.release.mods["config"].files))
        self.assertTrue(all(change.action == "write" for change in plan.data_changes))

    def test_missing_or_foreign_binary_rejects_required_reconstruction(self):
        with self.assertRaisesRegex(PatchError, "missing|source|reconstruct"):
            self.plan(("camera",))
        self.write_target("game/sys/game.dll", b"foreign")
        with self.assertRaisesRegex(PatchError, "foreign|source|reconstruct"):
            self.plan(("camera",), exact=True)

    def test_modified_data_is_repaired_only_when_selected(self):
        self.write_data()
        self.write_target("game/sys/game.dll", self.game_variants["0001"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        destination = self.release.mods["handling_defs"].files[0].destination
        self.write_target(destination, b"locally modified")

        additive = self.plan(("camera",))
        self.assertEqual(additive.data_changes, ())
        exact = self.plan(("camera",), exact=True)
        self.assertEqual(exact.data_changes, ())
        self.assertTrue(all(change.action == "write" for change in exact.data_changes))

        repaired = self.plan(("handling_defs",))
        self.assertEqual(len(repaired.data_changes), 1)
        change = repaired.data_changes[0]
        self.assertEqual(change.component, "handling_defs")
        self.assertEqual(change.path, destination)
        self.assertEqual(change.action, "write")
        self.assertEqual(change.expected_sha256, sha256(self.data_bytes["handling_defs"][destination]))

    def test_matching_additive_and_exact_plans_are_no_op(self):
        self.write_data()
        self.write_target("game/sys/game.dll", self.game_variants["1111"])
        self.write_target("sys/dx8.dll", self.dx8["vsync"])
        additive = self.plan()
        self.assertTrue(additive.no_op)
        self.assertEqual(additive.data_changes, ())
        self.assertEqual(additive.binary_changes, ())
        exact = self.plan(tuple(self.release.mods), exact=True)
        self.assertTrue(exact.no_op)
        self.assertEqual(exact.to_dict()["no_op"], True)
        self.assertEqual(exact.to_dict()["data_changes"], [])
        self.assertEqual(exact.to_dict()["binary_changes"], [])

    def test_cli_status_json_null_and_full_tree_unchanged(self):
        from htmod import cli

        self.write_target("game/sys/game.dll", self.legacy)
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.write_data(("config",))
        before = self.tree_hashes()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli.main([
                "--root", str(ROOT), "status", "--game-dir", str(self.game),
                "--migrations", str(ROOT / "modpack/migrations.json"),
            ])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["schema_version"], 1)
        game = next(item for item in value["targets"] if item["target"] == "game/sys/game.dll")
        self.assertIsNone(game["components"])
        self.assertEqual(self.tree_hashes(), before)

    def test_cli_plan_no_op_json_and_full_tree_unchanged(self):
        from htmod import cli

        self.write_data()
        self.write_target("game/sys/game.dll", self.game_variants["1111"])
        self.write_target("sys/dx8.dll", self.dx8["vsync"])
        before = self.tree_hashes()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli.main([
                "--root", str(ROOT), "plan", "--game-dir", str(self.game),
                "--migrations", str(ROOT / "modpack/migrations.json"),
            ])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["mode"], "additive")
        self.assertTrue(value["no_op"])
        self.assertEqual(value["data_changes"], [])
        self.assertEqual(value["binary_changes"], [])
        self.assertEqual(self.tree_hashes(), before)

    def test_cli_status_and_plan_create_no_bytecode_in_release_tree(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        before = self.tree_hashes()
        for command in ("status", "plan"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temporary:
                release_root = Path(temporary) / "release"
                shutil.copytree(
                ROOT / "modpack",
                release_root / "modpack",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
                environment = os.environ.copy()
                environment.pop("PYTHONDONTWRITEBYTECODE", None)
                environment.pop("PYTHONPYCACHEPREFIX", None)
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools/htmod.py"),
                        "--root", str(release_root),
                        command,
                        "--game-dir", str(self.game),
                        "--migrations", str(release_root / "modpack/migrations.json"),
                    ],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                json.loads(completed.stdout)
                self.assertEqual(list(release_root.rglob("__pycache__")), [])
                self.assertEqual(list(release_root.rglob("*.pyc")), [])
                self.assertEqual(self.tree_hashes(), before)

    def test_cli_camera_plan_is_declarative_and_leaves_game_and_source_unchanged(self):
        from htmod import cli

        self.write_target("prism3d.exe", (self.payload / "prism3d-stock.exe").read_bytes())
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        with tempfile.TemporaryDirectory() as temporary:
            release_root = Path(temporary) / "release"
            shutil.copytree(
                ROOT / "modpack",
                release_root / "modpack",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            game_before = self.tree_hashes()
            source_before = self.tree_hashes(release_root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main([
                    "--root", str(release_root), "plan", "--game-dir", str(self.game),
                    "--migrations", str(release_root / "modpack/migrations.json"),
                    "--mod", "camera",
                ])
            self.assertEqual(result, 0, stderr.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            value = json.loads(stdout.getvalue())
            self.assertEqual(
                set(value),
                {
                    "schema_version", "mode", "requested", "selected", "no_op",
                    "data_changes", "binary_changes",
                },
            )
            self.assertEqual(value["schema_version"], 1)
            self.assertEqual(value["mode"], "additive")
            self.assertEqual(value["requested"], ["camera"])
            self.assertEqual(value["selected"], ["camera"])
            self.assertFalse(value["no_op"])
            self.assertEqual(value["data_changes"], [])
            self.assertEqual(
                value["binary_changes"],
                [{
                    "target": "game/sys/game.dll",
                    "action": "replace",
                    "variant": "0001",
                    "components": ["camera"],
                    "source_sha256": sha256(self.game_variants["0000"]),
                    "expected_sha256": sha256(self.game_variants["0001"]),
                }],
            )
            self.assertEqual(self.tree_hashes(), game_before)
            self.assertEqual(self.tree_hashes(release_root), source_before)

    def test_invalid_cli_plan_emits_no_partial_json_and_does_not_change_tree(self):
        from htmod import cli

        self.write_target("game/sys/game.dll", self.legacy)
        before = self.tree_hashes()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli.main([
                "--root", str(ROOT), "plan", "--game-dir", str(self.game),
                "--migrations", str(ROOT / "modpack/migrations.json"),
                "--mod", "camera",
            ])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("exact", stderr.getvalue())
        self.assertEqual(self.tree_hashes(), before)


if __name__ == "__main__":
    unittest.main()
