import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from tests.test_lifecycle import ROOT
from tests.test_lifecycle_apply import ApplyFixture
from htmod.errors import PatchError
from htmod.lifecycle import Lifecycle, _transaction_lock


class CrashLockTests(ApplyFixture):
    def service(self):
        return Lifecycle(self.release, self.game, self.catalog)

    def apply_camera(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        result = self.service().apply(requested=("camera",), exact=True)
        generation = self.game / ".htmod/backups" / result.transaction_id
        journal_path = generation / "journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["status"] = "in_progress"
        journal["operations"][0]["status"] = "in_progress"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        return result

    def test_process_death_releases_lock_and_recovery_uses_crash_left_file(self):
        applied = self.apply_camera()
        script = (
            "import os, sys; "
            "from pathlib import Path; "
            "from htmod.lifecycle import _transaction_lock; "
            "root = Path(sys.argv[1]); "
            "lock = _transaction_lock(root, 'crashed-process'); "
            "lock.__enter__(); "
            "os._exit(23)"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "sdk")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.game)],
            env=environment,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 23, completed.stderr)
        self.assertTrue((self.game / ".htmod/transaction.lock").is_file())
        recovered = self.service().recover(applied.transaction_id)
        self.assertEqual(recovered.status, "recovered")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"],
        )

    def test_live_os_lock_still_excludes_lifecycle_transactions(self):
        self.write_target("game/sys/game.dll", self.game_variants["0000"])
        self.write_target("sys/dx8.dll", self.dx8["stock"])
        with _transaction_lock(self.game, "live-owner"):
            with self.assertRaisesRegex(PatchError, "lock|concurrent"):
                self.service().apply(requested=("camera",))
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0000"],
        )
        self.assertEqual(list((self.game / ".htmod/backups").glob("*")), [])


class RepeatedHistoryBaselineTests(ApplyFixture):
    def service(self):
        return Lifecycle(self.release, self.game, self.catalog)

    def generations(self):
        root = self.game / ".htmod/backups"
        return sorted(path for path in root.iterdir() if path.is_dir()) if root.exists() else []

    def test_repeated_multigeneration_removal_is_no_op_for_data_and_binaries(self):
        for name, game_source, dx8_source in (
            ("canonical", self.game_variants["0000"], self.dx8["stock"]),
            ("estonian", self.estonian_game, self.estonian_dx8),
        ):
            with self.subTest(source=name):
                shutil.rmtree(self.game)
                self.game.mkdir()
                self.write_target("game/sys/game.dll", game_source)
                self.write_target("sys/dx8.dll", dx8_source)
                existing = self.release.mods["config"].files[0].destination
                self.write_target(existing, b"incoming data")
                first_apply = self.service().apply(
                    requested=("config", "camera", "dx8_vsync"), exact=True,
                )
                self.assertEqual(first_apply.status, "installed")
                second_apply = self.service().apply(requested=("physics",))
                self.assertEqual(second_apply.status, "installed")

                removed = self.service().remove(all=True)

                self.assertEqual(removed.status, "removed")
                self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), game_source)
                self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), dx8_source)
                self.assertEqual((self.game / existing).read_bytes(), b"incoming data")
                for destination in list(self.data_bytes["config"])[1:]:
                    self.assertFalse((self.game / destination).exists())
                generation_count = len(self.generations())

                repeated = self.service().remove(all=True)

                self.assertEqual(repeated.status, "no_op")
                self.assertTrue(repeated.no_op)
                self.assertEqual(repeated.operations, ())
                self.assertEqual(len(self.generations()), generation_count)
                self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), game_source)
                self.assertEqual((self.game / "sys/dx8.dll").read_bytes(), dx8_source)


if __name__ == "__main__":
    unittest.main()
