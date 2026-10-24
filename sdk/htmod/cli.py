import argparse
from pathlib import Path
import sys

from .errors import PatchError
from .manifest import load_release


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="htmod", description="Hard Truck mod SDK")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list release components")
    subparsers.add_parser("validate", help="validate release manifests")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        release = load_release(args.root)
    except PatchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.command == "list":
        for mod_id in release.mods:
            print(mod_id)
    else:
        print(f"Release valid: {len(release.mods)} mods, {len(release.game_variants)} game variants")
    return 0
