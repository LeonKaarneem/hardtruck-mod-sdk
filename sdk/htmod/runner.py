import hashlib
import importlib.util
from pathlib import Path
import re
import shutil

from .errors import PatchError
from .patch import PatchRunner


REGISTERED_BINARY_HANDLERS = frozenset({"camera", "physics", "ai", "dx8_vsync"})


def strip_r_renderer(data):
    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    sanitized = re.sub(
        r"^[ \t]*uset\s+r_renderer\s+.*(?:\n|$)",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return sanitized.encode("utf-8")


TRANSFORMS = {"strip_r_renderer": strip_r_renderer}


def materialize_data(release, payload):
    data_root = payload / "data"
    components = {}
    for mod in release.mods.values():
        if mod.kind != "data":
            continue
        owned = []
        for record in mod.files:
            relative = record.destination.removeprefix("game/usr/")
            target = data_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = mod.source_path(record)
            if record.transforms:
                content = source.read_bytes()
                for transform in record.transforms:
                    content = TRANSFORMS[transform](content)
                target.write_bytes(content)
            else:
                shutil.copyfile(source, target)
            owned.append(f"data/{relative}")
        components[mod.id] = owned
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
        if handler not in REGISTERED_BINARY_HANDLERS:
            raise PatchError(f"binary handler '{handler}' is not registered")
        module_name = f"_htmod_registered_{handler}"
        module_path = release.root / "mods" / handler / "patch.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise PatchError(f"cannot load registered binary handler '{handler}'")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError, AttributeError) as error:
            raise PatchError(f"cannot load registered binary handler '{handler}': {error}") from error
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
