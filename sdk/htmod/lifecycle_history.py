from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .errors import PatchError


SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
ACTIVE_JSON_STATUSES = frozenset({"Installed", "UninstallPartial", "RollbackFailed"})
ACTIVE_JOURNAL_STATUSES = frozenset({
    "prepared", "in_progress", "installed", "partial", "rollback_failed",
})


@dataclass(frozen=True)
class HistoryEntry:
    generation: Path
    history_id: str
    destination: str
    installed_sha256: str
    original_sha256: str | None
    original_existed: bool
    backup_path: str | None
    component_ids: tuple = ()


def _relative_path(value, label):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise PatchError(f"invalid {label} path in backup evidence")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise PatchError(f"{label} path violates backup containment")
    return path.as_posix()


def _hash(value, label, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise PatchError(f"invalid {label} SHA-256 in backup evidence")
    return value.lower()


def _read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise PatchError(f"duplicate key '{key}' in backup evidence")
            value[key] = item
        return value

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PatchError(f"cannot read backup evidence '{path.name}': {error}") from error


def creation_sequence(value):
    created_ns = value.get("created_ns")
    if created_ns is not None:
        if not isinstance(created_ns, int) or isinstance(created_ns, bool) or created_ns < 0:
            raise PatchError("invalid created_ns in backup evidence")
        return created_ns
    created = value.get("created")
    if created is None:
        return 0
    if not isinstance(created, str):
        raise PatchError("invalid creation timestamp in backup evidence")
    try:
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return int(timestamp.timestamp() * 1_000_000_000)
    except ValueError as error:
        raise PatchError(f"invalid creation timestamp in backup evidence: {created!r}") from error


def _shell_creation_sequence(name):
    match = re.fullmatch(r"shell-(\d{8})-(\d{6})-(\d+)", name)
    if match is None:
        return 0
    try:
        timestamp = datetime.strptime("".join(match.groups()[:2]), "%Y%m%d%H%M%S")
        return int(timestamp.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    except ValueError as error:
        raise PatchError(f"invalid shell generation timestamp: {name!r}") from error


def _read_backup(generation, backup_path, original_sha256):
    relative = _relative_path(backup_path, "backup")
    path = generation / relative
    try:
        resolved_generation = generation.resolve(strict=True)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(resolved_generation):
            raise PatchError("backup path violates generation containment")
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PatchError("backup evidence is not a safe regular file")
        data = path.read_bytes()
    except FileNotFoundError as error:
        raise PatchError(f"backup evidence is missing: '{relative}'") from error
    except OSError as error:
        raise PatchError(f"cannot read backup evidence '{relative}': {error}") from error
    if hashlib.sha256(data).hexdigest() != original_sha256:
        raise PatchError(f"backup evidence SHA-256 mismatch: '{relative}'")
    return data


def _entry(generation, history_id, destination, installed, original, existed, backup, components=()):
    destination = _relative_path(destination, "destination")
    installed = _hash(installed, "installed")
    if not isinstance(existed, bool):
        raise PatchError("invalid existed flag in backup evidence")
    original = _hash(original, "original", optional=True)
    if existed:
        if original is None or backup is None:
            raise PatchError("replaced-file backup evidence is incomplete")
        _read_backup(generation, backup, original)
        backup = _relative_path(backup, "backup")
    elif original is not None or backup not in {None, "-"}:
        raise PatchError("created-file backup evidence has unexpected original content")
    return HistoryEntry(
        generation,
        history_id,
        destination,
        installed,
        original,
        existed,
        backup,
        tuple(components),
    )


def _json_entries(generation, allowed_destinations, game_root):
    value = _read_json(generation / "installation.json")
    if not isinstance(value, dict) or value.get("schema_version") != 1 or isinstance(
        value.get("schema_version"), bool
    ):
        raise PatchError("unsupported schema-1 installation evidence")
    if value.get("status") not in ACTIVE_JSON_STATUSES:
        return ()
    recorded_game = value.get("game_path")
    if not isinstance(recorded_game, str) or not recorded_game:
        return ()
    try:
        if Path(recorded_game).resolve() != Path(game_root).resolve():
            return ()
    except OSError:
        return ()
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise PatchError("schema-1 installation entries must be a list")
    skipped = value.get("skipped", [])
    if not isinstance(skipped, list) or not all(isinstance(item, str) for item in skipped):
        raise PatchError("schema-1 skipped destinations must be a list of paths")
    skipped = {_relative_path(item, "skipped destination") for item in skipped}
    result = []
    destinations = set()
    for record in entries:
        if not isinstance(record, dict):
            raise PatchError("schema-1 installation entry must be an object")
        destination = _relative_path(record.get("destination"), "destination")
        if destination in destinations:
            raise PatchError(f"duplicate destination '{destination}' in backup evidence")
        destinations.add(destination)
        if value["status"] == "UninstallPartial" and destination not in skipped:
            continue
        entry = _entry(
            generation,
            generation.name,
            destination,
            record.get("installed_sha256"),
            record.get("original_sha256"),
            record.get("existed"),
            record.get("backup_file"),
        )
        if entry.destination not in allowed_destinations:
            raise PatchError(f"destination '{entry.destination}' is not allowed by this release")
        result.append(entry)
    return tuple(result)


def _journal_entries(generation, allowed_destinations):
    value = _read_json(generation / "journal.json")
    if not isinstance(value, dict) or value.get("schema_version") != 1 or isinstance(
        value.get("schema_version"), bool
    ):
        raise PatchError("unsupported lifecycle journal evidence")
    if value.get("transaction_id") != generation.name:
        raise PatchError("lifecycle journal transaction ID does not match its generation")
    if value.get("kind", "apply") != "apply" or value.get("status") not in ACTIVE_JOURNAL_STATUSES:
        return ()
    operations = value.get("operations")
    if not isinstance(operations, list):
        raise PatchError("lifecycle journal operations must be a list")
    result = []
    destinations = set()
    for record in operations:
        if not isinstance(record, dict):
            raise PatchError("lifecycle journal operation must be an object")
        if record.get("status") in {"rolled_back", "recovered"}:
            continue
        entry = _entry(
            generation,
            generation.name,
            record.get("destination"),
            record.get("after_sha256"),
            record.get("before_sha256"),
            record.get("existed"),
            record.get("backup_path"),
            record.get("component_ids", ()),
        )
        if entry.destination not in allowed_destinations:
            raise PatchError(f"destination '{entry.destination}' is not allowed by this release")
        if entry.destination in destinations:
            raise PatchError(f"duplicate destination '{entry.destination}' in lifecycle journal")
        destinations.add(entry.destination)
        result.append(entry)
    return tuple(result)


def _tsv_entries(generation, allowed_destinations):
    path = generation / "installation.tsv"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PatchError(f"cannot read shell installation evidence: {error}") from error
    result = []
    destinations = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        columns = line.split("\t")
        if len(columns) != 5:
            raise PatchError(f"shell installation evidence line {line_number} must have five columns")
        action, destination, original, installed, backup = columns
        if action not in {"created", "replaced"}:
            raise PatchError(f"shell installation evidence line {line_number} has invalid action")
        entry = _entry(
            generation,
            generation.name,
            destination,
            installed,
            None if original == "-" else original,
            action == "replaced",
            None if backup == "-" else backup,
        )
        if entry.destination not in allowed_destinations:
            raise PatchError(f"destination '{entry.destination}' is not allowed by this release")
        if entry.destination in destinations:
            raise PatchError(f"duplicate destination '{entry.destination}' in shell evidence")
        destinations.add(entry.destination)
        result.append(entry)
    return tuple(result)


def load_history(backup_root, allowed_destinations, game_root):
    root = Path(backup_root)
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return ()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PatchError("backup history root is unsafe or not a directory")
    generations = []
    try:
        children = list(root.iterdir())
    except OSError as error:
        raise PatchError(f"cannot enumerate backup history: {error}") from error
    for generation in children:
        try:
            metadata = generation.lstat()
        except OSError as error:
            raise PatchError(f"cannot inspect backup generation: {error}") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            continue
        formats = [
            name for name in ("journal.json", "installation.json", "installation.tsv")
            if (generation / name).exists()
        ]
        if not formats:
            continue
        if len(formats) != 1:
            raise PatchError(f"backup generation '{generation.name}' has ambiguous evidence formats")
        if formats[0] == "journal.json":
            entries = _journal_entries(generation, allowed_destinations)
            sequence = creation_sequence(_read_json(generation / "journal.json"))
        elif formats[0] == "installation.json":
            entries = _json_entries(generation, allowed_destinations, game_root)
            sequence = creation_sequence(_read_json(generation / "installation.json"))
        else:
            entries = _tsv_entries(generation, allowed_destinations)
            sequence = _shell_creation_sequence(generation.name)
        if entries:
            generations.append((sequence, generation.name, entries))
    generations.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(entry for _, _, entries in generations for entry in entries)


def load_recovery_journal(generation, allowed_destinations):
    value = _read_json(generation / "journal.json")
    if not isinstance(value, dict) or value.get("schema_version") != 1 or isinstance(
        value.get("schema_version"), bool
    ):
        raise PatchError("unsupported lifecycle recovery journal")
    if value.get("transaction_id") != generation.name:
        raise PatchError("recovery journal transaction ID does not match its generation")
    if value.get("kind", "apply") not in {"apply", "remove"}:
        raise PatchError("recovery journal has an unknown transaction kind")
    if not isinstance(value.get("status"), str):
        raise PatchError("recovery journal has no valid status")
    operations = value.get("operations")
    if not isinstance(operations, list):
        raise PatchError("recovery journal operations must be a list")
    for record in operations:
        if not isinstance(record, dict) or not isinstance(record.get("status"), str):
            raise PatchError("recovery journal operation is invalid")
        destination = _relative_path(record.get("destination"), "destination")
        if destination not in allowed_destinations:
            raise PatchError(f"destination '{destination}' is not allowed by this release")
        existed = record.get("existed")
        if not isinstance(existed, bool):
            raise PatchError("recovery journal operation has an invalid existed flag")
        before = _hash(record.get("before_sha256"), "before", optional=True)
        _hash(record.get("after_sha256"), "after", optional=True)
        if existed and before is None:
            raise PatchError("recovery journal is missing its incoming SHA-256")
        if not existed and before is not None:
            raise PatchError("recovery journal has inconsistent incoming evidence")
        backup = record.get("backup_path")
        if backup is not None:
            _relative_path(backup, "backup")
        if not existed and backup is not None:
            raise PatchError("recovery journal has inconsistent backup evidence")
        components = record.get("component_ids")
        if not isinstance(components, list) or not all(isinstance(item, str) for item in components):
            raise PatchError("recovery journal component IDs are invalid")
    return value
