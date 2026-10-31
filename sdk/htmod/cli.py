import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from .errors import PatchError
from .lifecycle import Lifecycle, build_plan, inspect_status
from .manifest import Selection, load_release
from .migration import add_source, encode_catalog, load_catalog, normalize_to_pristine
from .patch import PatchRunner, PatchState
from .quick_setup import QUICK_SETUP_DESTINATION
from .runner import materialize_data_bytes, load_binary_mods, runner_from_directory, verify_pristine_images


STAGED_TARGETS = frozenset({"game/sys/game.dll", "sys/dx8.dll"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _regular_input(path, label):
    path = Path(path)
    if path.is_symlink():
        raise PatchError(f"{label} is an unsafe symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PatchError(f"cannot access {label} {path}: {error}") from error
    if not resolved.is_file():
        raise PatchError(f"{label} is not a safe regular file: {path}")
    return resolved


def _catalog_output(path):
    path = Path(path).absolute()
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise PatchError(f"migration output is unsafe or not a regular file: {path}")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise PatchError(f"cannot access migration output parent: {error}") from error
    if not parent.is_dir():
        raise PatchError(f"migration output parent is not a directory: {path.parent}")
    return parent / path.name


def _atomic_catalog(path, data):
    descriptor, name = tempfile.mkstemp(prefix=".htmod-migration-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            os.chmod(name, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migration_add(target, source, pristine, migrations, output):
    source_path = _regular_input(source, "migration source")
    pristine_path = _regular_input(pristine, "pristine source")
    migrations_path = _regular_input(migrations, "migration catalog")
    output_path = _catalog_output(output)
    if output_path in {source_path, pristine_path}:
        raise PatchError("migration output must not overwrite a source DLL")
    catalog = load_catalog(migrations_path)
    updated, status, source_hash = add_source(
        catalog,
        target,
        source_path.read_bytes(),
        pristine_path.read_bytes(),
    )
    if status == "added":
        _atomic_catalog(output_path, encode_catalog(updated))
    return {
        "schema_version": 1,
        "status": status,
        "target": target,
        "source_sha256": source_hash,
    }


def default_root():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[2]


def parse_expectations(values):
    if isinstance(values, dict):
        values = [f"{target}={expected}" for target, expected in values.items()]
    expectations = {}
    for value in values:
        if not isinstance(value, str) or value.count("=") != 1:
            raise PatchError(f"invalid expectation {value!r}; expected target=sha256")
        target, expected = value.split("=")
        if target not in STAGED_TARGETS:
            raise PatchError(f"unsafe or noncanonical staged target '{target}'")
        if not SHA256_PATTERN.fullmatch(expected):
            raise PatchError(f"target '{target}' expected hash must be lowercase SHA-256")
        if target in expectations:
            raise PatchError(f"duplicate expectation for target '{target}'")
        expectations[target] = expected
    if not expectations:
        raise PatchError("at least one staged target expectation is required")
    return expectations


def _input_file(game_root, target):
    path = game_root / target
    if path.is_symlink():
        raise PatchError(f"target '{target}' is an unsafe symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PatchError(f"target '{target}' not found: {error}") from error
    if not resolved.is_relative_to(game_root) or not resolved.is_file():
        raise PatchError(f"target '{target}' is unsafe or not a file")
    return resolved


def _output_file(output_root, target):
    path = output_root / target
    current = output_root
    if current.is_symlink():
        raise PatchError(f"output root is an unsafe symlink: {output_root}")
    for part in Path(target).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise PatchError(f"output parent is an unsafe symlink: {current}")
        current.mkdir(exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise PatchError(f"staged output is unsafe or not a file: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(output_root):
        raise PatchError(f"staged output escapes output root: {path}")
    return path


def _atomic_outputs(output_root, images):
    paths = {target: _output_file(output_root, target) for target in images}
    temporary = {}
    backups = {}
    replaced = []
    succeeded = recovered = False
    try:
        for target, path in paths.items():
            descriptor, name = tempfile.mkstemp(prefix=".htmod-stage-", dir=path.parent)
            temporary[target] = Path(name)
            with os.fdopen(descriptor, "wb") as output:
                output.write(images[target])
                output.flush()
                os.fsync(output.fileno())
            if path.exists():
                descriptor, name = tempfile.mkstemp(prefix=".htmod-backup-", dir=path.parent)
                backups[target] = Path(name)
                with os.fdopen(descriptor, "wb") as backup:
                    backup.write(path.read_bytes())
                    backup.flush()
                    os.fsync(backup.fileno())
        for target, path in paths.items():
            os.replace(temporary[target], path)
            replaced.append(target)
        for target, path in paths.items():
            if path.read_bytes() != images[target]:
                raise PatchError(f"post-write verification failed for '{target}'")
        succeeded = True
    except BaseException:
        rollback_errors = []
        for target in reversed(replaced):
            path = paths[target]
            try:
                if target in backups:
                    os.replace(backups[target], path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as error:
                rollback_errors.append(f"{target}: {error}")
        recovered = not rollback_errors
        if rollback_errors:
            raise PatchError(f"atomic staged output rollback failed: {rollback_errors}")
        raise
    finally:
        if succeeded or recovered:
            for path in (*temporary.values(), *backups.values()):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def stage(release, game_dir, output_dir, migrations, mod_ids, expectations):
    expectations = parse_expectations(expectations)
    Selection(mod_ids).validate(release.mods)
    binary_ids = [
        mod_id for mod_id, mod in release.mods.items()
        if mod_id in mod_ids and mod.kind == "binary"
    ]
    mods = load_binary_mods(release, binary_ids)
    for mod in mods:
        if mod.target not in expectations:
            raise PatchError(f"selected mod '{mod.id}' target '{mod.target}' has no expectation")
    try:
        game_root = Path(game_dir).resolve(strict=True)
    except OSError as error:
        raise PatchError(f"game directory not found: {game_dir}: {error}") from error
    if not game_root.is_dir():
        raise PatchError(f"game directory not found: {game_root}")
    output_root = Path(output_dir).absolute()
    resolved_output = output_root.resolve()
    if resolved_output == game_root or resolved_output.is_relative_to(game_root):
        raise PatchError("output directory must be outside the game directory")
    if output_root.exists() and (output_root.is_symlink() or not output_root.is_dir()):
        raise PatchError(f"output directory is unsafe or not a directory: {output_root}")
    catalog = load_catalog(migrations)
    input_paths = {target: _input_file(game_root, target) for target in expectations}
    images = {
        target: bytearray(normalize_to_pristine(path.read_bytes(), target, catalog))
        for target, path in input_paths.items()
    }
    verify_pristine_images(release, images)
    by_target = {
        target: [mod for mod in mods if mod.target == target]
        for target in images
    }
    runner = PatchRunner(images)
    for target, target_mods in by_target.items():
        if target_mods:
            runner.apply(target_mods)
    for target, expected in expectations.items():
        actual = hashlib.sha256(images[target]).hexdigest()
        if actual != expected:
            raise PatchError(
                f"target '{target}' final SHA-256 mismatch: expected {expected}, got {actual}"
            )
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink():
        raise PatchError(f"output directory is an unsafe symlink: {output_root}")
    _atomic_outputs(output_root, images)
    return {target: bytes(data) for target, data in images.items()}


def render_quick_setup_output(release, output_dir, mod_ids):
    Selection(mod_ids).validate(release.mods)
    selected = tuple(mod_id for mod_id in release.mods if mod_id in mod_ids)
    content = materialize_data_bytes(release, selected)["config"][QUICK_SETUP_DESTINATION]
    output_root = Path(output_dir).absolute()
    if output_root.exists() and (output_root.is_symlink() or not output_root.is_dir()):
        raise PatchError(f"output directory is unsafe or not a directory: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink():
        raise PatchError(f"output directory is an unsafe symlink: {output_root}")
    images = {QUICK_SETUP_DESTINATION: content}
    _atomic_outputs(output_root, images)
    return images


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="htmod", description="Hard Truck mod SDK")
    parser.add_argument("--root", type=Path, default=default_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list release components")
    subparsers.add_parser("validate", help="validate release manifests")
    subparser = subparsers.add_parser("migration-add", help="add one DLL source to a migration catalog")
    subparser.add_argument("--target", required=True)
    subparser.add_argument("--source", type=Path, required=True)
    subparser.add_argument("--pristine", type=Path, required=True)
    subparser.add_argument("--migrations", type=Path, required=True)
    subparser.add_argument("--output", type=Path, required=True)
    subparser = subparsers.add_parser("status", help="inspect release status as JSON")
    subparser.add_argument("--game-dir", type=Path, required=True)
    subparser.add_argument("--migrations", type=Path, required=True)
    subparser = subparsers.add_parser("plan", help="plan selected changes as JSON")
    subparser.add_argument("--game-dir", type=Path, required=True)
    subparser.add_argument("--migrations", type=Path, required=True)
    subparser.add_argument("--mod", action="append", default=[], dest="mod_ids")
    subparser.add_argument("--exact", action="store_true")
    for command in ("check", "restore"):
        subparser = subparsers.add_parser(command, help=f"{command} registered binary mods")
        subparser.add_argument("--game-dir", type=Path, required=True)
        subparser.add_argument("--mod", action="append", required=True, dest="mod_ids")
    subparser = subparsers.add_parser("apply", help="apply lifecycle or registered binary mods")
    subparser.add_argument("--game-dir", type=Path, required=True)
    subparser.add_argument("--migrations", type=Path)
    subparser.add_argument("--mod", action="append", default=[], dest="mod_ids")
    subparser.add_argument("--exact", action="store_true")
    subparser.add_argument("--all", action="store_true")
    subparser = subparsers.add_parser("remove", help="remove selected components or backup history")
    subparser.add_argument("--game-dir", type=Path, required=True)
    subparser.add_argument("--migrations", type=Path, required=True)
    subparser.add_argument("--mod", action="append", default=[], dest="mod_ids")
    subparser.add_argument("--cascade", action="store_true")
    subparser.add_argument("--all", action="store_true")
    subparser.add_argument("--dry-run", action="store_true")
    subparser = subparsers.add_parser("recover", help="recover an interrupted lifecycle transaction")
    subparser.add_argument("--game-dir", type=Path, required=True)
    subparser.add_argument("--transaction-id")
    subparser = subparsers.add_parser("render-quick-setup", help="render selected Quick Setup text")
    subparser.add_argument("--output-dir", type=Path, required=True)
    subparser.add_argument("--mod", action="append", default=[], dest="mod_ids")
    subparser = subparsers.add_parser("stage", help="normalize and stage selected binary mods")
    subparser.add_argument("--game-dir", type=Path, required=True)
    subparser.add_argument("--output-dir", type=Path, required=True)
    subparser.add_argument("--migrations", type=Path, required=True)
    subparser.add_argument("--mod", action="append", default=[], dest="mod_ids")
    subparser.add_argument("--expect", action="append", required=True, dest="expectations")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        release = load_release(args.root)
        if args.command == "migration-add":
            result = migration_add(
                args.target, args.source, args.pristine, args.migrations, args.output,
            )
            print(json.dumps(result, separators=(",", ":")))
            return 0
        if args.command == "list":
            for mod_id in release.mods:
                print(mod_id)
            return 0
        if args.command == "validate":
            print(f"Release valid: {len(release.mods)} mods, {len(release.game_variants)} game variants")
            return 0
        if args.command == "status":
            catalog = load_catalog(args.migrations)
            report = inspect_status(release, args.game_dir, catalog)
            print(json.dumps(report.to_dict(), separators=(",", ":")))
            return 0
        if args.command == "plan":
            catalog = load_catalog(args.migrations)
            plan = build_plan(
                release, args.game_dir, catalog, args.mod_ids, exact=args.exact,
            )
            print(json.dumps(plan.to_dict(), separators=(",", ":")))
            return 0
        if args.command == "remove":
            result = Lifecycle(release, args.game_dir, args.migrations).remove(
                requested=args.mod_ids,
                cascade_dependents=args.cascade,
                all=args.all,
                dry_run=args.dry_run,
            )
            print(json.dumps(result.to_dict(), separators=(",", ":")))
            if args.dry_run or result.status in {"removed", "no_op"}:
                return 0
            return 1
        if args.command == "recover":
            result = Lifecycle(release, args.game_dir, None).recover(args.transaction_id)
            print(json.dumps(result.to_dict(), separators=(",", ":")))
            return 0 if result.status in {"recovered", "no_op"} else 1
        if args.command == "apply" and args.migrations is not None:
            if args.all and args.mod_ids:
                raise PatchError("--all cannot be combined with --mod")
            requested = tuple(release.mods) if args.all else args.mod_ids
            result = Lifecycle(release, args.game_dir, args.migrations).apply(
                requested=requested, exact=args.exact,
            )
            print(json.dumps(result.to_dict(), separators=(",", ":")))
            return 0 if result.status in {"installed", "no_op"} else 1
        if args.command == "apply" and not args.mod_ids:
            raise PatchError("--mod is required without --migrations")
        if args.command == "apply" and (args.exact or args.all):
            raise PatchError("lifecycle apply options require --migrations")
        if args.command == "render-quick-setup":
            rendered = render_quick_setup_output(release, args.output_dir, args.mod_ids)
            for target, data in rendered.items():
                print(f"{target}: {hashlib.sha256(data).hexdigest()}")
            return 0
        if args.command == "stage":
            staged = stage(
                release, args.game_dir, args.output_dir, args.migrations,
                args.mod_ids, args.expectations,
            )
            for target, data in staged.items():
                print(f"{target}: {hashlib.sha256(data).hexdigest()}")
            return 0
        Selection(args.mod_ids).validate(release.mods)
        binary_ids = [mod_id for mod_id in args.mod_ids if release.mods[mod_id].kind == "binary"]
        if not binary_ids:
            raise PatchError("no binary mods selected")
        mods = load_binary_mods(release, binary_ids)
        runner = runner_from_directory(args.game_dir, mods)
        states = runner.check(mods)
        if args.command == "check":
            for mod_id, state in states.items():
                print(f"{mod_id}: {state.value}")
            return 1 if any(state == PatchState.FOREIGN for state in states.values()) else 0
        pending_state = PatchState.ORIGINAL if args.command == "apply" else PatchState.PATCHED
        pending = [mod for mod in mods if states[mod.id] == pending_state]
        if pending:
            getattr(runner, args.command)(pending)
            final_states = runner.check(mods)
            expected_final_state = PatchState.PATCHED if args.command == "apply" else PatchState.ORIGINAL
            invalid = {
                mod_id: state for mod_id, state in final_states.items()
                if state != expected_final_state
            }
            if invalid:
                raise PatchError(
                    f"selected mods failed final {expected_final_state.value} verification; "
                    f"refusing commit: {invalid}"
                )
            runner.commit()
        for mod_id, state in runner.check(mods).items():
            print(f"{mod_id}: {state.value}")
        return 0
    except (PatchError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
