import json
from pathlib import Path
import shutil
import unittest

from tests.test_lifecycle_apply import ApplyFixture
from htmod.errors import PatchError
from htmod.lifecycle import Lifecycle
from htmod.runner import materialize_data_bytes


class LifecycleRecoveryTests(ApplyFixture):
    def service(self):
        return Lifecycle(self.release, self.game, self.catalog)

    def generations(self):
        root = self.game / ".htmod/backups"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def journal(self, generation):
        return json.loads((generation / "journal.json").read_text(encoding="utf-8"))

    def write_journal(self, generation, value):
        (generation / "journal.json").write_text(json.dumps(value), encoding="utf-8")

    def apply_camera(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        result = self.service().apply(requested=("camera",), exact=True)
        generation = next(path for path in self.generations() if path.name == result.transaction_id)
        return result, generation

    def test_recovery_rolls_back_prepared_in_progress_partial_and_rollback_failed_journals(self):
        for state in ("prepared", "in_progress", "partial", "rollback_failed"):
            with self.subTest(state=state):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())
                result, generation = self.apply_camera()
                journal = self.journal(generation)
                journal["status"] = state
                journal["operations"][0]["status"] = state
                self.write_journal(generation, journal)
                before_count = len(self.generations())

                recovered = self.service().recover(result.transaction_id)

                self.assertEqual(recovered.status, "recovered")
                self.assertFalse(recovered.no_op)
                self.assertEqual(
                    (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"],
                )
                evidence = self.journal(generation)
                self.assertEqual(evidence["status"], "recovered")
                self.assertEqual(evidence["operations"][0]["status"], "recovered")
                entry = evidence["operations"][0]
                self.assertEqual((generation / entry["backup_path"]).read_bytes(), self.game_variants["0000"])
                self.assertEqual(len(self.generations()), before_count)

    def test_valid_no_op_recovery_updates_evidence_without_new_generation(self):
        self.write_target("prism3d.exe", (self.payload / "prism3d-stock.exe").read_bytes())
        result, generation = self.apply_camera()
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        journal = self.journal(generation)
        journal["status"] = "prepared"
        journal["operations"][0]["status"] = "prepared"
        self.write_journal(generation, journal)
        before_count = len(self.generations())

        recovered = self.service().recover(result.transaction_id)

        self.assertEqual(recovered.status, "no_op")
        self.assertTrue(recovered.no_op)
        self.assertEqual(self.journal(generation)["status"], "recovered")
        self.assertEqual(len(self.generations()), before_count)

    def test_recovery_without_interrupted_transaction_is_no_op(self):
        result, generation = self.apply_camera()

        recovered = self.service().recover()

        self.assertEqual(recovered.status, "no_op")
        self.assertTrue(recovered.no_op)
        self.assertEqual(recovered.operations, ())
        self.assertEqual(self.journal(generation)["status"], "installed")
        self.assertEqual(len(self.generations()), 1)

    def test_recovery_none_selects_newest_interrupted_journal(self):
        result, generation = self.apply_camera()
        second = self.service().apply(requested=("physics",))
        second_generation = next(path for path in self.generations() if path.name == second.transaction_id)
        journal = self.journal(second_generation)
        journal["status"] = "in_progress"
        journal["operations"][0]["status"] = "in_progress"
        self.write_journal(second_generation, journal)

        recovered = self.service().recover()

        self.assertEqual(recovered.transaction_id, second.transaction_id)
        self.assertEqual(recovered.status, "recovered")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0001"],
        )
        self.assertEqual(self.journal(generation)["status"], "installed")
        self.assertEqual(self.journal(second_generation)["status"], "recovered")

    def test_recovery_restores_interrupted_selective_data_deletion(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.write_data(("config",))
        removed = self.service().remove(requested=("config",))
        generation = next(path for path in self.generations() if path.name == removed.transaction_id)
        journal = self.journal(generation)
        journal["status"] = "in_progress"
        for operation in journal["operations"]:
            operation["status"] = "in_progress"
        self.write_journal(generation, journal)

        recovered = self.service().recover(removed.transaction_id)

        self.assertEqual(recovered.status, "recovered")
        expected = materialize_data_bytes(self.release, ("config",))["config"]
        for destination, content in expected.items():
            self.assertEqual((self.game / destination).read_bytes(), content)
        self.assertEqual(self.journal(generation)["status"], "recovered")

    def test_recovery_refuses_externally_modified_content_and_retains_evidence(self):
        result, generation = self.apply_camera()
        self.write_target("game/sys/game.dll", b"external user edit")
        journal = self.journal(generation)
        journal["status"] = "rollback_failed"
        journal["operations"][0]["status"] = "rollback_failed"
        self.write_journal(generation, journal)

        recovered = self.service().recover(result.transaction_id)

        self.assertEqual(recovered.status, "partial")
        self.assertFalse(recovered.no_op)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), b"external user edit")
        self.assertEqual((self.game / "prism3d.exe").read_bytes(), (self.payload / "prism3d-cap.exe").read_bytes())
        evidence = self.journal(generation)
        self.assertEqual(evidence["status"], "partial")
        self.assertEqual(evidence["operations"][0]["status"], "rollback_failed")
        entry = evidence["operations"][0]
        self.assertTrue((generation / entry["backup_path"]).is_file())
        self.assertTrue(any("modified" in warning for warning in recovered.warnings))

    def test_missing_or_corrupt_recovery_evidence_fails_without_game_mutation(self):
        for case in ("missing", "corrupt"):
            with self.subTest(case=case):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())
                result, generation = self.apply_camera()
                journal = self.journal(generation)
                journal["status"] = "in_progress"
                journal["operations"][0]["status"] = "in_progress"
                self.write_journal(generation, journal)
                backup = generation / journal["operations"][0]["backup_path"]
                if case == "missing":
                    backup.unlink()
                else:
                    backup.write_bytes(b"corrupt")
                before = (self.game / "game/sys/game.dll").read_bytes()

                recovered = self.service().recover(result.transaction_id)

                self.assertEqual(recovered.status, "partial")
                self.assertTrue(recovered.no_op)
                self.assertTrue(recovered.errors)
                self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), before)
                self.assertEqual(self.journal(generation)["status"], "in_progress")
                self.assertEqual(len(self.generations()), 1)

    def test_recovery_accepts_repeated_destinations_in_whole_removal_journal(self):
        result, _ = self.apply_camera()
        self.service().apply(requested=("physics",))
        removed = self.service().remove(all=True)
        generation = self.game / ".htmod/backups" / removed.transaction_id
        journal = self.journal(generation)
        self.assertEqual(len(journal["operations"]), 3)
        self.assertEqual(
            [item["destination"] for item in journal["operations"]],
            ["game/sys/game.dll", "game/sys/game.dll", "prism3d.exe"],
        )
        self.write_target("game/sys/game.dll", self.game_variants["0001"])
        journal["status"] = "in_progress"
        journal["operations"][0]["status"] = "restored"
        journal["operations"][1]["status"] = "prepared"
        self.write_journal(generation, journal)

        recovered = self.service().recover(removed.transaction_id)

        self.assertEqual(recovered.status, "recovered")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0011"],
        )
        self.assertEqual(self.journal(generation)["status"], "recovered")

    def test_unknown_transaction_and_corrupt_journal_fail_cleanly(self):
        result, generation = self.apply_camera()
        with self.assertRaisesRegex(PatchError, "transaction|journal|found"):
            self.service().recover("does-not-exist")
        (generation / "journal.json").write_text("{broken", encoding="utf-8")
        before = (self.game / "game/sys/game.dll").read_bytes()
        with self.assertRaisesRegex(PatchError, "journal|evidence|read"):
            self.service().recover(result.transaction_id)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
