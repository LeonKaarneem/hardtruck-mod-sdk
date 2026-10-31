import contextlib
import io
import json
import unittest

from tests.test_lifecycle_apply import ApplyFixture
from htmod import cli
from htmod.lifecycle import Lifecycle


class LifecycleRemovalCliTests(ApplyFixture):
    def service(self):
        return Lifecycle(self.release, self.game, self.catalog)

    def generations(self):
        root = self.game / ".htmod/backups"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def run_cli(self, arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_selective_remove_dry_run_and_result_json(self):
        from tests.test_lifecycle import ROOT
        self.write_target("game/sys/game.dll", self.game_variants["0011"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.write_data(("config",))
        base = [
            "--root", str(ROOT), "remove", "--game-dir", str(self.game),
            "--migrations", str(ROOT / "modpack/migrations.json"),
            "--mod", "camera", "--mod", "config",
        ]
        before = self.tree_hashes()

        returncode, stdout, stderr = self.run_cli(base + ["--dry-run"])

        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(stderr, "")
        plan = json.loads(stdout)
        self.assertEqual(plan["mode"], "selective")
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

        returncode, stdout, stderr = self.run_cli(base)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertEqual(result["status"], "removed")
        self.assertEqual(len(stdout.splitlines()), 1)
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0010"],
        )
        for destination in self.data_bytes["config"]:
            self.assertFalse((self.game / destination).exists())

    def test_whole_remove_partial_and_repeat_exit_status_matches_json(self):
        from tests.test_lifecycle import ROOT
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        self.service().apply(requested=("camera",), exact=True)
        base = [
            "--root", str(ROOT), "remove", "--game-dir", str(self.game),
            "--migrations", str(ROOT / "modpack/migrations.json"), "--all",
        ]
        self.write_target("game/sys/game.dll", b"user edit")
        generation_count = len(self.generations())

        returncode, stdout, stderr = self.run_cli(base)
        self.assertEqual(returncode, 1, stderr)
        self.assertEqual(json.loads(stdout)["status"], "partial")
        self.assertEqual(len(self.generations()), generation_count)

        self.write_target("game/sys/game.dll", self.game_variants["0001"])
        returncode, stdout, stderr = self.run_cli(base + ["--dry-run"])
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["mode"], "backup_history")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0001"])
        self.assertEqual(len(self.generations()), generation_count)

        returncode, stdout, stderr = self.run_cli(base)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "removed")
        returncode, stdout, stderr = self.run_cli(base)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "no_op")
        self.assertEqual(len(self.generations()), generation_count + 1)

    def test_recover_success_and_partial_emit_one_json_result(self):
        from tests.test_lifecycle import ROOT
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        applied = self.service().apply(requested=("camera",), exact=True)
        generation = self.game / ".htmod/backups" / applied.transaction_id
        journal_path = generation / "journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["status"] = "in_progress"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        base = ["--root", str(ROOT), "recover", "--game-dir", str(self.game)]

        returncode, stdout, stderr = self.run_cli(base + ["--transaction-id", applied.transaction_id])
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "recovered")
        self.assertEqual(len(stdout.splitlines()), 1)

        second = self.service().apply(requested=("camera",), exact=True)
        generation = self.game / ".htmod/backups" / second.transaction_id
        journal_path = generation / "journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["status"] = "rollback_failed"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        self.write_target("game/sys/game.dll", b"external edit")
        returncode, stdout, stderr = self.run_cli(base + ["--transaction-id", second.transaction_id])
        self.assertEqual(returncode, 1, stderr)
        self.assertEqual(json.loads(stdout)["status"], "partial")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), b"external edit")

    def test_invalid_remove_and_recover_requests_emit_no_partial_json(self):
        from tests.test_lifecycle import ROOT
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        before = self.tree_hashes()
        remove = [
            "--root", str(ROOT), "remove", "--game-dir", str(self.game),
            "--migrations", str(ROOT / "modpack/migrations.json"),
        ]
        recover = ["--root", str(ROOT), "recover", "--game-dir", str(self.game)]
        for arguments in (remove, remove + ["--all", "--mod", "camera"], recover + ["--transaction-id", "missing"]):
            with self.subTest(arguments=arguments):
                returncode, stdout, stderr = self.run_cli(arguments)
                self.assertEqual(returncode, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])


if __name__ == "__main__":
    unittest.main()
