import argparse
from pathlib import Path
import sys

from .errors import PatchError
from .manifest import load_release
from .patch import PatchState
from .runner import load_binary_mods, runner_from_directory


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="htmod", description="Hard Truck mod SDK")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list release components")
    subparsers.add_parser("validate", help="validate release manifests")
    for command in ("check", "apply", "restore"):
        subparser = subparsers.add_parser(command, help=f"{command} registered binary mods")
        subparser.add_argument("--game-dir", type=Path, required=True)
        subparser.add_argument("--mod", action="append", required=True, dest="mod_ids")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        release = load_release(args.root)
        if args.command == "list":
            for mod_id in release.mods:
                print(mod_id)
            return 0
        if args.command == "validate":
            print(f"Release valid: {len(release.mods)} mods, {len(release.game_variants)} game variants")
            return 0
        mods = load_binary_mods(release, args.mod_ids)
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
