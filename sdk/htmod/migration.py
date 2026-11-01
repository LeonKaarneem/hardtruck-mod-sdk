import hashlib
import json
from pathlib import Path
import re

from .errors import PatchError


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HEX_PATTERN = re.compile(r"^(?:[0-9a-f]{2})*$")
MAX_CATALOG_SIZE = 512 * 1024


class MigrationError(PatchError):
    pass


def _integer(value, field):
    if not isinstance(value, int) or isinstance(value, bool):
        raise MigrationError(f"{field} must be an integer")
    return value


def _keys(value, allowed, context):
    if not isinstance(value, dict):
        raise MigrationError(f"{context} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(allowed) - set(value)
    if unknown:
        raise MigrationError(f"{context} contains unknown key '{sorted(unknown)[0]}'")
    if missing:
        raise MigrationError(f"{context} missing required field '{sorted(missing)[0]}'")


def _hex(value, field):
    if not isinstance(value, str) or not HEX_PATTERN.fullmatch(value):
        raise MigrationError(f"{field} must be lowercase even-length hex")
    return bytes.fromhex(value)


def _validated_changes(changes):
    if not isinstance(changes, list):
        raise MigrationError("migration changes must be a list")
    validated = []
    previous_offset = -1
    previous_end = 0
    for index, change in enumerate(changes):
        context = f"migration change {index}"
        _keys(change, ("offset", "original_hex", "replacement_hex"), context)
        offset = _integer(change["offset"], f"{context} offset")
        if offset < 0:
            raise MigrationError(f"{context} offset must be nonnegative")
        original = _hex(change["original_hex"], f"{context} original_hex")
        replacement = _hex(change["replacement_hex"], f"{context} replacement_hex")
        if not original and not replacement:
            raise MigrationError(f"{context} must change bytes")
        if offset <= previous_offset:
            raise MigrationError("migration changes must have strictly sorted offsets")
        if offset < previous_end:
            raise MigrationError("migration changes overlap in original coordinates")
        validated.append((offset, original, replacement))
        previous_offset = offset
        previous_end = offset + len(original)
    return validated


def apply_splices(data, changes, final_length):
    if not isinstance(data, (bytes, bytearray)):
        raise MigrationError("migration source must be bytes")
    final_length = _integer(final_length, "migration final length")
    if final_length < 0:
        raise MigrationError("migration final length must be nonnegative")
    validated = _validated_changes(changes)
    output = bytearray()
    cursor = 0
    for index, (offset, original, replacement) in enumerate(validated):
        end = offset + len(original)
        if offset < cursor or end > len(data):
            raise MigrationError(f"migration change {index} exceeds source bounds")
        if data[offset:end] != original:
            raise MigrationError(f"migration change {index} original bytes do not match source")
        output.extend(data[cursor:offset])
        output.extend(replacement)
        cursor = end
    output.extend(data[cursor:])
    if len(output) != final_length:
        raise MigrationError(
            f"migration final length mismatch: expected {final_length}, got {len(output)}"
        )
    return bytes(output)


def generate_splices(source, pristine):
    if not isinstance(source, (bytes, bytearray)) or not isinstance(pristine, (bytes, bytearray)):
        raise MigrationError("migration generation requires bytes")
    source = bytes(source)
    pristine = bytes(pristine)
    changes = []
    source_offset = pristine_offset = 0
    anchor_size = 32
    search_limit = 65536
    while source_offset < len(source) or pristine_offset < len(pristine):
        if (source_offset < len(source) and pristine_offset < len(pristine)
                and source[source_offset] == pristine[pristine_offset]):
            source_offset += 1
            pristine_offset += 1
            continue
        source_start = source_offset
        pristine_start = pristine_offset
        candidates = []
        source_stop = min(len(source) - anchor_size + 1, source_start + search_limit)
        for candidate in range(source_start, max(source_start, source_stop)):
            anchor = source[candidate:candidate + anchor_size]
            match = pristine.find(anchor, pristine_start + 1)
            if match >= 0:
                candidates.append((candidate - source_start + match - pristine_start, candidate, match))
                break
        pristine_stop = min(len(pristine) - anchor_size + 1, pristine_start + search_limit)
        for candidate in range(pristine_start, max(pristine_start, pristine_stop)):
            anchor = pristine[candidate:candidate + anchor_size]
            match = source.find(anchor, source_start + 1)
            if match >= 0:
                candidates.append((match - source_start + candidate - pristine_start, match, candidate))
                break
        if candidates:
            _, source_end, pristine_end = min(candidates)
        else:
            source_end = len(source)
            pristine_end = len(pristine)
        changes.append({
            "offset": source_start,
            "original_hex": source[source_start:source_end].hex(),
            "replacement_hex": pristine[pristine_start:pristine_end].hex(),
        })
        source_offset = source_end
        pristine_offset = pristine_end
    return changes


def normalize_to_pristine(data, target, catalog):
    _keys(catalog, ("schema_version", "targets"), "migration catalog")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise MigrationError("migration catalog has unsupported schema_version")
    targets = catalog["targets"]
    if not isinstance(targets, dict) or target not in targets:
        raise MigrationError(f"migration catalog has no target '{target}'")
    record = targets[target]
    _keys(record, ("pristine_length", "pristine_sha256", "sources"), f"migration target '{target}'")
    source_hash = hashlib.sha256(data).hexdigest()
    sources = record["sources"]
    if not isinstance(sources, dict) or source_hash not in sources:
        raise MigrationError(f"target '{target}' has unknown source SHA-256 {source_hash}")
    source = sources[source_hash]
    _keys(source, ("length", "changes"), f"migration source '{source_hash}'")
    if len(data) != source["length"]:
        raise MigrationError(
            f"target '{target}' source length mismatch: expected {source['length']}, got {len(data)}"
        )
    pristine = apply_splices(data, source["changes"], record["pristine_length"])
    actual = hashlib.sha256(pristine).hexdigest()
    if actual != record["pristine_sha256"]:
        raise MigrationError(
            f"target '{target}' pristine SHA-256 mismatch: expected "
            f"{record['pristine_sha256']}, got {actual}"
        )
    return pristine


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise MigrationError(f"migration JSON contains duplicate key '{key}'")
        value[key] = item
    return value


def load_catalog(path):
    path = Path(path)
    try:
        if path.stat().st_size > MAX_CATALOG_SIZE:
            raise MigrationError(f"migration catalog exceeds {MAX_CATALOG_SIZE}-byte size budget")
        text = path.read_text(encoding="utf-8")
        catalog = json.loads(text, object_pairs_hook=_unique_object)
    except MigrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"cannot read migration JSON {path}: {error}") from error
    _keys(catalog, ("schema_version", "targets"), "migration catalog")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise MigrationError("migration catalog has unsupported schema_version")
    targets = catalog["targets"]
    if not isinstance(targets, dict) or not targets:
        raise MigrationError("migration catalog targets must be a nonempty object")
    for target, record in targets.items():
        context = f"migration target '{target}'"
        if not isinstance(target, str) or not target:
            raise MigrationError("migration target names must be nonempty strings")
        _keys(record, ("pristine_length", "pristine_sha256", "sources"), context)
        pristine_length = _integer(record["pristine_length"], f"{context} pristine_length")
        if pristine_length < 0:
            raise MigrationError(f"{context} pristine_length must be nonnegative")
        pristine_hash = record["pristine_sha256"]
        if not isinstance(pristine_hash, str) or not SHA256_PATTERN.fullmatch(pristine_hash):
            raise MigrationError(f"{context} pristine_sha256 must be lowercase SHA-256")
        sources = record["sources"]
        if not isinstance(sources, dict) or not sources:
            raise MigrationError(f"{context} sources must be a nonempty object")
        if pristine_hash not in sources:
            raise MigrationError(f"{context} is missing its pristine identity source")
        for source_hash, source in sources.items():
            source_context = f"migration source '{source_hash}'"
            if not isinstance(source_hash, str) or not SHA256_PATTERN.fullmatch(source_hash):
                raise MigrationError(f"{source_context} has invalid SHA-256 key")
            _keys(source, ("length", "changes"), source_context)
            length = _integer(source["length"], f"{source_context} length")
            if length < 0:
                raise MigrationError(f"{source_context} length must be nonnegative")
            _validated_changes(source["changes"])
            if source_hash == pristine_hash:
                if length != pristine_length or source["changes"]:
                    raise MigrationError(f"{source_context} pristine identity must be a no-op")
            elif not source["changes"]:
                raise MigrationError(f"{source_context} non-pristine source must have changes")
    return catalog


def encode_catalog(catalog):
    data = (json.dumps(catalog, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_CATALOG_SIZE:
        raise MigrationError(f"migration catalog exceeds {MAX_CATALOG_SIZE}-byte size budget")
    return data


def add_source(catalog, target, data, pristine):
    if target not in {"game/sys/game.dll", "sys/dx8.dll", "prism3d.exe"} or target not in catalog["targets"]:
        raise MigrationError(f"unknown migration target '{target}'")
    target_record = catalog["targets"][target]
    if len(pristine) != target_record["pristine_length"]:
        raise MigrationError(
            f"target '{target}' pristine length does not match the catalog"
        )
    pristine_hash = hashlib.sha256(pristine).hexdigest()
    if pristine_hash != target_record["pristine_sha256"]:
        raise MigrationError(
            f"target '{target}' pristine SHA-256 does not match the catalog"
        )
    if normalize_to_pristine(pristine, target, catalog) != pristine:
        raise MigrationError(f"target '{target}' pristine identity does not normalize exactly")
    source_hash = hashlib.sha256(data).hexdigest()
    record = {
        "length": len(data),
        "changes": generate_splices(data, pristine),
    }
    existing = target_record["sources"].get(source_hash)
    if existing is not None:
        if existing != record:
            raise MigrationError(
                f"target '{target}' has conflicting duplicate source {source_hash}"
            )
        return catalog, "no_op", source_hash
    changes = record["changes"]
    if (len(changes) == 1 and changes[0]["offset"] == 0
            and len(bytes.fromhex(changes[0]["original_hex"])) == len(data)
            and len(bytes.fromhex(changes[0]["replacement_hex"])) == len(pristine)):
        raise MigrationError("refusing to store a full binary delta")
    updated = dict(catalog)
    updated["targets"] = dict(catalog["targets"])
    updated["targets"][target] = dict(target_record)
    updated["targets"][target]["sources"] = dict(target_record["sources"])
    updated["targets"][target]["sources"][source_hash] = record
    if normalize_to_pristine(data, target, updated) != pristine:
        raise MigrationError(f"target '{target}' source does not normalize to exact pristine bytes")
    encode_catalog(updated)
    return updated, "added", source_hash
