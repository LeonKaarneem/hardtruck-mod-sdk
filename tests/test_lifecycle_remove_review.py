import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

from tests.test_lifecycle_apply import ApplyFixture
from htmod.errors import PatchError
from htmod.lifecycle import Lifecycle


class ReviewFixture(ApplyFixture):
    def service(self):
        return Lifecycle(self.release, self.game, self.catalog)

    def generations(self):
        root = self.game / ".htmod/backups"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def journal(self, generation):
        return json.loads((generation / "journal.json").read_text(encoding="utf-8"))

    def write_journal(self, generation, journal):
        (generation / "journal.json").write_text(json.dumps(journal), encoding="utf-8")

    def install_binaries(self, game="0000"):
        self.write_target("game/sys/game.dll", self.game_variants[game])
        self.write_target("sys/dx8.dll", self.dx8["stock"])

    def apply_camera(self):
        self.install_binaries()
        result = self.service().apply(requested=("camera",), exact=True)
        return result, self.game / ".htmod/backups" / result.transaction_id

    def apply_new_data(self):
        self.install_binaries()
        result = self.service().apply(requested=("config",), exact=True)
        return result, self.game / ".htmod/backups" / result.transaction_id


class ReplacementRaceTests(ReviewFixture):
    def test_new_file_appearing_before_replace_is_preserved_on_both_backends(self):
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        for posix in (True, False):
            with self.subTest(posix=posix):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())
                self.install_binaries()
                destination = self.game / self.release.mods["config"].files[0].destination
                created = False

                def create_then_replace(temporary, target, *args, **kwargs):
                    nonlocal created
                    if target == destination and not created:
                        target.write_bytes(b"user created during apply")
                        created = True
                    return real_replace(temporary, target, *args, **kwargs)

                with mock.patch("htmod.lifecycle._supports_directory_fd", return_value=posix), mock.patch(
                    "htmod.lifecycle._replace_destination", side_effect=create_then_replace,
                ):
                    result = self.service().apply(requested=("config",))
                self.assertTrue(created)
                self.assertNotEqual(result.status, "installed")
                self.assertEqual(destination.read_bytes(), b"user created during apply")


class DeleteRaceTests(ReviewFixture):
    def edit_during_operation_journal(self, real_write, destination, content):
        def write_and_edit(path, journal):
            if any(
                operation["destination"] == destination
                and operation["status"] == "in_progress"
                for operation in journal["operations"]
            ):
                self.write_target(destination, content)
            return real_write(path, journal)
        return write_and_edit

    def test_selective_delete_rechecks_content_after_journal_update(self):
        import htmod.lifecycle as lifecycle
        self.install_binaries()
        self.write_data(("config",))
        destination = self.release.mods["config"].files[0].destination

        with mock.patch(
            "htmod.lifecycle._write_journal",
            side_effect=self.edit_during_operation_journal(
                lifecycle._write_journal, destination, b"selective delete race",
            ),
        ):
            result = self.service().remove(requested=("config",))

        self.assertEqual(result.status, "rollback_failed")
        self.assertEqual((self.game / destination).read_bytes(), b"selective delete race")

    def test_whole_history_delete_rechecks_content_after_journal_update(self):
        import htmod.lifecycle as lifecycle
        self.apply_new_data()
        destination = self.release.mods["config"].files[0].destination

        with mock.patch(
            "htmod.lifecycle._write_journal",
            side_effect=self.edit_during_operation_journal(
                lifecycle._write_journal, destination, b"whole delete race",
            ),
        ):
            result = self.service().remove(all=True)

        self.assertEqual(result.status, "rollback_failed")
        self.assertEqual((self.game / destination).read_bytes(), b"whole delete race")

    def test_fallback_recovery_delete_rechecks_after_path_validation(self):
        import htmod.lifecycle as lifecycle
        applied, generation = self.apply_new_data()
        journal = self.journal(generation)
        journal["status"] = "in_progress"
        for operation in journal["operations"]:
            operation["status"] = "in_progress"
        self.write_journal(generation, journal)
        destination = self.release.mods["config"].files[0].destination
        real_validate = lifecycle._validate_destination

        def validate_then_edit(root, relative, create=False):
            path = real_validate(root, relative, create=create)
            if create and relative == destination:
                self.write_target(relative, b"fallback recovery delete race")
            return path

        with mock.patch("htmod.lifecycle._supports_directory_fd", return_value=False), mock.patch(
            "htmod.lifecycle._validate_destination", side_effect=validate_then_edit,
        ):
            result = self.service().recover(applied.transaction_id)

        self.assertEqual(result.status, "partial")
        self.assertEqual((self.game / destination).read_bytes(), b"fallback recovery delete race")


class DurableOrderingTests(ReviewFixture):
    def test_whole_history_uses_journal_sequence_not_directory_mtime(self):
        first, first_generation = self.apply_camera()
        second = self.service().apply(requested=("physics",))
        second_generation = self.game / ".htmod/backups" / second.transaction_id
        os.utime(first_generation, (200, 200))
        os.utime(second_generation, (100, 100))

        result = self.service().remove(all=True)

        self.assertEqual(result.status, "removed")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"],
        )
        self.assertIsInstance(self.journal(first_generation)["created_ns"], int)
        self.assertIsInstance(self.journal(second_generation)["created_ns"], int)

    def test_recovery_candidate_uses_journal_sequence_not_directory_mtime(self):
        first, first_generation = self.apply_camera()
        second = self.service().apply(requested=("physics",))
        second_generation = self.game / ".htmod/backups" / second.transaction_id
        for generation in (first_generation, second_generation):
            journal = self.journal(generation)
            journal["status"] = "in_progress"
            journal["operations"][0]["status"] = "in_progress"
            self.write_journal(generation, journal)
        os.utime(first_generation, (200, 200))
        os.utime(second_generation, (100, 100))

        result = self.service().recover()

        self.assertEqual(result.transaction_id, second.transaction_id)
        self.assertEqual(result.status, "recovered")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0001"],
        )


class VirtualRecoveryTests(ReviewFixture):
    def prepare_final_restored_whole_journal(self):
        self.apply_camera()
        self.service().apply(requested=("physics",))
        removed = self.service().remove(all=True)
        generation = self.game / ".htmod/backups" / removed.transaction_id
        journal = self.journal(generation)
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        journal["status"] = "in_progress"
        return removed, generation, journal

    def test_two_pending_repeated_destination_operations_recover_in_one_invocation(self):
        removed, generation, journal = self.prepare_final_restored_whole_journal()
        for operation in journal["operations"]:
            operation["status"] = "in_progress"
        self.write_journal(generation, journal)

        result = self.service().recover(removed.transaction_id)

        self.assertEqual(result.status, "recovered")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0011"],
        )
        self.assertEqual(self.journal(generation)["status"], "recovered")

    def test_crash_after_final_restore_before_journal_update_recovers_in_one_invocation(self):
        removed, generation, journal = self.prepare_final_restored_whole_journal()
        journal["operations"][0]["status"] = "restored"
        journal["operations"][1]["status"] = "in_progress"
        self.write_journal(generation, journal)

        result = self.service().recover(removed.transaction_id)

        self.assertEqual(result.status, "recovered")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0011"],
        )
        self.assertEqual(self.journal(generation)["status"], "recovered")


class Schema1ReviewTests(ReviewFixture):
    def write_history(self, game_path, status="Installed", skipped=()):
        self.install_binaries(game="0001")
        destination = "game/sys/game.dll"
        generation = self.game / ".htmod/backups/schema1-review"
        backup = generation / f"files/{destination}"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(self.game_variants["0000"])
        import hashlib
        record = {
            "schema_version": 1,
            "status": status,
            "created": "2026-10-30T00:00:00Z",
            "game_path": game_path,
            "entries": [{
                "destination": destination,
                "installed_sha256": hashlib.sha256(self.game_variants["0001"]).hexdigest(),
                "existed": True,
                "backup_file": f"files/{destination}",
                "original_sha256": hashlib.sha256(self.game_variants["0000"]).hexdigest(),
            }],
            "skipped": list(skipped),
        }
        (generation / "installation.json").write_text(json.dumps(record), encoding="utf-8")
        return generation

    def test_schema1_other_game_history_is_not_imported(self):
        generation = self.write_history(str(self.root / "another game"))

        result = self.service().remove(all=True)

        self.assertEqual(result.status, "no_op")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0001"],
        )
        self.assertEqual(list(generation.parent.iterdir()), [generation])

    def test_schema1_uninstall_partial_retains_only_unresolved_skipped_entries(self):
        generation = self.write_history(
            str(self.game), status="UninstallPartial", skipped=("game/sys/game.dll",),
        )
        completed = self.release.mods["config"].files[0].destination
        completed_before = b"completed incoming bytes"
        completed_after = self.data_bytes["config"][completed]
        backup = generation / f"files/{completed}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(completed_before)
        self.write_target(completed, completed_after)
        manifest_path = generation / "installation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        import hashlib
        manifest["entries"].append({
            "destination": completed,
            "installed_sha256": hashlib.sha256(completed_after).hexdigest(),
            "existed": True,
            "backup_file": f"files/{completed}",
            "original_sha256": hashlib.sha256(completed_before).hexdigest(),
        })
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.service().remove(all=True)

        self.assertEqual(result.status, "removed")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"],
        )
        self.assertEqual((self.game / completed).read_bytes(), completed_after)
        self.assertEqual(
            [operation.destination for operation in result.operations],
            ["game/sys/game.dll"],
        )


class RecoveryGenerationSafetyTests(ReviewFixture):
    def test_explicit_recovery_rejects_parent_and_non_simple_generation_names(self):
        backup_root = self.game / ".htmod/backups"
        backup_root.mkdir(parents=True)
        (self.game / ".htmod/journal.json").write_text(json.dumps({
            "schema_version": 1,
            "transaction_id": "..",
            "status": "installed",
            "operations": [],
        }), encoding="utf-8")
        for transaction_id in ("", ".", "..", "nested/id"):
            with self.subTest(transaction_id=transaction_id), self.assertRaisesRegex(
                PatchError, "transaction|generation|containment",
            ):
                self.service().recover(transaction_id)

    def test_explicit_recovery_rejects_symlink_generation_and_automatic_ignores_it(self):
        backup_root = self.game / ".htmod/backups"
        backup_root.mkdir(parents=True)
        outside = self.root / "outside-generation"
        outside.mkdir()
        (outside / "journal.json").write_text(json.dumps({
            "schema_version": 1,
            "transaction_id": "linked",
            "status": "installed",
            "operations": [],
        }), encoding="utf-8")
        (backup_root / "linked").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(PatchError, "generation|containment|symlink"):
            self.service().recover("linked")
        self.assertEqual(self.service().recover().status, "no_op")


if __name__ == "__main__":
    unittest.main()
