from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import tempfile

from .errors import PatchError, SignatureError, StateError, TransactionError
from .pe import PEImage


class PatchState(str, Enum):
    ORIGINAL = "ORIGINAL"
    PATCHED = "PATCHED"
    FOREIGN = "FOREIGN"


@dataclass(frozen=True)
class Signature:
    pattern: bytes
    mask: bytes | None = None

    def __post_init__(self):
        if not self.pattern:
            raise SignatureError("signature pattern must not be empty")
        if self.mask is not None and len(self.mask) != len(self.pattern):
            raise SignatureError("signature mask length must match pattern")


def find_unique(data, signature, start=0, end=None):
    end = len(data) if end is None else end
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end > len(data) or start > end:
        raise SignatureError(f"invalid signature bounds {start}..{end} for {len(data)} bytes")
    if len(signature.pattern) > end - start:
        raise SignatureError("no matches: signature exceeds bounded search range")
    mask = signature.mask
    hits = []
    if mask is None or all(mask):
        position = data.find(signature.pattern, start, end)
        while position >= 0:
            hits.append(position)
            position = data.find(signature.pattern, position + 1, end)
    else:
        expression = bytearray()
        for byte, exact in zip(signature.pattern, mask):
            expression.extend(re.escape(bytes((byte,))) if exact else b".")
        for match in re.finditer(b"(?=" + bytes(expression) + b")", bytes(data[start:end]), re.DOTALL):
            hits.append(start + match.start())
    if len(hits) != 1:
        raise SignatureError(
            f"signature has {len(hits)} matches in {start:#x}..{end:#x}; expected exactly 1"
            if hits else "signature has no matches in bounded search range"
        )
    return hits[0]


@dataclass(frozen=True)
class Anchor:
    offset: int
    expected: bytes


@dataclass(frozen=True)
class BytePatch:
    name: str
    offset: int
    original: bytes
    patched: bytes
    anchors: tuple = ()

    def state(self, data):
        ranges = ((self.offset, len(self.original)),) + tuple(
            (anchor.offset, len(anchor.expected)) for anchor in self.anchors
        )
        if any(offset < 0 or offset + size > len(data) for offset, size in ranges):
            return PatchState.FOREIGN
        if any(data[anchor.offset:anchor.offset + len(anchor.expected)] != anchor.expected for anchor in self.anchors):
            return PatchState.FOREIGN
        current = bytes(data[self.offset:self.offset + len(self.original)])
        if current == self.original:
            return PatchState.ORIGINAL
        if current == self.patched:
            return PatchState.PATCHED
        return PatchState.FOREIGN


class BinaryMod:
    def __init__(self, mod_id, target, patches, allow_original_restore=True):
        self.id = mod_id
        self.target = target
        self.patches = tuple(patches)
        self.allow_original_restore = allow_original_restore

    def patches_for(self, image):
        return self.patches

    def inspect(self, image):
        try:
            patches = self.patches_for(image)
            if not patches:
                return PatchState.FOREIGN
            states = [patch.state(image.data) for patch in patches]
        except PatchError:
            return PatchState.FOREIGN
        if all(state == PatchState.ORIGINAL for state in states):
            return PatchState.ORIGINAL
        if all(state == PatchState.PATCHED for state in states):
            return PatchState.PATCHED
        return PatchState.FOREIGN

    def inspect_bytes(self, data):
        return self.inspect(PEImage.parse(data))

    def apply(self, image):
        self._change(image, PatchState.ORIGINAL, PatchState.PATCHED, "patched")

    def restore(self, image):
        state = self.inspect(image)
        if state == PatchState.ORIGINAL and self.allow_original_restore:
            return
        self._change(image, PatchState.PATCHED, PatchState.ORIGINAL, "original")

    def _change(self, image, source_state, destination_state, field):
        state = self.inspect(image)
        if state != source_state:
            raise StateError(f"mod '{self.id}' is {state.value}, expected {source_state.value}; refusing")
        working = bytearray(image.data)
        candidate = PEImage.parse(working)
        for patch in self.patches_for(candidate):
            replacement = getattr(patch, field)
            candidate.data[patch.offset:patch.offset + len(replacement)] = replacement
        if self.inspect(candidate) != destination_state:
            raise StateError(f"mod '{self.id}' failed post-{destination_state.value.lower()} verification")
        image.data[:] = working

    def apply_bytes(self, data):
        if not isinstance(data, bytearray):
            raise PatchError("binary apply requires a mutable bytearray")
        image = PEImage.parse(data)
        self.apply(image)

    def restore_bytes(self, data):
        if not isinstance(data, bytearray):
            raise PatchError("binary restore requires a mutable bytearray")
        image = PEImage.parse(data)
        self.restore(image)


class PatchRunner:
    def __init__(self, images, paths=None):
        self.images = images
        self.paths = paths
        for target, data in images.items():
            if not isinstance(data, bytearray):
                raise TransactionError(f"target '{target}' must be a mutable bytearray")
        if paths is not None and set(paths) != set(images):
            raise TransactionError("transaction paths and images must have identical targets")

    def check(self, mods):
        self._validate_mods(mods)
        return {mod.id: mod.inspect_bytes(self.images[mod.target]) for mod in mods}

    def apply(self, mods):
        self._run(mods, "apply")

    def restore(self, mods):
        self._run(tuple(reversed(mods)), "restore")

    def _run(self, mods, action):
        self._validate_mods(mods)
        working = {target: bytearray(data) for target, data in self.images.items()}
        for mod in mods:
            image = PEImage.parse(working[mod.target])
            getattr(mod, action)(image)
        states = self._check_images(mods, working)
        expected = PatchState.PATCHED if action == "apply" else PatchState.ORIGINAL
        invalid = {mod_id: state for mod_id, state in states.items() if state != expected}
        if invalid:
            raise StateError(f"transaction verification failed: {invalid}")
        for target, data in working.items():
            self.images[target][:] = data

    def _check_images(self, mods, images):
        return {mod.id: mod.inspect_bytes(images[mod.target]) for mod in mods}

    def _validate_mods(self, mods):
        ids = [mod.id for mod in mods]
        if len(set(ids)) != len(ids):
            raise TransactionError("transaction contains duplicate mod IDs")
        for mod in mods:
            if mod.target not in self.images:
                raise TransactionError(f"mod '{mod.id}' target '{mod.target}' is not loaded")

    def commit(self):
        if self.paths is None:
            raise TransactionError("in-memory transaction has no output paths")
        temporary = {}
        backups = {}
        replaced = []
        preserved_backups = set()
        try:
            for target, path in self.paths.items():
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                original = path.read_bytes()
                temporary[target] = self._temporary_file(path, self.images[target], ".htmod-new")
                backups[target] = self._temporary_file(path, original, ".htmod-backup")
            for target, path in self.paths.items():
                os.replace(temporary[target], path)
                replaced.append(target)
        except (OSError, PatchError) as error:
            rollback_errors = []
            for target in reversed(replaced):
                try:
                    os.replace(backups[target], self.paths[target])
                except OSError as rollback_error:
                    preserved_backups.add(backups[target])
                    rollback_errors.append(
                        f"{target}: {rollback_error}; pristine backup preserved at {backups[target]}"
                    )
            detail = f"; rollback failures: {rollback_errors}" if rollback_errors else ""
            raise TransactionError(f"atomic target commit failed: {error}{detail}") from error
        finally:
            for path in (*temporary.values(), *backups.values()):
                if path in preserved_backups:
                    continue
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _temporary_file(path, data, suffix):
        descriptor, name = tempfile.mkstemp(prefix=".htmod-", suffix=suffix, dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(name, path.stat().st_mode)
        except Exception:
            Path(name).unlink(missing_ok=True)
            raise
        return name


def legacy_main(mod, argv=None, custom_rva=False):
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=f"Patch {mod.target} with registered mod '{mod.id}'")
    parser.add_argument("dll", type=Path)
    if custom_rva:
        parser.add_argument("--rva", type=lambda value: int(value, 16), default=mod.rva)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--restore", action="store_true")
    args = parser.parse_args(argv)
    if custom_rva:
        mod = type(mod)(args.rva)
    try:
        data = bytearray(args.dll.read_bytes())
        state = mod.inspect_bytes(data)
        if args.check:
            print(f"file: {args.dll}")
            print(f"status: {'UNPATCHED' if state == PatchState.ORIGINAL else state.value}")
            return 1 if state == PatchState.FOREIGN else 0
        runner = PatchRunner({mod.target: data}, {mod.target: args.dll})
        if args.restore:
            if state == PatchState.ORIGINAL and mod.allow_original_restore:
                print("already in original state")
                return 0
            runner.restore([mod])
            print("restored original bytes")
        else:
            if state == PatchState.PATCHED:
                raise StateError("already patched; use --restore first")
            runner.apply([mod])
            print(f"patched {mod.target}")
        runner.commit()
        return 0
    except (PatchError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
