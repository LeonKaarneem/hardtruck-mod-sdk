from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re

from .errors import ManifestError


BINARY_HANDLERS = frozenset({"camera", "physics", "ai", "ui_keyboard", "dx8_vsync"})
TRANSFORM_NAMES = frozenset({"strip_r_renderer"})
FORBIDDEN_DIRECTORIES = frozenset({"save", "saves", "music", "log", "logs", "__pycache__"})
FORBIDDEN_EXTENSIONS = frozenset({".sav", ".log", ".dmp", ".mdmp", ".dump", ".pyc", ".pyo"})
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_RESERVED_NAME = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9]|conin\$|conout\$)(?:\..*)?$",
    re.IGNORECASE,
)
WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')


@dataclass(frozen=True)
class DataFile:
    source: str
    destination: str
    transforms: tuple = ()


@dataclass(frozen=True)
class Mod:
    id: str
    version: str
    kind: str
    setup: dict
    requires: tuple
    conflicts: tuple
    new_profile: bool
    files: tuple
    handler: str | None
    target: str | None
    source_sha256: str | None
    directory: Path

    def source_path(self, record):
        return self.directory / "files" / record.source


@dataclass(frozen=True)
class GameVariant:
    file: str
    components: tuple


@dataclass(frozen=True)
class Release:
    root: Path
    mods: dict
    game_variants: dict
    legacy_game_sha256: tuple


class Selection:
    def __init__(self, mod_ids):
        self.mod_ids = tuple(mod_ids)

    def validate(self, mods):
        selected = set(self.mod_ids)
        if len(selected) != len(self.mod_ids):
            raise ManifestError("selection contains duplicate mod IDs")
        for mod_id in self.mod_ids:
            if mod_id not in mods:
                raise ManifestError(f"selection contains unknown mod '{mod_id}'")
        for mod_id in self.mod_ids:
            mod = mods[mod_id]
            for required in mod.requires:
                if required not in selected:
                    raise ManifestError(f"mod '{mod_id}' requires '{required}'")
            for conflict in mod.conflicts:
                if conflict in selected:
                    raise ManifestError(f"mod '{mod_id}' conflicts with '{conflict}'")
        return self


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError(f"{path} must contain a JSON object")
    return value


def require_fields(value, fields, context):
    for field in fields:
        if field not in value:
            raise ManifestError(f"{context} missing required field '{field}'")


def reject_unknown_keys(value, allowed, context):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ManifestError(f"{context} contains unknown key '{sorted(unknown)[0]}'")


def string_list(value, field, context):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{context} field '{field}' must be a list of strings")
    if len(set(value)) != len(value):
        raise ManifestError(f"{context} field '{field}' contains duplicates")
    return tuple(value)


def safe_path(value, field, context):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ManifestError(f"{context} has unsafe {field} path: {value!r}")
    if any(ord(character) < 32 or character in WINDOWS_FORBIDDEN_CHARACTERS for character in value):
        raise ManifestError(f"{context} has nonportable {field} path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ManifestError(f"{context} has unsafe {field} path: {value!r}")
    for part in path.parts:
        if part.endswith((" ", ".")) or WINDOWS_RESERVED_NAME.fullmatch(part):
            raise ManifestError(f"{context} has reserved or nonportable {field} path: {value!r}")
    return path


def load_mod(root, mod_id):
    context = f"mod '{mod_id}'"
    manifest_path = root / "mods" / mod_id / "mod.json"
    value = read_json(manifest_path)
    require_fields(
        value,
        ("schema_version", "id", "version", "kind", "setup", "requires", "conflicts", "new_profile"),
        context,
    )
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ManifestError(f"{context} has unsupported schema_version")
    reject_unknown_keys(
        value,
        (
            "schema_version", "id", "version", "kind", "setup", "requires",
            "conflicts", "new_profile", "files", "handler", "target", "source_sha256",
        ),
        context,
    )
    if value["id"] != mod_id:
        raise ManifestError(f"{context} ID does not match directory/name '{value['id']}'")
    if not ID_PATTERN.fullmatch(mod_id):
        raise ManifestError(f"{context} has invalid stable ID")
    if not isinstance(value["version"], str) or not value["version"]:
        raise ManifestError(f"{context} has invalid version")
    kind = value["kind"]
    if not isinstance(kind, str) or kind not in {"data", "binary"}:
        raise ManifestError(f"{context} has unknown kind '{kind}'")
    setup = value["setup"]
    if not isinstance(setup, dict):
        raise ManifestError(f"{context} setup must be an object")
    require_fields(setup, ("label", "description", "default"), f"{context} setup")
    reject_unknown_keys(setup, ("label", "description", "default"), f"{context} setup")
    if not all(isinstance(setup[field], str) and setup[field] for field in ("label", "description")):
        raise ManifestError(f"{context} setup label/description must be nonempty strings")
    if not isinstance(setup["default"], bool) or not isinstance(value["new_profile"], bool):
        raise ManifestError(f"{context} setup.default and new_profile must be booleans")
    requires = string_list(value["requires"], "requires", context)
    conflicts = string_list(value["conflicts"], "conflicts", context)
    if mod_id in requires or mod_id in conflicts:
        raise ManifestError(f"{context} cannot require or conflict with itself")

    files = []
    handler = target = source_sha256 = None
    if kind == "data":
        require_fields(value, ("files",), context)
        if not isinstance(value["files"], list) or not value["files"]:
            raise ManifestError(f"{context} files must be a nonempty list")
        for index, record in enumerate(value["files"]):
            record_context = f"{context} file {index}"
            if not isinstance(record, dict):
                raise ManifestError(f"{record_context} must be an object")
            require_fields(record, ("source", "destination"), record_context)
            reject_unknown_keys(record, ("source", "destination", "transforms"), record_context)
            source = safe_path(record["source"], "source", record_context)
            destination = safe_path(record["destination"], "destination", record_context)
            if destination.parts[:2] != ("game", "usr") or len(destination.parts) < 3:
                raise ManifestError(f"{record_context} destination must be under game/usr")
            relative = destination.parts[2:]
            if any(part.casefold() in FORBIDDEN_DIRECTORIES for part in relative[:-1]):
                raise ManifestError(f"{record_context} destination is forbidden")
            if PurePosixPath(relative[-1]).suffix.casefold() in FORBIDDEN_EXTENSIONS:
                raise ManifestError(f"{record_context} destination is forbidden")
            transforms = string_list(record.get("transforms", []), "transforms", record_context)
            for transform in transforms:
                if transform not in TRANSFORM_NAMES:
                    raise ManifestError(f"{record_context} has unknown transform '{transform}'")
            files.append(DataFile(source.as_posix(), destination.as_posix(), transforms))
    else:
        require_fields(value, ("handler", "target", "source_sha256"), context)
        handler = value["handler"]
        if not isinstance(handler, str) or handler not in BINARY_HANDLERS:
            raise ManifestError(f"{context} has unknown handler '{handler}'")
        target = safe_path(value["target"], "target", context).as_posix()
        source_sha256 = value["source_sha256"]
        if not isinstance(source_sha256, str) or not SHA256_PATTERN.fullmatch(source_sha256):
            raise ManifestError(f"{context} source_sha256 must be a lowercase SHA-256")

    mod = Mod(
        mod_id,
        value["version"],
        kind,
        setup,
        requires,
        conflicts,
        value["new_profile"],
        tuple(files),
        handler,
        target,
        source_sha256,
        root / "mods" / mod_id,
    )
    try:
        registry_root = (root / "mods").resolve()
        mod_root = mod.directory.resolve()
        files_root = (mod.directory / "files").resolve()
    except (OSError, ValueError) as error:
        raise ManifestError(f"{context} cannot resolve mod/files root: {error}") from error
    if not mod_root.is_relative_to(registry_root) or not files_root.is_relative_to(mod_root):
        raise ManifestError(f"{context} files root escapes resolved mod/registry root")
    for record in mod.files:
        source = mod.source_path(record)
        try:
            if not source.resolve().is_relative_to(files_root) or not source.is_file():
                raise ManifestError(f"{context} source file not found or unsafe: {record.source}")
        except (OSError, ValueError) as error:
            raise ManifestError(f"{context} cannot access source '{record.source}': {error}") from error
    return mod


def load_release(root):
    try:
        root = Path(root).resolve()
        modpack = root if (root / "release.json").is_file() else root / "modpack"
    except (OSError, TypeError, ValueError) as error:
        raise ManifestError(f"invalid release root path: {error}") from error
    value = read_json(modpack / "release.json")
    require_fields(value, ("schema_version", "mods", "game_variants"), "release")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ManifestError("release has unsupported schema_version")
    reject_unknown_keys(value, ("schema_version", "mods", "game_variants", "legacy_game_sha256"), "release")
    legacy_game_sha256 = string_list(value.get("legacy_game_sha256", []), "legacy_game_sha256", "release")
    for digest in legacy_game_sha256:
        if not SHA256_PATTERN.fullmatch(digest):
            raise ManifestError("release field 'legacy_game_sha256' must contain lowercase SHA-256 hashes")
    mod_ids = string_list(value["mods"], "mods", "release")
    for mod_id in mod_ids:
        if not ID_PATTERN.fullmatch(mod_id):
            raise ManifestError(f"release has invalid mod ID '{mod_id}'")
    mods_root = modpack / "mods"
    try:
        available = {path.name for path in mods_root.iterdir() if path.is_dir()}
    except OSError as error:
        raise ManifestError(f"cannot read mod registry {mods_root}: {error}") from error
    unknown = set(mod_ids) - available
    unlisted = available - set(mod_ids)
    if unknown:
        raise ManifestError(f"release references unknown mod '{sorted(unknown)[0]}'")
    if unlisted:
        raise ManifestError(f"mod '{sorted(unlisted)[0]}' is not listed by release")
    mods = {}
    destinations = {}
    for mod_id in mod_ids:
        mod = load_mod(modpack, mod_id)
        for record in mod.files:
            destination_key = record.destination.casefold()
            if destination_key in destinations:
                raise ManifestError(
                    f"destination '{record.destination}' already owned by "
                    f"'{destinations[destination_key]}'"
                )
            destinations[destination_key] = mod_id
        mods[mod_id] = mod
    for mod in mods.values():
        for field, references in (("requires", mod.requires), ("conflicts", mod.conflicts)):
            for reference in references:
                if reference not in mods:
                    raise ManifestError(f"mod '{mod.id}' {field} unknown mod '{reference}'")

    if not isinstance(value["game_variants"], dict):
        raise ManifestError("release game_variants must be an object")
    game_components = tuple(
        mod_id for mod_id, mod in mods.items()
        if mod.kind == "binary" and mod.target == "game/sys/game.dll"
    )
    if not game_components and value["game_variants"]:
        raise ManifestError("release game variants require at least one game.dll component")
    variants = {}
    for name, record in value["game_variants"].items():
        if not isinstance(record, dict):
            raise ManifestError(f"release has invalid game variant '{name}'")
        if not isinstance(name, str) or len(name) != len(game_components) or any(bit not in "01" for bit in name):
            raise ManifestError(
                f"game variant '{name}' has invalid bit length; expected {len(game_components)}"
            )
        require_fields(record, ("file", "components"), f"game variant '{name}'")
        reject_unknown_keys(record, ("file", "components"), f"game variant '{name}'")
        if record["file"] != f"game-{name}.dll":
            raise ManifestError(f"game variant '{name}' has noncanonical file")
        components = string_list(record["components"], "components", f"game variant '{name}'")
        for component in components:
            if component not in mods:
                raise ManifestError(f"game variant '{name}' references unknown component '{component}'")
            if mods[component].kind != "binary" or mods[component].target != "game/sys/game.dll":
                raise ManifestError(f"game variant '{name}' includes non-game component '{component}'")
        expected = tuple(
            component for bit, component in enumerate(game_components)
            if int(name, 2) & (1 << bit)
        )
        if components != expected:
            raise ManifestError(f"game variant '{name}' does not match component bits")
        variants[name] = GameVariant(record["file"], components)
    return Release(modpack, mods, variants, legacy_game_sha256)
