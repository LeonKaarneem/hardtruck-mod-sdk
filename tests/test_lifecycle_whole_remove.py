import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

from tests.test_lifecycle_apply import ApplyFixture
from htmod.errors import PatchError
from htmod.lifecycle import Lifecycle


class WholeInstallRemovalTests(ApplyFixture):
    def service(self, backups=None):
        return Lifecycle(self.release, self.game, self.catalog, backup_dir=backups)

    def generations(self):
        root = self.game / ".htmod/backups"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def journal(self, generation):
        return json.loads((generation / "journal.json").read_text(encoding="utf-8"))

    def write_json_history(self, name, entries, status="Installed"):
        generation = self.game / ".htmod/backups" / name
        generation.mkdir(parents=True)
        record = {
            "schema_version": 1,
            "status": status,
            "created": "2026-10-30T00:00:00Z",
            "game_path": str(self.game),
            "entries": entries,
        }
        (generation / "installation.json").write_text(json.dumps(record), encoding="utf-8")
        return generation

    def test_whole_removal_restores_exact_canonical_legacy_and_estonian_incoming_bytes(self):
        for name, source in (
            ("canonical", self.game_variants["0000"]),
            ("legacy", self.legacy),
            ("estonian", self.estonian_game),
        ):
            with self.subTest(source=name):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())
                self.write_target("game/sys/game.dll", source)
                self.write_target("sys/dx8.dll", self.dx8["stock"])
                installed = self.service().apply(requested=("camera",), exact=True)
                self.assertEqual(installed.status, "installed")

                result = self.service().remove(all=True)

                self.assertEqual(result.status, "removed")
                self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), source)
                self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), self.dx8["stock"])
                self.assertEqual(len(self.generations()), 2)
                removal = next(
                    path for path in self.generations()
                    if self.journal(path).get("kind") == "remove"
                )
                journal = self.journal(removal)
                self.assertEqual(journal["mode"], "backup_history")
                entry = journal["operations"][0]
                self.assertEqual(entry["action"], "restore")
                self.assertEqual((removal / entry["backup_path"]).read_bytes(), self.game_variants["0001"])

                repeated = self.service().remove(all=True)
                self.assertEqual(repeated.status, "no_op")
                self.assertTrue(repeated.no_op)
                self.assertEqual(len(self.generations()), 2)

    def test_whole_removal_restores_incoming_data_and_preserves_saves_and_unrelated_files(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        existing = self.release.mods["config"].files[0].destination
        self.write_target(existing, b"exact incoming data")
        self.write_target("game/usr/save/profile.sav", b"save bytes")
        self.write_target("unrelated.txt", b"unrelated")
        self.service().apply(requested=("config",), exact=True)

        result = self.service().remove(all=True)

        self.assertEqual(result.status, "removed")
        self.assertEqual((self.game / existing).read_bytes(), b"exact incoming data")
        for destination in list(self.data_bytes["config"])[1:]:
            self.assertFalse((self.game / destination).exists(), destination)
        self.assertEqual((self.game / "game/usr/save/profile.sav").read_bytes(), b"save bytes")
        self.assertEqual((self.game / "unrelated.txt").read_bytes(), b"unrelated")

    def test_modified_newest_content_blocks_older_history_for_destination(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.service().apply(requested=("camera",), exact=True)
        self.service().apply(requested=("physics",))
        self.write_target("game/sys/game.dll", b"user modified binary")
        before_count = len(self.generations())

        result = self.service().remove(all=True)

        self.assertEqual(result.status, "partial")
        self.assertFalse(result.no_op)
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), b"user modified binary")
        self.assertEqual(len(self.generations()), before_count + 1)
        operations = [
            operation for operation in result.operations
            if operation.destination == "game/sys/game.dll"
        ]
        self.assertEqual(len(operations), 2)
        self.assertEqual(operations[0].status, "skipped_modified")
        self.assertEqual(operations[1].status, "skipped_blocked")

    def test_no_backup_history_never_invents_baseline_or_deletes_unmanaged_files(self):
        self.write_target("game/sys/game.dll", self.game_variants["1111"])
        self.write_target("sys/dx8.dll", b"unmanaged dx8")
        self.write_data(("config",))
        before = self.tree_hashes()

        result = self.service().remove(all=True)

        self.assertEqual(result.status, "no_op")
        self.assertTrue(result.no_op)
        self.assertEqual(result.operations, ())
        after = self.tree_hashes()
        after.pop(".htmod/transaction.lock", None)
        self.assertEqual(after, before)
        self.assertEqual(self.generations(), [])

    def test_schema1_json_and_shell_tsv_histories_restore_exact_bytes(self):
        for format_name in ("json", "tsv"):
            with self.subTest(format=format_name):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())
                source = self.estonian_game if format_name == "json" else self.legacy
                self.write_target("game/sys/game.dll", self.game_variants["0001"])
                destination = "game/sys/game.dll"
                if format_name == "json":
                    generation = self.write_json_history("2026-10-30-json", [{
                        "destination": destination,
                        "installed_sha256": self.status().targets[0].sha256,
                        "existed": True,
                        "backup_file": f"files/{destination}",
                        "original_sha256": self._sha(source),
                    }])
                else:
                    generation = self.game / ".htmod/backups/shell-20261030-1"
                    generation.mkdir(parents=True)
                    (generation / "installation.tsv").write_text(
                        f"replaced\t{destination}\t{self._sha(source)}\t"
                        f"{self._sha(self.game_variants['0001'])}\tfiles/{destination}\n",
                        encoding="utf-8",
                    )
                backup = generation / f"files/{destination}"
                backup.parent.mkdir(parents=True)
                backup.write_bytes(source)
                old_manifest = (
                    generation / ("installation.json" if format_name == "json" else "installation.tsv")
                ).read_bytes()

                result = self.service().remove(all=True)

                self.assertEqual(result.status, "removed")
                self.assertEqual((self.game / destination).read_bytes(), source)
                self.assertEqual(
                    (generation / ("installation.json" if format_name == "json" else "installation.tsv")).read_bytes(),
                    old_manifest,
                )

    @staticmethod
    def _sha(data):
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def test_missing_corrupt_escaping_and_disallowed_evidence_is_refused_without_mutation(self):
        for case in ("missing", "corrupt", "escaping", "disallowed"):
            with self.subTest(case=case):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("prism3d.exe", (self.payload / "prism3d-cap.exe").read_bytes())
                self.write_target("game/sys/game.dll", self.game_variants["0001"])
                destination = "game/sys/game.dll" if case != "disallowed" else "unmanaged.exe"
                backup_path = f"files/{destination}" if case != "escaping" else "../escape.dll"
                generation = self.write_json_history(f"2026-10-30-{case}", [{
                    "destination": destination,
                    "installed_sha256": self._sha(self.game_variants["0001"]),
                    "existed": True,
                    "backup_file": backup_path,
                    "original_sha256": self._sha(self.game_variants["0000"]),
                }])
                if case not in {"missing", "escaping"}:
                    backup = generation / backup_path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(b"corrupt" if case == "corrupt" else self.game_variants["0000"])
                before = self.tree_hashes()

                with self.assertRaisesRegex(PatchError, "backup|evidence|destination|containment|allowed"):
                    self.service().remove(all=True)
                self.assertEqual(self.tree_hashes(), before)

    def test_whole_dry_run_prints_history_plan_without_writes(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.service().apply(requested=("camera", "dx8_vsync"), exact=True)
        before = self.tree_hashes()

        plan = self.service().remove(all=True, dry_run=True)

        value = plan.to_dict()
        self.assertEqual(value["mode"], "backup_history")
        self.assertFalse(value["no_op"])
        self.assertEqual(len(value["history_changes"]), 3)
        self.assertTrue(all(change["action"] == "restore" for change in value["history_changes"]))
        self.assertNotIn(str(self.game), json.dumps(value))
        self.assertEqual(self.tree_hashes(), before)

    def test_failure_during_history_restore_rolls_back_in_reverse_and_retains_journal(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.service().apply(requested=("camera", "dx8_vsync"), exact=True)
        installed = {
            "game/sys/game.dll": self.game_variants["0001"],
            "sys/dx8.dll": self.dx8["vsync"],
        }
        import htmod.lifecycle as lifecycle
        real_replace = lifecycle._replace_destination
        real_restore = lifecycle._restore_operation
        replacements = []
        restores = []

        def replace_and_fail(temporary, destination, *args, **kwargs):
            relative = destination.relative_to(self.game).as_posix()
            replacements.append(relative)
            if len(replacements) == 2:
                raise PatchError("injected history restore")
            return real_replace(temporary, destination, *args, **kwargs)

        def record_restore(*args):
            restores.append(args[1])
            return real_restore(*args)

        with mock.patch("htmod.lifecycle._replace_destination", side_effect=replace_and_fail), mock.patch(
            "htmod.lifecycle._restore_operation", side_effect=record_restore,
        ):
            result = self.service().remove(all=True)

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(restores, list(reversed(replacements[:2])))
        for destination, content in installed.items():
            self.assertEqual((self.game / destination).read_bytes(), content)
        removal = next(path for path in self.generations() if self.journal(path).get("kind") == "remove")
        self.assertEqual(self.journal(removal)["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
