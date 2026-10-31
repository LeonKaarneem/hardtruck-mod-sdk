import hashlib
import importlib.util
from pathlib import Path
import sys

from . import registry
from .errors import PatchError
from .patch import PatchRunner
from .quick_setup import QUICK_SETUP_DESTINATION, render_quick_setup


def verify_pristine_images(release, images):
    for target, data in images.items():
        if target not in release.pristine_sha256:
            raise PatchError(f"target '{target}' has no manifest pristine SHA-256")
        expected = release.pristine_sha256[target]
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise PatchError(
                f"target '{target}' pristine SHA-256 mismatch: expected {expected}, got {actual}"
            )


def materialize_data_bytes(release, selected=None):
    selected = tuple(release.mods) if selected is None else tuple(selected)
    components = {}
    for mod in release.mods.values():
        if mod.kind != "data":
            continue
        files = {}
        for record in mod.files:
            content = mod.source_path(record).read_bytes()
            for transform in record.transforms:
                content = registry.TRANSFORMS[transform](content)
            if record.destination == QUICK_SETUP_DESTINATION:
                content = render_quick_setup(content, selected)
            files[record.destination] = content
        components[mod.id] = files
    return components


def materialize_data(release, payload, selected=None):
    data_root = payload / "data"
    components = {}
    for mod_id, files in materialize_data_bytes(release, selected).items():
        owned = []
        for destination, content in files.items():
            relative = destination.removeprefix("game/usr/")
            target = data_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            owned.append(f"data/{relative}")
        components[mod_id] = owned
    return components


def load_binary_mods(release, mod_ids):
    loaded = []
    for mod_id in mod_ids:
        if mod_id not in release.mods:
            raise PatchError(f"selection contains unknown mod '{mod_id}'")
        manifest = release.mods[mod_id]
        if manifest.kind != "binary":
            raise PatchError(f"mod '{mod_id}' is not a binary mod")
        handler = manifest.handler
        if handler not in registry.BINARY_HANDLERS:
            raise PatchError(f"binary handler '{handler}' is not registered")
        module_name = f"_htmod_registered_{handler}"
        module_path = release.root / "mods" / handler / "patch.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise PatchError(f"cannot load registered binary handler '{handler}'")
        module = importlib.util.module_from_spec(spec)
        dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError, AttributeError) as error:
            raise PatchError(f"cannot load registered binary handler '{handler}': {error}") from error
        finally:
            sys.dont_write_bytecode = dont_write_bytecode
        loaded_mod = module.MOD
        if loaded_mod.id != mod_id:
            raise PatchError(
                f"handler '{handler}' id '{loaded_mod.id}' does not match manifest id '{mod_id}'"
            )
        if loaded_mod.target != manifest.target:
            raise PatchError(
                f"handler '{handler}' target '{loaded_mod.target}' does not match "
                f"manifest target '{manifest.target}'"
            )
        loaded_mod.source_sha256 = manifest.source_sha256
        loaded.append(loaded_mod)
    return loaded


def matches_exact_recognized_state(data, target, target_mods, pristine_sha256):
    if len(target_mods) > 4:
        raise PatchError(f"target '{target}' has too many selected mods for bounded exact-state recognition")
    restored = {target: bytearray(data)}
    try:
        PatchRunner(restored).restore(target_mods)
    except PatchError as error:
        raise PatchError(f"target '{target}' cannot restore to its pinned pristine image: {error}") from error
    pristine = restored[target]
    if hashlib.sha256(pristine).hexdigest() != pristine_sha256:
        return False
    for bits in range(1, 1 << len(target_mods)):
        subset = [mod for index, mod in enumerate(target_mods) if bits & (1 << index)]
        candidate = {target: bytearray(pristine)}
        PatchRunner(candidate).apply(subset)
        if candidate[target] == data:
            return True
    return False


def runner_from_directory(game_dir, mods):
    root = Path(game_dir).resolve()
    if not root.is_dir():
        raise PatchError(f"game directory not found: {root}")
    images = {}
    paths = {}
    for mod in mods:
        if mod.target in images:
            continue
        path = root / mod.target
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise PatchError(f"target '{mod.target}' not found: {error}") from error
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise PatchError(f"target '{mod.target}' is unsafe or not a file")
        paths[mod.target] = resolved
        images[mod.target] = bytearray(resolved.read_bytes())
    runner = PatchRunner(images, paths)
    for target, data in images.items():
        target_mods = [mod for mod in mods if mod.target == target]
        pristine_hashes = {mod.source_sha256 for mod in target_mods}
        if len(pristine_hashes) != 1:
            raise PatchError(f"target '{target}' selected mods disagree on pristine source SHA-256")
        pristine_sha256 = pristine_hashes.pop()
        if hashlib.sha256(data).hexdigest() == pristine_sha256:
            continue
        if not matches_exact_recognized_state(data, target, target_mods, pristine_sha256):
            raise PatchError(
                f"target '{target}' has unrecognized source SHA-256 and does not match any "
                "exact recognized whole-file state "
                "for the selected mods"
            )
    return runner
