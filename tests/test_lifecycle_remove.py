from dataclasses import replace
import json
import unittest

from tests.test_lifecycle import sha256
from tests.test_lifecycle_apply import ApplyFixture
from htmod.errors import PatchError
from htmod.lifecycle import Lifecycle


class SelectiveRemovalTests(ApplyFixture):
    def service(self, release=None):
        return Lifecycle(
            self.release if release is None else release,
            self.game,
            self.catalog,
        )

    def generations(self):
        root = self.game / ".htmod/backups"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def journal(self, generation):
        return json.loads((generation / "journal.json").read_text(encoding="utf-8"))

    def install_state(self, game="0011", dx8="stock", data=("config",)):
        self.write_target("game/sys/game.dll", self.game_variants[game])
        self.write_target("sys/dx8.dll", self.dx8[dx8])
        self.write_data(data)

    def test_binary_removal_preserves_retained_components_and_exact_variant(self):
        self.install_state(game="0011", data=())

        result = self.service().remove(requested=("camera",))

        self.assertEqual(result.status, "removed")
        self.assertFalse(result.no_op)
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0010"],
        )
        generation, = self.generations()
        journal = self.journal(generation)
        self.assertEqual(journal["kind"], "remove")
        self.assertEqual(journal["status"], "removed")
        entry = journal["operations"][0]
        self.assertEqual(entry["action"], "replace")
        self.assertEqual(entry["component_ids"], ["physics"])
        self.assertEqual((generation / entry["backup_path"]).read_bytes(), self.game_variants["0011"])
        self.assertEqual(entry["after_sha256"], sha256(self.game_variants["0010"]))

        repeated = self.service().remove(requested=("camera",))
        self.assertEqual(repeated.status, "no_op")
        self.assertTrue(repeated.no_op)
        self.assertEqual(len(self.generations()), 1)

    def test_data_removal_deletes_matching_skips_modified_and_ignores_missing(self):
        self.install_state(game="0000")
        destinations = list(self.data_bytes["config"])
        modified, missing = destinations[:2]
        self.write_target(modified, b"user modified")
        (self.game / missing).unlink()

        result = self.service().remove(requested=("config",))

        self.assertEqual(result.status, "partial")
        self.assertFalse(result.no_op)
        self.assertEqual((self.game / modified).read_bytes(), b"user modified")
        self.assertFalse((self.game / missing).exists())
        quick_setup = "game/usr/ui/cosetup.sc"
        for destination in destinations[2:]:
            if destination == quick_setup:
                self.assertTrue((self.game / destination).is_file())
            else:
                self.assertFalse((self.game / destination).exists(), destination)
        by_path = {operation.destination: operation for operation in result.operations}
        self.assertEqual(by_path[modified].status, "skipped_modified")
        self.assertEqual(by_path[missing].status, "missing")
        self.assertEqual(by_path[quick_setup].status, "skipped_modified")
        self.assertTrue(all(
            by_path[destination].status == "removed"
            for destination in destinations[2:]
            if destination != quick_setup
        ))
        generation, = self.generations()
        journal = self.journal(generation)
        self.assertEqual(journal["status"], "partial")
        deleted = next(
            item for item in journal["operations"] if item["destination"] == destinations[2]
        )
        self.assertEqual(
            (generation / deleted["backup_path"]).read_bytes(),
            self.data_bytes["config"][destinations[2]],
        )
        self.assertIsNone(deleted["after_sha256"])

    def test_only_modified_or_missing_data_is_partial_without_backup_generation(self):
        self.install_state(game="0000", data=())
        destinations = list(self.data_bytes["config"])
        self.write_target(destinations[0], b"user modified")

        result = self.service().remove(requested=("config",))

        self.assertEqual(result.status, "partial")
        self.assertTrue(result.no_op)
        self.assertEqual(self.generations(), [])
        self.assertEqual((self.game / destinations[0]).read_bytes(), b"user modified")
        self.assertEqual(
            {operation.status for operation in result.operations},
            {"skipped_modified", "missing"},
        )

    def test_dependent_removal_rejects_unless_cascade_includes_dependents(self):
        self.install_state(game="1001")
        before = self.tree_hashes()
        with self.assertRaisesRegex(PatchError, "requires|dependent|cascade"):
            self.service().remove(requested=("config",))
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

        result = self.service().remove(requested=("config",), cascade_dependents=True)

        self.assertEqual(result.status, "removed")
        self.assertEqual(
            (self.game / "game/sys/game.dll").read_bytes(), self.game_variants["0001"],
        )
        for destination in self.data_bytes["config"]:
            self.assertFalse((self.game / destination).exists())

    def test_uncurated_final_selection_fails_before_writes(self):
        release = replace(
            self.release,
            game_variants={
                name: self.release.game_variants[name]
                for name in ("0000", "0011")
            },
        )
        self.install_state(game="0011", data=())
        before = self.tree_hashes()
        with self.assertRaisesRegex(PatchError, "curated|variant"):
            self.service(release=release).remove(requested=("camera",))
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

    def test_unknown_membership_blocks_binary_but_not_safe_data_removal(self):
        self.install_state(game="0000")
        self.write_target("game/sys/game.dll", self.legacy)
        before = self.tree_hashes()
        with self.assertRaisesRegex(PatchError, "membership|legacy|unknown"):
            self.service().remove(requested=("camera",))
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

        result = self.service().remove(requested=("config",))
        self.assertEqual(result.status, "removed")
        self.assertEqual((self.game / "game/sys/game.dll").read_bytes(), self.legacy)
        for destination in self.data_bytes["config"]:
            self.assertFalse((self.game / destination).exists())

    def test_selective_dry_run_is_declarative_and_has_no_writes(self):
        self.install_state(game="0011")
        before = self.tree_hashes()

        plan = self.service().remove(requested=("camera", "config"), dry_run=True)

        value = plan.to_dict()
        self.assertEqual(value["mode"], "selective")
        self.assertEqual(value["requested"], ["config", "camera"])
        self.assertFalse(value["no_op"])
        self.assertEqual(value["binary_changes"][0]["variant"], "0010")
        self.assertTrue(all(change["action"] == "delete" for change in value["data_changes"]))
        self.assertNotIn(str(self.game), json.dumps(value))
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])

    def test_invalid_removal_requests_fail_before_writes(self):
        self.install_state()
        before = self.tree_hashes()
        for requested in (("missing",), ("camera", "camera")):
            with self.subTest(requested=requested), self.assertRaisesRegex(PatchError, "unknown|duplicate"):
                self.service().remove(requested=requested)
        with self.assertRaisesRegex(PatchError, "mod|select"):
            self.service().remove()
        self.assertEqual(self.tree_hashes(), before)
        self.assertEqual(self.generations(), [])


if __name__ == "__main__":
    unittest.main()
