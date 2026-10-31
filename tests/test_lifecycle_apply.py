import contextlib
from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tests.test_lifecycle import ROOT, sha256
from htmod import load_release
from htmod.errors import PatchError
from htmod.lifecycle import Lifecycle
from htmod.migration import load_catalog
from htmod.runner import materialize_data_bytes
from tests.estonian_variants import estonian_binary_images
from tests.legacy_variants import write_legacy_variant
from tests.release_fixtures import current_binary_images, write_current_payload


class ApplyFixture(unittest.TestCase):
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
        from htmod.lifecycle import inspect_status
        return inspect_status(self.release, self.game, self.catalog)

    def plan(self, requested=(), exact=False):
        from htmod.lifecycle import build_plan
        return build_plan(self.release, self.game, self.catalog, requested, exact=exact)

    def component(self, report, component):
        return next(item for item in report.components if item.id == component)

    def tree_hashes(self, root=None):
        root = self.game if root is None else root
        return {
            path.relative_to(root).as_posix(): sha256(path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        }


class LifecycleApplyTests(ApplyFixture):
    def service(self, game=None, backups=None, release=None):
        return Lifecycle(
            self.release if release is None else release,
            self.game if game is None else game,
            self.catalog,
            backup_dir=backups,
        )

    def generations(self, root=None):
        root = self.game / ".htmod/backups" if root is None else Path(root)
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def journal(self, generation):
        return json.loads((generation / "journal.json").read_text(encoding="utf-8"))

    def test_data_only_apply_transforms_data_and_preserves_binaries_saves_and_unrelated_files(self):
        unknown_game = b"unknown game"
        unknown_dx8 = b"unknown dx8"
        self.write_target("game/sys/game.dll", unknown_game)
        self.write_target("sys/dx8.dll", unknown_dx8)
        self.write_target("game/usr/save/profile.sav", b"save bytes")
        self.write_target("unrelated.txt", b"unrelated")
        existing = self.release.mods["config"].files[0].destination
        self.write_target(existing, b"exact incoming data")

        result = self.service().apply(requested=("config",))

        self.assertEqual(result.status, "installed")
        self.assertEqual(result.errors, ())
        expected_config = materialize_data_bytes(self.release, ("config",))["config"]
        for destination, expected in expected_config.items():
            self.assertEqual((self.game / destination).read_bytes(), expected)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), unknown_game)
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), unknown_dx8)
        self.assertEqual((self.game / "game/usr/save/profile.sav").read_bytes(), b"save bytes")
        self.assertEqual((self.game / "unrelated.txt").read_bytes(), b"unrelated")
        generation, = self.generations()
        journal = self.journal(generation)
        self.assertEqual(journal["status"], "installed")
        self.assertEqual(journal["transaction_id"], result.transaction_id)
        entry = next(item for item in journal["operations"] if item["destination"] == existing)
        backup = generation / entry["backup_path"]
        self.assertEqual(backup.read_bytes(), b"exact incoming data")
        self.assertTrue(entry["existed"])
        self.assertTrue(self.service().apply(requested=("config",)).no_op)
        self.assertEqual(len(self.generations()), 1)

    def test_custom_quick_setup_apply_hashes_bytes_backup_and_idempotence(self):
        selected = ("config", "ui_keyboard")
        custom = materialize_data_bytes(self.release, selected)
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        original = b"exact incoming Quick Setup bytes\r\n"
        self.write_target("game/usr/ui/cosetup.sc", original)

        plan = self.plan(selected, exact=True)
        change = next(
            item for item in plan.data_changes
            if item.path == "game/usr/ui/cosetup.sc"
        )
        expected = custom["config"]["game/usr/ui/cosetup.sc"]
        self.assertEqual(change.expected_sha256, sha256(expected))
        self.assertNotIn("Enabled:", json.dumps(plan.to_dict()))

        result = self.service().apply(plan=plan)
        self.assertEqual(result.status, "installed", result.errors)
        self.assertEqual((self.game / change.path).read_bytes(), expected)
        generation, = self.generations()
        entry = next(
            item for item in self.journal(generation)["operations"]
            if item["destination"] == change.path
        )
        self.assertEqual(entry["before_sha256"], sha256(original))
        self.assertEqual(entry["after_sha256"], change.expected_sha256)
        self.assertEqual((generation / entry["backup_path"]).read_bytes(), original)
        self.assertTrue(self.plan(selected, exact=True).no_op)
        self.assertTrue(self.service().apply(requested=selected, exact=True).no_op)
        self.assertEqual(len(self.generations()), 1)

    def test_additive_binary_preserves_existing_components_and_exact_reconciles(self):
        self.write_target("game/sys/game.dll", self.game_variants["0001"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])

        additive = self.service().apply(requested=("physics",))

        self.assertEqual(additive.status, "installed")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0011"],
        )
        report = self.status()
        self.assertEqual(self.component(report, "camera").state, "installed")
        self.assertEqual(self.component(report, "physics").state, "installed")
        generation, = self.generations()
        entry = self.journal(generation)["operations"][0]
        self.assertEqual((generation / entry["backup_path"]).read_bytes(), self.game_variants["0001"])

        exact = self.service().apply(requested=("physics",), exact=True)
        self.assertEqual(exact.status, "installed")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0010"],
        )
        self.assertEqual(self.component(self.status(), "camera").state, "not_installed")
        self.assertEqual(len(self.generations()), 2)

        no_op = self.service().apply(requested=("physics",), exact=True)
        self.assertEqual(no_op.status, "no_op")
        self.assertTrue(no_op.no_op)
        self.assertEqual(no_op.operations, ())
        self.assertEqual(len(self.generations()), 2)

    def test_current_legacy_and_estonian_apply_produce_current_bytes_and_exact_backups(self):
        for name, source in (
            ("current", self.game_variants["0000"]),
            ("legacy", self.legacy),
            ("estonian", self.estonian_game),
        ):
            with self.subTest(source=name):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("game/sys/game.dll", source)
                self.write_target("sys/dx8.dll", self.dx8["stock"])
                result = self.service().apply(requested=("camera",), exact=True)
                self.assertEqual(result.status, "installed")
                self.assertEqual(
                    (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0001"],
                )
                generation, = self.generations()
                entry = next(
                    item for item in self.journal(generation)["operations"]
                    if item["destination"] == "game/sys/game.dll"
                )
                self.assertEqual((generation / entry["backup_path"]).read_bytes(), source)
                self.assertEqual(entry["before_sha256"], sha256(source))
                self.assertEqual(entry["after_sha256"], sha256(self.game_variants["0001"]))

    def test_custom_backup_location_and_declarative_immutable_result(self):
        backups = self.root / "custom backups"
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        result = self.service(backups=backups).apply(requested=("camera",))
        self.assertEqual(len(self.generations()), 0)
        generation, = self.generations(backups)
        self.assertEqual(self.journal(generation)["schema_version"], 1)
        with self.assertRaises(FrozenInstanceError):
            result.status = "changed"
        value = result.to_dict()
        self.assertEqual(
            set(value),
            {"schema_version", "transaction_id", "status", "no_op", "operations", "warnings", "errors"},
        )
        self.assertEqual(value["schema_version"], 1)
        encoded = json.dumps(value)
        self.assertNotIn(str(self.game), encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("payload", encoded)
        self.assertTrue(self.plan(("camera",)).no_op)

    def test_invalid_combinations_fail_before_backup_or_mutation(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        before = self.tree_hashes()
        mods = dict(self.release.mods)
        mods["physics"] = replace(mods["physics"], conflicts=("ai",))
        conflicted = replace(self.release, mods=mods)
        with self.assertRaisesRegex(PatchError, "conflicts"):
            self.service(release=conflicted).apply(requested=("physics", "ai"), exact=True)
        uncurated = replace(
            self.release,
            game_variants={"0000": self.release.game_variants["0000"]},
        )
        with self.assertRaisesRegex(PatchError, "curated|variant"):
            self.service(release=uncurated).apply(requested=("camera",), exact=True)
        with self.assertRaisesRegex(PatchError, "unknown"):
            self.service().apply(requested=("missing",))
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

    def test_stale_or_tampered_plan_and_external_edit_are_refused_without_backup(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        stale = self.plan(("camera",))
        self.write_target("game/sys/game.dll", self.game_variants["0010"])
        with self.assertRaisesRegex(PatchError, "stale|changed|plan"):
            self.service().apply(plan=stale)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0010"])
        self.assertEqual(self.generations(), [])

        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        tampered = replace(
            self.plan(("camera",)),
            binary_changes=(replace(self.plan(("camera",)).binary_changes[0], expected_sha256="0" * 64),),
        )
        with self.assertRaisesRegex(PatchError, "stale|invalid|plan"):
            self.service().apply(plan=tampered)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"])
        self.assertEqual(self.generations(), [])

    def test_staging_and_lock_failures_create_no_backup_or_mutation(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        before = self.tree_hashes()
        with mock.patch("htmod.lifecycle._stage_binary", side_effect=PatchError("injected staging")):
            with self.assertRaisesRegex(PatchError, "injected staging"):
                self.service().apply(requested=("camera",))
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

        from htmod.lifecycle import _transaction_lock
        with _transaction_lock(self.game, "other transaction"):
            with self.assertRaisesRegex(PatchError, "lock|concurrent"):
                self.service().apply(requested=("camera",))
            self.assertEqual(
                (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"],
            )
            self.assertEqual(self.generations(), [])
        lock = self.game / ".htmod/transaction.lock"
        self.assertEqual(lock.read_text(encoding="utf-8"), "other transaction")

    def test_symlink_and_nonregular_destinations_fail_safely(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        destination = self.game / self.release.mods["config"].files[0].destination
        destination.parent.mkdir(parents=True)
        destination.symlink_to(outside)
        with self.assertRaisesRegex(PatchError, "unsafe|regular|symlink"):
            self.service().apply(requested=("config",))
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertEqual(self.generations(), [])

        destination.unlink()
        destination.mkdir()
        with self.assertRaisesRegex(PatchError, "unsafe|regular"):
            self.service().apply(requested=("config",))
        self.assertTrue(destination.is_dir())
        self.assertEqual(self.generations(), [])

    def test_cli_apply_all_exact_and_no_op_emit_one_result(self):
        from htmod import cli

        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        argv = [
            "--root", str(ROOT), "apply", "--game-dir", str(self.game),
            "--migrations", str(ROOT / "modpack/migrations.json"), "--all", "--exact",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = cli.main(argv)
        self.assertEqual(returncode, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["status"], "installed")
        self.assertEqual(len(value["operations"]), 22)
        installed_paths = {
            path for path in self.tree_hashes() if not path.startswith(".htmod/")
        }
        expected_paths = {
            destination
            for files in self.data_bytes.values()
            for destination in files
        } | {"game/sys/game.dll", "sys/dx8.dll"}
        self.assertEqual(installed_paths, expected_paths)
        self.assertTrue(all(
            component.state in {"matching", "installed"}
            for component in self.status().components
        ))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = cli.main(argv)
        self.assertEqual(returncode, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "no_op")
        self.assertEqual(len(self.generations()), 1)

    def test_invalid_cli_apply_has_no_partial_json_or_backup(self):
        from htmod import cli

        self.write_target("game/sys/game.dll", self.legacy)
        before = self.tree_hashes()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = cli.main([
                "--root", str(ROOT), "apply", "--game-dir", str(self.game),
                "--migrations", str(ROOT / "modpack/migrations.json"), "--mod", "camera",
            ])
        self.assertEqual(returncode, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("exact", stderr.getvalue())
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])


class LifecycleApplyFailureTests(LifecycleApplyTests):
    def prepare_two_binary_changes(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        return self.plan(("camera", "dx8_vsync"), exact=True)

    def test_backup_failure_retains_journal_and_incoming_files(self):
        plan = self.prepare_two_binary_changes()
        with mock.patch("htmod.lifecycle._write_backup", side_effect=PatchError("injected backup")):
            result = self.service().apply(plan=plan)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"])
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.dx8["stock"])
        generation, = self.generations()
        self.assertEqual(self.journal(generation)["status"], "rolled_back")
        self.assertIn("injected backup", result.errors[0])

    def test_failure_at_each_replacement_rolls_back_in_reverse_order(self):
        for failure_index in (0, 1):
            with self.subTest(replacement=failure_index):
                shutil.rmtree(self.game)
                self.game.mkdir()
                plan = self.prepare_two_binary_changes()
                originals = {
                    "game/sys/game.dll": self.game_variants["0000"],
                    "sys/dx8.dll": self.dx8["stock"],
                }
                import htmod.lifecycle as lifecycle
                real_replace = lifecycle._replace_destination
                real_restore = lifecycle._restore_operation
                replacements = []
                restores = []

                def replace_and_fail(temporary, destination, *args, **kwargs):
                    index = len(replacements)
                    replacements.append(destination.relative_to(self.game).as_posix())
                    if index == failure_index:
                        raise PatchError("injected replacement")
                    return real_replace(temporary, destination, *args, **kwargs)

                def record_restore(*args):
                    restores.append(args[1])
                    return real_restore(*args)

                with mock.patch("htmod.lifecycle._replace_destination", side_effect=replace_and_fail), mock.patch(
                    "htmod.lifecycle._restore_operation", side_effect=record_restore,
                ):
                    result = self.service().apply(plan=plan)
                self.assertEqual(result.status, "rolled_back")
                self.assertEqual(restores, list(reversed(replacements[:failure_index + 1])))
                for destination, original in originals.items():
                    self.assertEqual((self.game / destination).read_bytes(), original)
                generation, = self.generations()
                self.assertEqual(self.journal(generation)["status"], "rolled_back")

    def test_post_write_verification_failure_rolls_back_every_operation_in_reverse(self):
        plan = self.prepare_two_binary_changes()
        import htmod.lifecycle as lifecycle
        real_restore = lifecycle._restore_operation
        restores = []
        verifications = []

        def record_restore(*args):
            restores.append(args[1])
            return real_restore(*args)

        def fail_after_both(destination, expected_sha256):
            verifications.append(destination)
            if len(verifications) == 2:
                raise PatchError("injected verification")

        with mock.patch("htmod.lifecycle._verify_write", side_effect=fail_after_both), mock.patch(
            "htmod.lifecycle._restore_operation", side_effect=record_restore,
        ):
            result = self.service().apply(plan=plan)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(restores, ["sys/dx8.dll", "game/sys/game.dll"])
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"])
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.dx8["stock"])
        generation, = self.generations()
        journal = self.journal(generation)
        self.assertEqual(journal["status"], "rolled_back")
        self.assertTrue(all(item["status"] == "rolled_back" for item in journal["operations"]))

    def test_journal_update_failure_after_replace_rolls_back_and_retains_evidence(self):
        plan = self.prepare_two_binary_changes()
        import htmod.lifecycle as lifecycle
        real_write = lifecycle._write_journal
        failed = False

        def fail_installed(path, record):
            nonlocal failed
            if not failed and any(item["status"] == "installed" for item in record["operations"]):
                failed = True
                raise PatchError("injected journal")
            return real_write(path, record)

        with mock.patch("htmod.lifecycle._write_journal", side_effect=fail_installed):
            result = self.service().apply(plan=plan)
        self.assertTrue(failed)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"])
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.dx8["stock"])
        generation, = self.generations()
        self.assertEqual(self.journal(generation)["status"], "rolled_back")

    def test_rollback_never_overwrites_user_modified_installed_content(self):
        plan = self.prepare_two_binary_changes()
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        calls = 0

        def modify_then_fail(temporary, destination, *args, **kwargs):
            nonlocal calls
            index = calls
            calls += 1
            if index == 0:
                real_replace(temporary, destination, *args, **kwargs)
                (self.game / destination).write_bytes(b"user edit after install")
                return
            raise PatchError("injected second replacement")

        with mock.patch("htmod.lifecycle._replace_destination", side_effect=modify_then_fail):
            result = self.service().apply(plan=plan)
        self.assertEqual(result.status, "rollback_failed")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), b"user edit after install")
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.dx8["stock"])
        generation, = self.generations()
        journal = self.journal(generation)
        self.assertEqual(journal["status"], "rollback_failed")
        game_entry = next(
            item for item in journal["operations"] if item["destination"] == "game/sys/game.dll"
        )
        self.assertEqual(game_entry["status"], "rollback_failed")
        self.assertTrue((generation / game_entry["backup_path"]).is_file())
        self.assertTrue(any("modified" in warning for warning in result.warnings))

    def test_post_rename_failure_tracks_and_rolls_back_existing_destination(self):
        plan = self.prepare_two_binary_changes()
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        failed = False

        def rename_then_fail(*args, **kwargs):
            nonlocal failed
            real_replace(*args, **kwargs)
            if not failed:
                failed = True
                raise PatchError("injected post-rename failure")

        with mock.patch("htmod.lifecycle._replace_destination", side_effect=rename_then_fail):
            result = self.service().apply(plan=plan)
        self.assertTrue(failed)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"])
        self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.dx8["stock"])
        generation, = self.generations()
        self.assertEqual(self.journal(generation)["status"], "rolled_back")

    def test_post_rename_failure_removes_new_data_file(self):
        self.write_target("game/sys/game.dll", b"unknown")
        self.write_target("sys/dx8.dll", b"unknown")
        destination = self.release.mods["config"].files[0].destination
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        failed = False

        def rename_then_fail(*args, **kwargs):
            nonlocal failed
            real_replace(*args, **kwargs)
            if not failed:
                failed = True
                raise PatchError("injected post-rename failure")

        with mock.patch("htmod.lifecycle._replace_destination", side_effect=rename_then_fail):
            result = self.service().apply(requested=("config",))
        self.assertTrue(failed)
        self.assertEqual(result.status, "rolled_back")
        self.assertFalse((self.game / destination).exists())
        generation, = self.generations()
        entry = next(
            item for item in self.journal(generation)["operations"]
            if item["destination"] == destination
        )
        self.assertFalse(entry["existed"])
        self.assertEqual(entry["status"], "rolled_back")

    def test_failure_after_new_data_file_creation_removes_it(self):
        self.write_target("game/sys/game.dll", b"unknown")
        self.write_target("sys/dx8.dll", b"unknown")
        first = self.release.mods["config"].files[0].destination
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PatchError("injected later data failure")
            return real_replace(*args, **kwargs)

        with mock.patch("htmod.lifecycle._replace_destination", side_effect=fail_second):
            result = self.service().apply(requested=("config",))
        self.assertEqual(result.status, "rolled_back")
        self.assertFalse((self.game / first).exists())
        self.assertTrue(all(
            not (self.game / destination).exists()
            for destination in self.data_bytes["config"]
        ))

    def test_backup_ancestry_is_fsynced_before_destination_replacement(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        backups = self.root / "outer" / "inner" / "backups"
        import htmod.lifecycle as lifecycle
        real_fsync = lifecycle._fsync_directory
        real_replace = lifecycle._replace_destination
        fsynced = []
        before_replace = []

        def record_fsync(path):
            fsynced.append(Path(path))
            return real_fsync(path)

        def capture_replace(*args, **kwargs):
            if not before_replace:
                before_replace.extend(fsynced)
            return real_replace(*args, **kwargs)

        with mock.patch("htmod.lifecycle._fsync_directory", side_effect=record_fsync), mock.patch(
            "htmod.lifecycle._replace_destination", side_effect=capture_replace,
        ):
            result = self.service(backups=backups).apply(requested=("camera",))
        self.assertEqual(result.status, "installed")
        generation, = self.generations(backups)
        required = {
            self.root / "outer",
            self.root / "outer/inner",
            backups,
            generation,
            generation / "files",
            generation / "files/game",
            generation / "files/game/sys",
        }
        self.assertTrue(required.issubset(set(before_replace)), before_replace)
        self.assertLess(before_replace.index(self.root / "outer"), before_replace.index(backups))
        self.assertLess(before_replace.index(backups), before_replace.index(generation))

    def test_external_edit_between_source_check_and_rename_is_preserved(self):
        plan = self.prepare_two_binary_changes()
        original = self.game_variants["0000"]
        later = b"later writer content"
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        edited = False

        def edit_then_replace(temporary, destination, *args, **kwargs):
            nonlocal edited
            if not edited:
                destination.write_bytes(later)
                edited = True
            return real_replace(temporary, destination, *args, **kwargs)

        with mock.patch("htmod.lifecycle._replace_destination", side_effect=edit_then_replace):
            result = self.service().apply(plan=plan)
        self.assertTrue(edited)
        self.assertEqual(result.status, "rollback_failed")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), later)
        generation, = self.generations()
        entry = next(
            item for item in self.journal(generation)["operations"]
            if item["destination"] == "game/sys/game.dll"
        )
        self.assertEqual((generation / entry["backup_path"]).read_bytes(), original)
        self.assertTrue(any("modified" in warning for warning in result.warnings))

    def test_destination_parent_swap_cannot_replace_outside_file(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "game.dll").write_bytes(b"outside")
        parent = self.game / "game/sys"
        import htmod.lifecycle as lifecycle
        real_temporary = lifecycle._temporary_destination
        swapped = False

        def swap_then_temporary(destination, data, *args, **kwargs):
            nonlocal swapped
            if destination.parent == parent and not swapped:
                parent.rename(self.game / "game/sys-original")
                parent.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_temporary(destination, data, *args, **kwargs)

        with mock.patch("htmod.lifecycle._temporary_destination", side_effect=swap_then_temporary):
            result = self.service().apply(requested=("camera",))
        self.assertTrue(swapped)
        self.assertNotEqual(result.status, "installed")
        self.assertEqual((outside / "game.dll").read_bytes(), b"outside")
        generation, = self.generations()
        entry = next(
            item for item in self.journal(generation)["operations"]
            if item["destination"] == "game/sys/game.dll"
        )
        self.assertEqual((generation / entry["backup_path"]).read_bytes(), self.game_variants["0000"])

    def test_backup_parent_swap_cannot_write_outside_generation(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        outside = self.root / "outside"
        outside.mkdir()
        import htmod.lifecycle as lifecycle
        real_backup = lifecycle._write_backup
        swapped = False

        def swap_then_backup(generation, operation, data):
            nonlocal swapped
            if not swapped:
                files = generation / "files"
                files.mkdir()
                files.rename(generation / "files-original")
                files.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_backup(generation, operation, data)

        with mock.patch("htmod.lifecycle._write_backup", side_effect=swap_then_backup):
            result = self.service().apply(requested=("camera",))
        self.assertTrue(swapped)
        self.assertEqual(result.status, "rolled_back")
        self.assertFalse((outside / "game/sys/game.dll").exists())
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"])

    def test_no_op_rechecks_plan_while_holding_lock_without_backup(self):
        config = materialize_data_bytes(self.release, ("config",))["config"]
        for destination, content in config.items():
            self.write_target(destination, content)
        service = self.service()
        import htmod.lifecycle as lifecycle
        real_lock = lifecycle._transaction_lock
        real_plan = service.plan
        calls = 0
        lock_active = False

        @contextlib.contextmanager
        def record_lock(root, transaction_id):
            nonlocal lock_active
            with real_lock(root, transaction_id):
                lock_active = True
                yield
                lock_active = False

        def recheck(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.assertTrue(lock_active)
                destination = self.release.mods["config"].files[0].destination
                self.write_target(destination, b"changed while acquiring lock")
            return real_plan(*args, **kwargs)

        with mock.patch("htmod.lifecycle._transaction_lock", side_effect=record_lock), mock.patch.object(
            service, "plan", side_effect=recheck,
        ):
            with self.assertRaisesRegex(PatchError, "stale|changed|plan"):
                service.apply(requested=("config",))
        self.assertEqual(calls, 3)
        self.assertEqual(self.generations(), [])
        destination = self.release.mods["config"].files[0].destination
        self.assertEqual((self.game / destination).read_bytes(), b"changed while acquiring lock")


if __name__ == "__main__":
    unittest.main()
