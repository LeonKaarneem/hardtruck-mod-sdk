import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod.migration import generate_splices, normalize_to_pristine


TARGET = "game/sys/game.dll"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(catalog):
    return (json.dumps(catalog, indent=2, sort_keys=True) + "\n").encode("utf-8")


class MigrationAddTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pristine_bytes = bytes(range(256)) * 2
        source = bytearray(self.pristine_bytes)
        source[32:36] = b"mod!"
        source.extend(b"tail")
        self.source_bytes = bytes(source)
        self.source = self.root / "source.dll"
        self.pristine = self.root / "pristine.dll"
        self.source.write_bytes(self.source_bytes)
        self.pristine.write_bytes(self.pristine_bytes)
        self.migrations = self.root / "migrations.json"
        self.output = self.root / "output.json"
        self.catalog = {
            "schema_version": 1,
            "targets": {
                TARGET: {
                    "pristine_length": len(self.pristine_bytes),
                    "pristine_sha256": digest(self.pristine_bytes),
                    "sources": {
                        digest(self.pristine_bytes): {
                            "length": len(self.pristine_bytes),
                            "changes": [],
                        }
                    },
                }
            },
        }
        self.migrations.write_bytes(encoded(self.catalog))

    def arguments(self, target=TARGET, source=None, output=None):
        return [
            "migration-add",
            "--target", target,
            "--source", str(source or self.source),
            "--pristine", str(self.pristine),
            "--migrations", str(self.migrations),
            "--output", str(output or self.output),
        ]

    def run_cli(self, arguments):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/htmod.py"), "--root", str(ROOT), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_preserved(self, result, output=b"original output"):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("error:", result.stderr)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.pristine.read_bytes(), self.pristine_bytes)
        if output is None:
            self.assertFalse(self.output.exists())
        else:
            self.assertEqual(self.output.read_bytes(), output)

    def test_add_source_writes_deterministic_length_changes_and_real_cli_json(self):
        migrations_before = self.migrations.read_bytes()
        result = self.run_cli(self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 1,
            "status": "added",
            "target": TARGET,
            "source_sha256": digest(self.source_bytes),
        })
        self.assertEqual(self.migrations.read_bytes(), migrations_before)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.pristine.read_bytes(), self.pristine_bytes)
        expected = dict(self.catalog)
        expected["targets"] = dict(self.catalog["targets"])
        expected["targets"][TARGET] = dict(self.catalog["targets"][TARGET])
        expected["targets"][TARGET]["sources"] = dict(self.catalog["targets"][TARGET]["sources"])
        expected["targets"][TARGET]["sources"][digest(self.source_bytes)] = {
            "length": len(self.source_bytes),
            "changes": generate_splices(self.source_bytes, self.pristine_bytes),
        }
        self.assertEqual(self.output.read_bytes(), encoded(expected))
        written = json.loads(self.output.read_text(encoding="utf-8"))
        record = written["targets"][TARGET]["sources"][digest(self.source_bytes)]
        self.assertEqual(set(record), {"length", "changes"})
        self.assertEqual(
            normalize_to_pristine(self.source_bytes, TARGET, written),
            self.pristine_bytes,
        )
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            ["migrations.json", "output.json", "pristine.dll", "source.dll"],
        )

    def test_identical_record_is_no_op_and_does_not_create_or_replace_output(self):
        self.catalog["targets"][TARGET]["sources"][digest(self.source_bytes)] = {
            "length": len(self.source_bytes),
            "changes": generate_splices(self.source_bytes, self.pristine_bytes),
        }
        self.migrations.write_bytes(encoded(self.catalog))
        before = self.migrations.read_bytes()
        result = self.run_cli(self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "no_op")
        self.assertEqual(self.migrations.read_bytes(), before)
        self.assertFalse(self.output.exists())
        self.output.write_bytes(b"keep")
        result = self.run_cli(self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "no_op")
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_identical_legacy_full_delta_record_remains_no_op(self):
        self.source_bytes = b"X" * len(self.pristine_bytes)
        self.source.write_bytes(self.source_bytes)
        self.catalog["targets"][TARGET]["sources"][digest(self.source_bytes)] = {
            "length": len(self.source_bytes),
            "changes": generate_splices(self.source_bytes, self.pristine_bytes),
        }
        self.migrations.write_bytes(encoded(self.catalog))
        before = self.migrations.read_bytes()
        result = self.run_cli(self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "no_op")
        self.assertEqual(self.migrations.read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_conflicting_duplicate_is_refused_without_changes(self):
        self.catalog["targets"][TARGET]["sources"][digest(self.source_bytes)] = {
            "length": len(self.source_bytes) + 1,
            "changes": generate_splices(self.source_bytes, self.pristine_bytes),
        }
        self.migrations.write_bytes(encoded(self.catalog))
        before = self.migrations.read_bytes()
        self.output.write_bytes(b"keep")
        result = self.run_cli(self.arguments())
        self.assert_preserved(result, b"keep")
        self.assertIn("conflicting duplicate", result.stderr)
        self.assertEqual(self.migrations.read_bytes(), before)

    def test_unknown_or_uncatalogued_target_is_refused(self):
        for target in ("game/sys/other.dll", "../game.dll", "sys/dx8.dll"):
            with self.subTest(target=target):
                self.output.unlink(missing_ok=True)
                result = self.run_cli(self.arguments(target=target))
                self.assert_preserved(result, None)
                self.assertIn("target", result.stderr)

    def test_corrupt_catalog_and_mismatched_pristine_are_refused(self):
        cases = []
        self.migrations.write_bytes(b"not json")
        cases.append("catalog")
        for case in cases:
            with self.subTest(case=case):
                before = self.migrations.read_bytes()
                result = self.run_cli(self.arguments())
                self.assert_preserved(result, None)
                self.assertEqual(self.migrations.read_bytes(), before)
        self.migrations.write_bytes(encoded(self.catalog))
        self.pristine.write_bytes(b"wrong pristine")
        result = self.run_cli(self.arguments())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.pristine.read_bytes(), b"wrong pristine")
        self.assertIn("pristine", result.stderr)

    def test_unsafe_source_catalog_and_output_paths_are_refused(self):
        directory = self.root / "directory.dll"
        directory.mkdir()
        result = self.run_cli(self.arguments(source=directory))
        self.assert_preserved(result, None)

        real_source = self.root / "real.dll"
        real_source.write_bytes(self.source_bytes)
        linked_source = self.root / "linked.dll"
        linked_source.symlink_to(real_source)
        result = self.run_cli(self.arguments(source=linked_source))
        self.assert_preserved(result, None)

        linked_catalog = self.root / "linked.json"
        linked_catalog.symlink_to(self.migrations)
        arguments = self.arguments()
        arguments[arguments.index("--migrations") + 1] = str(linked_catalog)
        result = self.run_cli(arguments)
        self.assert_preserved(result, None)

        self.output.write_bytes(b"keep")
        result = self.run_cli(self.arguments(output=self.source))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_full_binary_delta_is_refused(self):
        self.source_bytes = b"X" * len(self.pristine_bytes)
        self.source.write_bytes(self.source_bytes)
        result = self.run_cli(self.arguments())
        self.assert_preserved(result, None)
        self.assertIn("full", result.stderr.lower())

    def test_existing_output_adds_without_fchmod_and_preserves_mode(self):
        from htmod import cli

        self.output.write_bytes(b"keep")
        self.output.chmod(0o640)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
                cli.os, "fchmod", create=True,
                side_effect=AttributeError("fchmod unavailable")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main(["--root", str(ROOT), *self.arguments()])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["status"], "added")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o640)
        self.assertIn(digest(self.source_bytes), json.loads(
            self.output.read_text(encoding="utf-8"),
        )["targets"][TARGET]["sources"])
        self.assertFalse(list(self.root.glob(".htmod-migration-*")))

    def test_atomic_replace_failure_preserves_output_and_inputs(self):
        from htmod import cli

        self.output.write_bytes(b"keep")
        migrations_before = self.migrations.read_bytes()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(cli.os, "replace", side_effect=OSError("injected replace")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main(["--root", str(ROOT), *self.arguments()])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("injected replace", stderr.getvalue())
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assertEqual(self.migrations.read_bytes(), migrations_before)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.pristine.read_bytes(), self.pristine_bytes)
        self.assertFalse(list(self.root.glob(".htmod-migration-*")))

    def test_oversized_result_preserves_output_and_inputs(self):
        from htmod import cli

        self.output.write_bytes(b"keep")
        migrations_before = self.migrations.read_bytes()
        limit = len(migrations_before) + 16
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("htmod.migration.MAX_CATALOG_SIZE", limit):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main(["--root", str(ROOT), *self.arguments()])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("size budget", stderr.getvalue())
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assertEqual(self.migrations.read_bytes(), migrations_before)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)


if __name__ == "__main__":
    unittest.main()
