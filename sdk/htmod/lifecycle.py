import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

from .errors import PatchError
from .lifecycle_history import creation_sequence, load_history, load_recovery_journal
from .manifest import Selection
from .migration import normalize_to_pristine
from .patch import PatchRunner, PatchState
from .quick_setup import QUICK_SETUP_DESTINATION
from .runner import load_binary_mods, materialize_data_bytes, verify_pristine_images


def _membership(value):
    return None if value is None else list(value)


@dataclass(frozen=True)
class BinaryTargetStatus:
    target: str
    state: str
    sha256: str | None
    variant: str | None
    components: tuple | None

    def to_dict(self):
        return {
            "target": self.target,
            "state": self.state,
            "sha256": self.sha256,
            "variant": self.variant,
            "components": _membership(self.components),
        }


@dataclass(frozen=True)
class DataFileStatus:
    path: str
    state: str
    expected_sha256: str
    actual_sha256: str | None

    def to_dict(self):
        return {
            "path": self.path,
            "state": self.state,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
        }


@dataclass(frozen=True)
class ComponentStatus:
    id: str
    kind: str
    state: str
    target: str | None
    files: tuple

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "target": self.target,
            "files": [item.to_dict() for item in self.files],
        }


@dataclass(frozen=True)
class StatusReport:
    targets: tuple
    components: tuple
    schema_version: int = 1

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "targets": [item.to_dict() for item in self.targets],
            "components": [item.to_dict() for item in self.components],
        }


@dataclass(frozen=True)
class PlannedDataChange:
    component: str
    path: str
    expected_sha256: str
    action: str = "write"

    def to_dict(self):
        return {
            "component": self.component,
            "path": self.path,
            "action": self.action,
            "expected_sha256": self.expected_sha256,
        }


@dataclass(frozen=True)
class PlannedBinaryChange:
    target: str
    variant: str
    components: tuple
    source_sha256: str
    expected_sha256: str
    action: str = "replace"

    def to_dict(self):
        return {
            "target": self.target,
            "action": self.action,
            "variant": self.variant,
            "components": list(self.components),
            "source_sha256": self.source_sha256,
            "expected_sha256": self.expected_sha256,
        }


@dataclass(frozen=True)
class Plan:
    mode: str
    requested: tuple
    selected: tuple
    data_changes: tuple
    binary_changes: tuple
    schema_version: int = 1

    @property
    def no_op(self):
        return not self.data_changes and not self.binary_changes

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "requested": list(self.requested),
            "selected": list(self.selected),
            "no_op": self.no_op,
            "data_changes": [item.to_dict() for item in self.data_changes],
            "binary_changes": [item.to_dict() for item in self.binary_changes],
        }


def _read_regular(root, relative):
    path = root / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PatchError(f"cannot inspect '{relative}': {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PatchError(f"path '{relative}' is unsafe or not a regular file")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise PatchError(f"path '{relative}' escapes the game directory")
        return resolved.read_bytes()
    except OSError as error:
        raise PatchError(f"cannot read '{relative}': {error}") from error


def _game_root(game_dir):
    try:
        root = Path(game_dir).resolve(strict=True)
    except OSError as error:
        raise PatchError(f"game directory not found: {game_dir}: {error}") from error
    if not root.is_dir():
        raise PatchError(f"game directory not found: {root}")
    return root


def _apply_selection(release, target, pristine, mod_ids, mods):
    images = {target: bytearray(pristine)}
    runner = PatchRunner(images)
    selected = [mod for mod in mods if mod.id in mod_ids]
    if selected:
        runner.apply(selected)
    states = runner.check(mods)
    for mod in mods:
        expected = PatchState.PATCHED if mod.id in mod_ids else PatchState.ORIGINAL
        if states[mod.id] != expected:
            raise PatchError(
                f"target '{target}' selection has unexpected {mod.id} state {states[mod.id].value}"
            )
    result = bytes(images[target])
    if selected:
        runner.restore(selected)
    if images[target] != pristine:
        raise PatchError(f"target '{target}' selection did not restore to pristine bytes")
    return result


def _current_states(release, target, pristine):
    mod_ids = tuple(
        mod_id for mod_id, mod in release.mods.items()
        if mod.kind == "binary" and mod.target == target
    )
    mods = load_binary_mods(release, mod_ids)
    if target == "game/sys/game.dll":
        non_game = tuple(mod_id for mod_id in release.mods if mod_id not in mod_ids)
        selections = []
        for name, variant in release.game_variants.items():
            Selection(variant.components + non_game).validate(release.mods)
            selections.append((name, variant.components))
    elif target == "sys/dx8.dll":
        selections = (("stock", ()), ("vsync", ("dx8_vsync",)))
        for _, components in selections:
            Selection(components).validate(release.mods)
    elif target == "prism3d.exe":
        selections = (("stock", ()), ("cap", ("frame_cap",)))
        for _, components in selections:
            Selection(components).validate(release.mods)
    else:
        raise PatchError(f"target '{target}' has no curated current-state definitions")

    states = []
    by_hash = {}
    for name, components in selections:
        data = _apply_selection(release, target, pristine, components, mods)
        digest = hashlib.sha256(data).hexdigest()
        previous = by_hash.get(digest)
        if previous is not None and previous[1] != components:
            raise PatchError(f"target '{target}' current states have ambiguous hashes")
        record = (name, components, digest)
        states.append(record)
        by_hash.setdefault(digest, record)
    return states


def _inspect(release, game_dir, catalog, selected=None):
    root = _game_root(game_dir)
    targets = []
    details = {}
    for target, pristine_sha256 in release.pristine_sha256.items():
        data = _read_regular(root, target)
        if data is None:
            status = BinaryTargetStatus(target, "missing", None, None, None)
            targets.append(status)
            details[target] = {"status": status, "pristine": None, "current": ()}
            continue
        actual_sha256 = hashlib.sha256(data).hexdigest()
        try:
            sources = catalog["targets"][target]["sources"]
        except (KeyError, TypeError) as error:
            raise PatchError(f"migration catalog has no target '{target}'") from error
        if actual_sha256 not in sources:
            status = BinaryTargetStatus(target, "foreign", actual_sha256, None, None)
            targets.append(status)
            details[target] = {"status": status, "pristine": None, "current": ()}
            continue
        pristine = normalize_to_pristine(data, target, catalog)
        verify_pristine_images(release, {target: pristine})
        if hashlib.sha256(pristine).hexdigest() != pristine_sha256:
            raise PatchError(f"target '{target}' normalized pristine SHA-256 mismatch")
        current = _current_states(release, target, pristine)
        match = next((record for record in current if record[2] == actual_sha256), None)
        if match is not None:
            variant, components, _ = match
            status = BinaryTargetStatus(target, "current", actual_sha256, variant, components)
        elif target == "game/sys/game.dll" and actual_sha256 in release.legacy_game_sha256:
            status = BinaryTargetStatus(target, "legacy", actual_sha256, None, None)
        else:
            status = BinaryTargetStatus(target, "migration", actual_sha256, None, None)
        targets.append(status)
        details[target] = {"status": status, "pristine": pristine, "current": current}

    materialized = materialize_data_bytes(release)
    inferred = []
    for target in targets:
        if target.state == "current":
            inferred.extend(target.components)
    for mod in release.mods.values():
        if mod.kind != "data":
            continue
        records = [
            record for record in mod.files
            if record.destination != QUICK_SETUP_DESTINATION
        ]
        if records and all(
            _read_regular(root, record.destination) == materialized[mod.id][record.destination]
            for record in records
        ):
            inferred.append(mod.id)
    if selected is None:
        selected = resolve_selection(release, (), inferred)
    materialized = materialize_data_bytes(release, selected)
    data_status = {}
    for mod in release.mods.values():
        if mod.kind != "data":
            continue
        files = []
        for record in mod.files:
            expected = materialized[mod.id][record.destination]
            actual = _read_regular(root, record.destination)
            actual_sha256 = None if actual is None else hashlib.sha256(actual).hexdigest()
            if actual is None:
                state = "missing"
            elif actual == expected:
                state = "matching"
            else:
                state = "modified"
            files.append(DataFileStatus(
                record.destination,
                state,
                hashlib.sha256(expected).hexdigest(),
                actual_sha256,
            ))
        states = {item.state for item in files}
        if states == {"missing"}:
            aggregate = "missing"
        elif states == {"matching"}:
            aggregate = "matching"
        elif states == {"modified"}:
            aggregate = "modified"
        else:
            aggregate = "partial"
        data_status[mod.id] = ComponentStatus(mod.id, mod.kind, aggregate, None, tuple(files))

    components = []
    target_by_name = {item.target: item for item in targets}
    for mod in release.mods.values():
        if mod.kind == "data":
            components.append(data_status[mod.id])
            continue
        target = target_by_name[mod.target]
        if target.state == "missing":
            state = "missing"
        elif target.state == "current":
            state = "installed" if mod.id in target.components else "not_installed"
        else:
            state = "unknown"
        components.append(ComponentStatus(mod.id, mod.kind, state, mod.target, ()))
    return StatusReport(tuple(targets), tuple(components)), details


def inspect_status(release, game_dir, catalog):
    report, _ = _inspect(release, game_dir, catalog)
    return report


def resolve_selection(release, requested, installed=()):
    requested = tuple(requested)
    installed = tuple(installed)
    if len(set(requested)) != len(requested):
        raise PatchError("selection contains duplicate mod IDs")
    for mod_id in requested:
        if mod_id not in release.mods:
            raise PatchError(f"selection contains unknown mod '{mod_id}'")
    if len(set(installed)) != len(installed):
        raise PatchError("installed base contains duplicate mod IDs")
    for mod_id in installed:
        if mod_id not in release.mods:
            raise PatchError(f"installed base contains unknown mod '{mod_id}'")
    selected = set(installed) | set(requested)
    pending = list(selected)
    while pending:
        mod_id = pending.pop()
        for required in release.mods[mod_id].requires:
            if required not in selected:
                selected.add(required)
                pending.append(required)
    ordered = tuple(mod_id for mod_id in release.mods if mod_id in selected)
    Selection(ordered).validate(release.mods)
    return ordered


def build_plan(release, game_dir, catalog, requested, exact=False):
    report, details = _inspect(release, game_dir, catalog)
    requested_set = set(requested)
    installed = ()
    if not exact:
        installed = tuple(
            component.id for component in report.components
            if component.state == "installed"
            or (component.kind == "data" and component.state == "matching")
        )
    selected = resolve_selection(release, requested, installed)
    report, details = _inspect(release, game_dir, catalog, selected)
    selected_set = set(selected)
    ordered_requested = tuple(mod_id for mod_id in release.mods if mod_id in requested_set)
    component_by_id = {component.id: component for component in report.components}

    data_changes = []
    for mod in release.mods.values():
        if mod.kind != "data" or mod.id not in selected_set:
            continue
        files = component_by_id[mod.id].files
        for record, status in zip(mod.files, files):
            if status.state != "matching":
                data_changes.append(PlannedDataChange(
                    mod.id,
                    record.destination,
                    status.expected_sha256,
                ))

    binary_changes = []
    for target, detail in details.items():
        desired = tuple(
            mod_id for mod_id, mod in release.mods.items()
            if mod_id in selected_set and mod.kind == "binary" and mod.target == target
        )
        status = detail["status"]
        if status.state == "current" and status.components == desired:
            continue
        if not exact and status.state != "current" and not desired:
            continue
        if not exact and status.state != "current":
            raise PatchError(
                f"target '{target}' binary reconstruction requires --exact"
            )
        if status.state in {"missing", "foreign"}:
            raise PatchError(
                f"target '{target}' has no recognized source for binary reconstruction"
            )
        final = next((record for record in detail["current"] if record[1] == desired), None)
        if final is None:
            raise PatchError(f"target '{target}' final selection is not a curated variant")
        variant, components, expected_sha256 = final
        binary_changes.append(PlannedBinaryChange(
            target,
            variant,
            components,
            status.sha256,
            expected_sha256,
        ))

    return Plan(
        "exact" if exact else "additive",
        ordered_requested,
        selected,
        tuple(data_changes),
        tuple(binary_changes),
    )


@dataclass(frozen=True)
class RemovalPlan:
    mode: str
    requested: tuple
    selected: tuple
    retained: tuple
    data_changes: tuple
    binary_changes: tuple
    schema_version: int = 1

    @property
    def no_op(self):
        return not self.binary_changes and all(
            change.action != "delete" for change in self.data_changes
        )

    @property
    def partial(self):
        return any(change.action == "skip_modified" for change in self.data_changes)

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "requested": list(self.requested),
            "selected": list(self.selected),
            "retained": list(self.retained),
            "no_op": self.no_op,
            "data_changes": [item.to_dict() for item in self.data_changes],
            "binary_changes": [item.to_dict() for item in self.binary_changes],
        }


@dataclass(frozen=True)
class PlannedHistoryChange:
    destination: str
    action: str
    component_ids: tuple
    original_existed: bool
    installed_sha256: str
    original_sha256: str | None
    current_sha256: str | None
    status: str
    source: object

    def to_dict(self):
        return {
            "destination": self.destination,
            "action": self.action,
            "component_ids": list(self.component_ids),
            "current_sha256": self.current_sha256,
            "installed_sha256": self.installed_sha256,
            "original_sha256": self.original_sha256,
            "status": self.status,
        }


@dataclass(frozen=True)
class HistoryRemovalPlan:
    history_changes: tuple
    mode: str = "backup_history"
    schema_version: int = 1

    @property
    def no_op(self):
        return all(change.status != "prepared" for change in self.history_changes)

    @property
    def partial(self):
        return any(change.status.startswith("skipped_") for change in self.history_changes)

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "no_op": self.no_op,
            "history_changes": [change.to_dict() for change in self.history_changes],
        }


def build_history_removal_plan(release, game_dir, backup_root):
    root = _game_root(game_dir)
    allowed = {
        record.destination
        for mod in release.mods.values() if mod.kind == "data"
        for record in mod.files
    } | set(release.pristine_sha256)
    history = load_history(backup_root, allowed, root)
    actual = {}
    for entry in history:
        if entry.destination not in actual:
            current = _read_regular(root, entry.destination)
            actual[entry.destination] = (
                None if current is None else hashlib.sha256(current).hexdigest()
            )
    baseline = {}
    for entry in history:
        baseline[entry.destination] = entry.original_sha256
    already_final = {
        destination for destination, current_sha256 in actual.items()
        if current_sha256 == baseline[destination]
    }
    virtual = dict(actual)
    blocked = set()
    changes = []
    for entry in history:
        destination = entry.destination
        current_sha256 = virtual[destination]
        action = "restore" if entry.original_existed else "delete"
        if destination in already_final:
            continue
        if destination in blocked:
            status = "skipped_blocked"
        elif current_sha256 == entry.original_sha256:
            continue
        elif current_sha256 != entry.installed_sha256:
            status = "skipped_modified"
            blocked.add(destination)
        else:
            status = "prepared"
            virtual[destination] = entry.original_sha256
        changes.append(PlannedHistoryChange(
            destination,
            action,
            entry.component_ids,
            entry.original_existed,
            entry.installed_sha256,
            entry.original_sha256,
            current_sha256,
            status,
            entry,
        ))
    return HistoryRemovalPlan(tuple(changes))


def build_removal_plan(release, game_dir, catalog, requested, cascade_dependents=False):
    requested = tuple(requested)
    if not requested:
        raise PatchError("selective removal requires at least one --mod")
    if len(set(requested)) != len(requested):
        raise PatchError("removal selection contains duplicate mod IDs")
    for mod_id in requested:
        if mod_id not in release.mods:
            raise PatchError(f"removal selection contains unknown mod '{mod_id}'")

    report, details = _inspect(release, game_dir, catalog)
    components = {component.id: component for component in report.components}
    present = {
        component.id for component in report.components
        if component.state == "installed"
        or (component.kind == "data" and component.state != "missing")
    }
    removed = set(requested)
    for mod_id in requested:
        mod = release.mods[mod_id]
        if mod.kind == "binary" and details[mod.target]["status"].state != "current":
            raise PatchError(
                f"target '{mod.target}' has unknown binary membership; selective removal is blocked"
            )

    changed = True
    while changed:
        changed = False
        for mod in release.mods.values():
            if mod.id not in present or mod.id in removed:
                continue
            required = next((item for item in mod.requires if item in removed), None)
            if required is None:
                continue
            if not cascade_dependents:
                raise PatchError(
                    f"retained mod '{mod.id}' requires removed mod '{required}'; use cascade to remove dependents"
                )
            removed.add(mod.id)
            changed = True

    retained = tuple(mod_id for mod_id in release.mods if mod_id in present and mod_id not in removed)
    Selection(retained).validate(release.mods)
    selected = tuple(mod_id for mod_id in release.mods if mod_id in removed)
    ordered_requested = tuple(mod_id for mod_id in release.mods if mod_id in requested)

    data_changes = []
    for mod_id in selected:
        mod = release.mods[mod_id]
        if mod.kind != "data":
            continue
        for record, status in zip(mod.files, components[mod_id].files):
            action = {
                "matching": "delete",
                "modified": "skip_modified",
                "missing": "missing",
            }[status.state]
            data_changes.append(PlannedDataChange(
                mod_id,
                record.destination,
                status.expected_sha256,
                action,
            ))

    binary_changes = []
    affected_targets = {
        release.mods[mod_id].target for mod_id in selected
        if release.mods[mod_id].kind == "binary"
    }
    for target in sorted(affected_targets):
        detail = details[target]
        status = detail["status"]
        if status.state != "current":
            raise PatchError(
                f"target '{target}' has unknown binary membership; selective removal is blocked"
            )
        desired = tuple(mod_id for mod_id in status.components if mod_id not in removed)
        if desired == status.components:
            continue
        final = next((record for record in detail["current"] if record[1] == desired), None)
        if final is None:
            raise PatchError(f"target '{target}' final retained selection is not a curated variant")
        variant, final_components, expected_sha256 = final
        binary_changes.append(PlannedBinaryChange(
            target,
            variant,
            final_components,
            status.sha256,
            expected_sha256,
        ))

    return RemovalPlan(
        "selective",
        ordered_requested,
        selected,
        retained,
        tuple(data_changes),
        tuple(binary_changes),
    )


@dataclass(frozen=True)
class AppliedOperation:
    destination: str
    action: str
    component_ids: tuple
    existed: bool
    before_sha256: str | None
    after_sha256: str | None
    backup_path: str | None
    status: str

    def to_dict(self):
        return {
            "destination": self.destination,
            "action": self.action,
            "component_ids": list(self.component_ids),
            "existed": self.existed,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "backup_path": self.backup_path,
            "status": self.status,
        }


@dataclass(frozen=True)
class Result:
    transaction_id: str
    status: str
    no_op: bool
    operations: tuple = ()
    warnings: tuple = ()
    errors: tuple = ()
    schema_version: int = 1

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "status": self.status,
            "no_op": self.no_op,
            "operations": [item.to_dict() for item in self.operations],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def _fsync_directory(path):
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory_flags():
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _supports_directory_fd():
    functions = (os.open, os.mkdir, os.stat, os.unlink, os.rename)
    return os.name == "posix" and all(function in os.supports_dir_fd for function in functions)


def _open_directory(path, create=False):
    path = Path(path)
    if not path.is_absolute():
        raise PatchError("directory descriptor requires an absolute path")
    descriptor = os.open(path.anchor, _directory_flags())
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            parent = current
            current = current / part
            created = False
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, dir_fd=descriptor)
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
                created = True
            os.close(descriptor)
            descriptor = child
            if created:
                _fsync_directory(current)
                _fsync_directory(parent)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_relative_directory(descriptor, base, relative, create=False):
    descriptor = os.dup(descriptor)
    current = Path(base)
    try:
        for part in Path(relative).parts:
            parent = current
            current = current / part
            created = False
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, dir_fd=descriptor)
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
                created = True
            os.close(descriptor)
            descriptor = child
            if created:
                _fsync_directory(current)
                _fsync_directory(parent)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_parent_descriptor(path, descriptor):
    metadata = path.lstat()
    opened = os.fstat(descriptor)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise PatchError(f"destination parent changed before mutation: '{path.name}'")


def _read_at(descriptor, name):
    source = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
    try:
        if not stat.S_ISREG(os.fstat(source).st_mode):
            raise PatchError(f"destination '{name}' is not a regular file")
        with os.fdopen(source, "rb") as input_file:
            source = None
            return input_file.read()
    finally:
        if source is not None:
            os.close(source)


def _temporary_destination(destination, data, parent_fd=None):
    if parent_fd is None:
        descriptor, name = tempfile.mkstemp(prefix=".htmod-apply-", dir=destination.parent)
    else:
        name = f".htmod-apply-{uuid.uuid4().hex}"
        descriptor = os.open(
            name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
    temporary = Path(name) if parent_fd is None else destination.parent / name
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if parent_fd is None:
            if destination.exists():
                os.chmod(temporary, stat.S_IMODE(destination.stat().st_mode))
        else:
            try:
                metadata = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                metadata = None
            if metadata is not None:
                if not stat.S_ISREG(metadata.st_mode):
                    raise PatchError(f"destination '{destination.name}' is not a regular file")
                os.chmod(temporary.name, stat.S_IMODE(metadata.st_mode), dir_fd=parent_fd)
        return temporary
    except BaseException:
        if parent_fd is None:
            temporary.unlink(missing_ok=True)
        else:
            os.unlink(temporary.name, dir_fd=parent_fd)
        raise


def _discard_temporary(temporary, parent_fd=None):
    try:
        if parent_fd is None:
            temporary.unlink(missing_ok=True)
        else:
            os.unlink(temporary.name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _require_absent_at(descriptor, name, relative):
    try:
        os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise PatchError(f"destination '{relative}' appeared immediately before replacement")


def _replace_destination(
    temporary, destination, root=None, relative=None, expected=None, parent_fd=None,
):
    if parent_fd is None:
        if root is not None and relative is not None:
            _validate_destination(root, relative)
            if expected is not None and destination.read_bytes() != expected:
                raise PatchError(f"destination '{relative}' changed immediately before replacement")
            if expected is None:
                try:
                    destination.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise PatchError(
                        f"destination '{relative}' appeared immediately before replacement"
                    )
        os.replace(temporary, destination)
    else:
        _verify_parent_descriptor(destination.parent, parent_fd)
        if expected is not None and _read_at(parent_fd, destination.name) != expected:
            raise PatchError(f"destination '{relative}' changed immediately before replacement")
        if expected is None:
            _require_absent_at(parent_fd, destination.name, relative)
        _verify_parent_descriptor(destination.parent, parent_fd)
        os.replace(
            temporary.name,
            destination.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    _fsync_directory(destination.parent)


def _write_json_atomic(path, value):
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if not _supports_directory_fd():
        descriptor, name = tempfile.mkstemp(prefix=".htmod-journal-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return
    parent_fd = _open_directory(path.parent)
    temporary = None
    try:
        temporary = _temporary_destination(path, data, parent_fd=parent_fd)
        os.replace(temporary.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            _discard_temporary(temporary, parent_fd)
        os.close(parent_fd)


def _write_journal(path, record):
    _write_json_atomic(Path(path), record)


def _write_backup(generation, operation, data):
    path = generation / operation["backup_path"]
    try:
        path.relative_to(generation)
    except ValueError as error:
        raise PatchError("backup path escapes its generation") from error
    try:
        if not _supports_directory_fd():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            _fsync_directory(path.parent)
            return
        generation_fd = _open_directory(generation)
        try:
            parent_fd = _open_relative_directory(
                generation_fd,
                generation,
                Path(operation["backup_path"]).parent,
                create=True,
            )
            try:
                descriptor = os.open(
                    path.name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                _fsync_directory(path.parent)
            finally:
                os.close(parent_fd)
        finally:
            os.close(generation_fd)
    except OSError as error:
        raise PatchError(f"backup failed for '{operation['destination']}': {error}") from error


def _read_backup(generation, operation):
    path = generation / operation["backup_path"]
    if not _supports_directory_fd():
        return path.read_bytes()
    generation_fd = _open_directory(generation)
    try:
        parent_fd = _open_relative_directory(
            generation_fd, generation, Path(operation["backup_path"]).parent,
        )
        try:
            return _read_at(parent_fd, path.name)
        finally:
            os.close(parent_fd)
    finally:
        os.close(generation_fd)


def _validate_destination(root, relative, create=False):
    current = root
    for part in Path(relative).parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not create:
                return root / relative
            current.mkdir()
            metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PatchError(f"destination parent for '{relative}' is unsafe")
    path = root / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return path
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PatchError(f"destination '{relative}' is unsafe or not a regular file")
    return path


def _verify_write(destination, expected_sha256):
    try:
        metadata = destination.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PatchError(f"post-write destination '{destination.name}' is unsafe")
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
    except OSError as error:
        raise PatchError(f"post-write verification failed: {error}") from error
    if actual != expected_sha256:
        raise PatchError("post-write SHA-256 mismatch")


def _restore_operation(root, destination, operation, generation):
    current = _read_regular(root, destination)
    original_missing = not operation["existed"] and current is None
    original_present = (
        operation["existed"]
        and current is not None
        and hashlib.sha256(current).hexdigest() == operation["before_sha256"]
    )
    if original_missing or original_present:
        return
    after_missing = operation["after_sha256"] is None and current is None
    after_present = (
        operation["after_sha256"] is not None
        and current is not None
        and hashlib.sha256(current).hexdigest() == operation["after_sha256"]
    )
    if not after_missing and not after_present:
        raise PatchError(f"destination '{destination}' was modified after install; refusing rollback")
    path = _validate_destination(root, destination, create=True)
    parent_fd = None
    root_fd = None
    temporary = None
    try:
        if _supports_directory_fd():
            root_fd = _open_directory(root)
            parent_fd = _open_relative_directory(root_fd, root, Path(destination).parent)
            _verify_parent_descriptor(path.parent, parent_fd)
            if current is None:
                try:
                    os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise PatchError(f"destination '{destination}' changed before rollback")
            elif _read_at(parent_fd, path.name) != current:
                raise PatchError(f"destination '{destination}' changed before rollback")
        if operation["existed"]:
            original = _read_backup(generation, operation)
            if hashlib.sha256(original).hexdigest() != operation["before_sha256"]:
                raise PatchError(f"backup SHA-256 mismatch for '{destination}'")
            temporary = _temporary_destination(path, original, parent_fd=parent_fd)
            _replace_destination(
                temporary,
                path,
                root=root,
                relative=destination,
                expected=current,
                parent_fd=parent_fd,
            )
            restored = _read_regular(root, destination)
            if restored != original:
                raise PatchError(f"rollback verification failed for '{destination}'")
        elif parent_fd is None:
            _validate_destination(root, destination)
            if path.read_bytes() != current:
                raise PatchError(f"destination '{destination}' changed before rollback")
            path.unlink()
            _fsync_directory(path.parent)
        else:
            _verify_parent_descriptor(path.parent, parent_fd)
            if _read_at(parent_fd, path.name) != current:
                raise PatchError(f"destination '{destination}' changed before rollback")
            os.unlink(path.name, dir_fd=parent_fd)
            _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            _discard_temporary(temporary, parent_fd)
        if parent_fd is not None:
            os.close(parent_fd)
        if root_fd is not None:
            os.close(root_fd)


def _stage_binary(release, target, source, change, catalog, staging_root):
    pristine = normalize_to_pristine(source, target, catalog)
    verify_pristine_images(release, {target: pristine})
    mod_ids = tuple(
        mod_id for mod_id, mod in release.mods.items()
        if mod.kind == "binary" and mod.target == target
    )
    mods = load_binary_mods(release, mod_ids)
    data = _apply_selection(release, target, pristine, change.components, mods)
    actual = hashlib.sha256(data).hexdigest()
    if actual != change.expected_sha256:
        raise PatchError(
            f"target '{target}' staged SHA-256 mismatch: expected {change.expected_sha256}, got {actual}"
        )
    path = staging_root / target
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    if hashlib.sha256(path.read_bytes()).hexdigest() != change.expected_sha256:
        raise PatchError(f"target '{target}' staged file SHA-256 mismatch")
    return path


def _verify_lock_file(path, descriptor):
    metadata = path.lstat()
    opened = os.fstat(descriptor)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise PatchError("transaction lock file is unsafe or changed")


def _lock_descriptor(descriptor):
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise PatchError("another lifecycle transaction holds the game lock") from error


def _unlock_descriptor(descriptor):
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _transaction_lock(root, transaction_id):
    metadata_root = root / ".htmod"
    try:
        metadata = metadata_root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PatchError("transaction metadata directory is unsafe")
    except FileNotFoundError:
        metadata_root.mkdir()
        _fsync_directory(metadata_root)
        _fsync_directory(root)
    path = metadata_root / "transaction.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        _verify_lock_file(path, descriptor)
        _lock_descriptor(descriptor)
        locked = True
        _verify_lock_file(path, descriptor)
        data = transaction_id.encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written == 0:
                raise PatchError("could not write transaction lock metadata")
            view = view[written:]
        os.fsync(descriptor)
        yield
    finally:
        if locked:
            _unlock_descriptor(descriptor)
        os.close(descriptor)


class Lifecycle:
    def __init__(self, release, game_dir, migrations, backup_dir=None):
        self.release = release
        self.root = _game_root(game_dir)
        if isinstance(migrations, (str, os.PathLike)):
            from .migration import load_catalog
            migrations = load_catalog(migrations)
        self.catalog = migrations
        self.backup_dir = Path(backup_dir).absolute() if backup_dir is not None else None

    def status(self):
        return inspect_status(self.release, self.root, self.catalog)

    def plan(self, requested=(), exact=False):
        return build_plan(self.release, self.root, self.catalog, requested, exact=exact)

    def _generation(self, transaction_id):
        root = self.backup_dir or self.root / ".htmod/backups"
        generation = root / transaction_id
        if _supports_directory_fd():
            root_fd = _open_directory(root, create=True)
            try:
                os.mkdir(transaction_id, dir_fd=root_fd)
                generation_fd = os.open(transaction_id, _directory_flags(), dir_fd=root_fd)
                os.close(generation_fd)
            finally:
                os.close(root_fd)
        else:
            if root.exists() and (root.is_symlink() or not root.is_dir()):
                raise PatchError("backup root is unsafe or not a directory")
            root.mkdir(parents=True, exist_ok=True)
            generation.mkdir()
        _fsync_directory(generation)
        _fsync_directory(root)
        return generation

    def _message(self, error):
        message = str(error)
        for path, label in ((self.root, "<game>"), (self.backup_dir, "<backups>")):
            if path is not None:
                message = message.replace(str(path), label)
        return message

    @staticmethod
    def _result(transaction_id, status, journal, warnings=(), errors=()):
        operations = tuple(AppliedOperation(
            item["destination"],
            item["action"],
            tuple(item["component_ids"]),
            item["existed"],
            item["before_sha256"],
            item["after_sha256"],
            item["backup_path"],
            item["status"],
        ) for item in journal["operations"])
        return Result(transaction_id, status, False, operations, tuple(warnings), tuple(errors))

    def apply(self, plan=None, requested=(), exact=False):
        if plan is None:
            plan = self.plan(requested, exact=exact)
        if not isinstance(plan, Plan) or plan.mode not in {"additive", "exact"}:
            raise PatchError("apply requires a valid lifecycle plan")
        fresh = self.plan(plan.requested, exact=plan.mode == "exact")
        if fresh != plan:
            raise PatchError("plan is stale or does not match fresh disk state")
        transaction_id = uuid.uuid4().hex
        if plan.no_op:
            with _transaction_lock(self.root, transaction_id):
                locked = self.plan(plan.requested, exact=plan.mode == "exact")
                if locked != plan:
                    raise PatchError("no-op plan changed while acquiring the game lock")
                return Result(transaction_id, "no_op", True)

        destinations = [change.path for change in plan.data_changes]
        destinations.extend(change.target for change in plan.binary_changes)
        incoming = {}
        for destination in destinations:
            _validate_destination(self.root, destination)
            incoming[destination] = _read_regular(self.root, destination)

        with tempfile.TemporaryDirectory(prefix="htmod-lifecycle-") as temporary:
            staging_root = Path(temporary)
            materialized = materialize_data_bytes(self.release, plan.selected)
            payloads = {}
            for change in plan.data_changes:
                data = materialized[change.component][change.path]
                if hashlib.sha256(data).hexdigest() != change.expected_sha256:
                    raise PatchError(f"data payload SHA-256 mismatch for '{change.path}'")
                payloads[change.path] = data
            for change in plan.binary_changes:
                source = incoming[change.target]
                if source is None or hashlib.sha256(source).hexdigest() != change.source_sha256:
                    raise PatchError(f"target '{change.target}' changed before staging")
                staged = _stage_binary(
                    self.release, change.target, source, change, self.catalog, staging_root,
                )
                payloads[change.target] = staged

            with _transaction_lock(self.root, transaction_id):
                for destination in destinations:
                    _validate_destination(self.root, destination)
                    if _read_regular(self.root, destination) != incoming[destination]:
                        raise PatchError(f"destination '{destination}' changed before backup")

                operations = []
                for change in plan.data_changes:
                    before = incoming[change.path]
                    operations.append({
                        "destination": change.path,
                        "action": change.action,
                        "component_ids": [change.component],
                        "existed": before is not None,
                        "before_sha256": None if before is None else hashlib.sha256(before).hexdigest(),
                        "after_sha256": change.expected_sha256,
                        "backup_path": None if before is None else f"files/{change.path}",
                        "status": "prepared",
                    })
                for change in plan.binary_changes:
                    before = incoming[change.target]
                    operations.append({
                        "destination": change.target,
                        "action": change.action,
                        "component_ids": list(change.components),
                        "existed": True,
                        "before_sha256": hashlib.sha256(before).hexdigest(),
                        "after_sha256": change.expected_sha256,
                        "backup_path": f"files/{change.target}",
                        "status": "prepared",
                    })
                journal = {
                    "schema_version": 1,
                    "transaction_id": transaction_id,
                    "created_ns": time.time_ns(),
                    "status": "prepared",
                    "component_ids": list(plan.selected),
                    "operations": operations,
                }
                generation = None
                installed = []
                warnings = []
                errors = []
                try:
                    generation = self._generation(transaction_id)
                    journal_path = generation / "journal.json"
                    _write_journal(journal_path, journal)
                    for operation in operations:
                        if operation["existed"]:
                            _write_backup(generation, operation, incoming[operation["destination"]])
                    journal["status"] = "in_progress"
                    _write_journal(journal_path, journal)

                    for operation in operations:
                        destination = operation["destination"]
                        if _read_regular(self.root, destination) != incoming[destination]:
                            raise PatchError(f"destination '{destination}' changed before replacement")
                        path = _validate_destination(self.root, destination, create=True)
                        payload = payloads[destination]
                        data = payload.read_bytes() if isinstance(payload, Path) else payload
                        if hashlib.sha256(data).hexdigest() != operation["after_sha256"]:
                            raise PatchError(f"payload SHA-256 mismatch for '{destination}'")
                        root_fd = None
                        parent_fd = None
                        staged = None
                        try:
                            if _supports_directory_fd():
                                root_fd = _open_directory(self.root)
                                parent_fd = _open_relative_directory(
                                    root_fd,
                                    self.root,
                                    Path(destination).parent,
                                    create=True,
                                )
                                _verify_parent_descriptor(path.parent, parent_fd)
                            staged = _temporary_destination(path, data, parent_fd=parent_fd)
                            installed.append(operation)
                            operation["status"] = "in_progress"
                            _write_journal(journal_path, journal)
                            _replace_destination(
                                staged,
                                path,
                                root=self.root,
                                relative=destination,
                                expected=incoming[destination],
                                parent_fd=parent_fd,
                            )
                            operation["status"] = "installed"
                            _write_journal(journal_path, journal)
                            _verify_write(path, operation["after_sha256"])
                        finally:
                            if staged is not None:
                                _discard_temporary(staged, parent_fd)
                            if parent_fd is not None:
                                os.close(parent_fd)
                            if root_fd is not None:
                                os.close(root_fd)

                    final = self.plan(plan.requested, exact=plan.mode == "exact")
                    if not final.no_op or final.selected != plan.selected:
                        raise PatchError("post-apply status does not match the requested component set")
                    journal["status"] = "installed"
                    _write_journal(journal_path, journal)
                    return self._result(transaction_id, "installed", journal)
                except Exception as error:
                    errors.append(self._message(error))
                    rollback_failed = False
                    for operation in reversed(installed):
                        destination = operation["destination"]
                        try:
                            _restore_operation(self.root, destination, operation, generation)
                            operation["status"] = "rolled_back"
                        except Exception as rollback_error:
                            rollback_failed = True
                            operation["status"] = "rollback_failed"
                            warnings.append(self._message(rollback_error))
                        try:
                            _write_journal(journal_path, journal)
                        except Exception as journal_error:
                            rollback_failed = True
                            warnings.append(self._message(journal_error))
                    status = "rollback_failed" if rollback_failed else "rolled_back"
                    journal["status"] = status
                    try:
                        _write_journal(journal_path, journal)
                    except Exception as journal_error:
                        status = "partial"
                        journal["status"] = status
                        warnings.append(self._message(journal_error))
                    return self._result(transaction_id, status, journal, warnings, errors)

    def _removal_operations(self, plan):
        operations = []
        for change in plan.data_changes:
            current = _read_regular(self.root, change.path)
            current_sha256 = None if current is None else hashlib.sha256(current).hexdigest()
            if change.action == "delete" and current_sha256 != change.expected_sha256:
                raise PatchError(f"destination '{change.path}' changed before removal")
            if change.action == "skip_modified" and current is None:
                raise PatchError(f"destination '{change.path}' changed before removal")
            if change.action == "missing" and current is not None:
                raise PatchError(f"destination '{change.path}' changed before removal")
            operations.append({
                "destination": change.path,
                "action": "delete",
                "component_ids": [change.component],
                "existed": current is not None,
                "before_sha256": current_sha256,
                "after_sha256": None,
                "backup_path": f"files/{change.path}" if change.action == "delete" else None,
                "status": {
                    "delete": "prepared",
                    "skip_modified": "skipped_modified",
                    "missing": "missing",
                }[change.action],
            })
        for change in plan.binary_changes:
            current = _read_regular(self.root, change.target)
            if current is None or hashlib.sha256(current).hexdigest() != change.source_sha256:
                raise PatchError(f"target '{change.target}' changed before removal")
            operations.append({
                "destination": change.target,
                "action": "replace",
                "component_ids": list(change.components),
                "existed": True,
                "before_sha256": change.source_sha256,
                "after_sha256": change.expected_sha256,
                "backup_path": f"files/{change.target}",
                "status": "prepared",
            })
        return operations

    def remove(self, requested=(), cascade_dependents=False, all=False, dry_run=False):
        if all:
            if requested or cascade_dependents:
                raise PatchError("--all cannot be combined with selective removal options")
            return self._remove_all(dry_run=dry_run)
        plan = build_removal_plan(
            self.release,
            self.root,
            self.catalog,
            requested,
            cascade_dependents=cascade_dependents,
        )
        if dry_run:
            return plan
        transaction_id = uuid.uuid4().hex
        if plan.no_op:
            with _transaction_lock(self.root, transaction_id):
                locked = build_removal_plan(
                    self.release,
                    self.root,
                    self.catalog,
                    requested,
                    cascade_dependents=cascade_dependents,
                )
                if locked != plan:
                    raise PatchError("no-op removal plan changed while acquiring the game lock")
                operations = tuple(AppliedOperation(
                    item["destination"],
                    item["action"],
                    tuple(item["component_ids"]),
                    item["existed"],
                    item["before_sha256"],
                    item["after_sha256"],
                    item["backup_path"],
                    item["status"],
                ) for item in self._removal_operations(locked))
                status = "partial" if locked.partial else "no_op"
                return Result(transaction_id, status, True, operations)

        destinations = [change.path for change in plan.data_changes if change.action == "delete"]
        destinations.extend(change.target for change in plan.binary_changes)
        incoming = {}
        for destination in destinations:
            _validate_destination(self.root, destination)
            incoming[destination] = _read_regular(self.root, destination)

        with tempfile.TemporaryDirectory(prefix="htmod-lifecycle-remove-") as temporary:
            staging_root = Path(temporary)
            payloads = {}
            for change in plan.binary_changes:
                source = incoming[change.target]
                if source is None or hashlib.sha256(source).hexdigest() != change.source_sha256:
                    raise PatchError(f"target '{change.target}' changed before staging")
                payloads[change.target] = _stage_binary(
                    self.release, change.target, source, change, self.catalog, staging_root,
                )

            with _transaction_lock(self.root, transaction_id):
                for destination in destinations:
                    _validate_destination(self.root, destination)
                    if _read_regular(self.root, destination) != incoming[destination]:
                        raise PatchError(f"destination '{destination}' changed before backup")
                operations = self._removal_operations(plan)
                journal = {
                    "schema_version": 1,
                    "transaction_id": transaction_id,
                    "created_ns": time.time_ns(),
                    "kind": "remove",
                    "mode": "selective",
                    "status": "prepared",
                    "component_ids": list(plan.selected),
                    "retained_component_ids": list(plan.retained),
                    "operations": operations,
                }
                generation = None
                installed = []
                warnings = []
                errors = []
                try:
                    generation = self._generation(transaction_id)
                    journal_path = generation / "journal.json"
                    _write_journal(journal_path, journal)
                    for operation in operations:
                        if operation["status"] == "prepared" and operation["existed"]:
                            _write_backup(generation, operation, incoming[operation["destination"]])
                    journal["status"] = "in_progress"
                    _write_journal(journal_path, journal)

                    for operation in operations:
                        if operation["status"] != "prepared":
                            continue
                        destination = operation["destination"]
                        if _read_regular(self.root, destination) != incoming[destination]:
                            raise PatchError(f"destination '{destination}' changed before removal")
                        path = _validate_destination(self.root, destination, create=True)
                        root_fd = None
                        parent_fd = None
                        staged = None
                        try:
                            if _supports_directory_fd():
                                root_fd = _open_directory(self.root)
                                parent_fd = _open_relative_directory(
                                    root_fd,
                                    self.root,
                                    Path(destination).parent,
                                    create=True,
                                )
                                _verify_parent_descriptor(path.parent, parent_fd)
                                if _read_at(parent_fd, path.name) != incoming[destination]:
                                    raise PatchError(
                                        f"destination '{destination}' changed before removal"
                                    )
                            installed.append(operation)
                            operation["status"] = "in_progress"
                            _write_journal(journal_path, journal)
                            if operation["action"] == "delete":
                                if parent_fd is None:
                                    _validate_destination(self.root, destination)
                                    if path.read_bytes() != incoming[destination]:
                                        raise PatchError(
                                            f"destination '{destination}' changed before removal"
                                        )
                                    path.unlink()
                                else:
                                    _verify_parent_descriptor(path.parent, parent_fd)
                                    if _read_at(parent_fd, path.name) != incoming[destination]:
                                        raise PatchError(
                                            f"destination '{destination}' changed before removal"
                                        )
                                    os.unlink(path.name, dir_fd=parent_fd)
                                _fsync_directory(path.parent)
                                if _read_regular(self.root, destination) is not None:
                                    raise PatchError(f"post-removal verification failed for '{destination}'")
                                operation["status"] = "removed"
                            else:
                                data = payloads[destination].read_bytes()
                                if hashlib.sha256(data).hexdigest() != operation["after_sha256"]:
                                    raise PatchError(f"payload SHA-256 mismatch for '{destination}'")
                                staged = _temporary_destination(path, data, parent_fd=parent_fd)
                                _replace_destination(
                                    staged,
                                    path,
                                    root=self.root,
                                    relative=destination,
                                    expected=incoming[destination],
                                    parent_fd=parent_fd,
                                )
                                operation["status"] = "replaced"
                                _verify_write(path, operation["after_sha256"])
                            _write_journal(journal_path, journal)
                        finally:
                            if staged is not None:
                                _discard_temporary(staged, parent_fd)
                            if parent_fd is not None:
                                os.close(parent_fd)
                            if root_fd is not None:
                                os.close(root_fd)

                    final = build_removal_plan(
                        self.release,
                        self.root,
                        self.catalog,
                        requested,
                        cascade_dependents=cascade_dependents,
                    )
                    if not final.no_op or final.retained != plan.retained:
                        raise PatchError("post-removal status does not match the retained component set")
                    journal["status"] = "partial" if plan.partial else "removed"
                    _write_journal(journal_path, journal)
                    return self._result(transaction_id, journal["status"], journal)
                except Exception as error:
                    errors.append(self._message(error))
                    rollback_failed = False
                    for operation in reversed(installed):
                        destination = operation["destination"]
                        try:
                            _restore_operation(self.root, destination, operation, generation)
                            operation["status"] = "rolled_back"
                        except Exception as rollback_error:
                            rollback_failed = True
                            operation["status"] = "rollback_failed"
                            warnings.append(self._message(rollback_error))
                        try:
                            _write_journal(journal_path, journal)
                        except Exception as journal_error:
                            rollback_failed = True
                            warnings.append(self._message(journal_error))
                    status = "rollback_failed" if rollback_failed else "rolled_back"
                    journal["status"] = status
                    try:
                        _write_journal(journal_path, journal)
                    except Exception as journal_error:
                        status = "partial"
                        journal["status"] = status
                        warnings.append(self._message(journal_error))
                    return self._result(transaction_id, status, journal, warnings, errors)

    def _history_plan(self):
        backup_root = self.backup_dir or self.root / ".htmod/backups"
        return build_history_removal_plan(self.release, self.root, backup_root)

    @staticmethod
    def _history_result_operations(plan):
        return tuple(AppliedOperation(
            change.destination,
            change.action,
            tuple(change.component_ids),
            change.current_sha256 is not None,
            change.current_sha256,
            change.original_sha256,
            None,
            change.status,
        ) for change in plan.history_changes)

    def _remove_all(self, dry_run=False):
        plan = self._history_plan()
        if dry_run:
            return plan
        transaction_id = uuid.uuid4().hex
        if plan.no_op:
            with _transaction_lock(self.root, transaction_id):
                locked = self._history_plan()
                if locked != plan:
                    raise PatchError("no-op history removal plan changed while acquiring the game lock")
                status = "partial" if locked.partial else "no_op"
                return Result(
                    transaction_id,
                    status,
                    True,
                    self._history_result_operations(locked),
                )

        with _transaction_lock(self.root, transaction_id):
            locked = self._history_plan()
            if locked != plan:
                raise PatchError("history removal plan changed while acquiring the game lock")
            operations = []
            for index, change in enumerate(plan.history_changes):
                prepared = change.status == "prepared"
                operations.append({
                    "destination": change.destination,
                    "action": change.action,
                    "component_ids": list(change.component_ids),
                    "existed": change.current_sha256 is not None,
                    "before_sha256": (
                        change.installed_sha256 if prepared else change.current_sha256
                    ),
                    "after_sha256": change.original_sha256,
                    "backup_path": (
                        f"files/{index:04d}/{change.destination}" if prepared else None
                    ),
                    "status": change.status,
                })
            journal = {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "created_ns": time.time_ns(),
                "kind": "remove",
                "mode": "backup_history",
                "status": "prepared",
                "component_ids": [],
                "operations": operations,
            }
            generation = None
            installed = []
            warnings = []
            errors = []
            try:
                generation = self._generation(transaction_id)
                journal_path = generation / "journal.json"
                _write_journal(journal_path, journal)
                journal["status"] = "in_progress"
                _write_journal(journal_path, journal)

                for operation, change in zip(operations, plan.history_changes):
                    if operation["status"] != "prepared":
                        continue
                    destination = operation["destination"]
                    current = _read_regular(self.root, destination)
                    if current is None or hashlib.sha256(current).hexdigest() != operation["before_sha256"]:
                        raise PatchError(f"destination '{destination}' changed before history removal")
                    _write_backup(generation, operation, current)
                    original = None
                    if change.original_existed:
                        source_operation = {"backup_path": change.source.backup_path}
                        original = _read_backup(change.source.generation, source_operation)
                        if hashlib.sha256(original).hexdigest() != change.original_sha256:
                            raise PatchError(f"backup SHA-256 mismatch for '{destination}'")
                    path = _validate_destination(self.root, destination, create=True)
                    root_fd = None
                    parent_fd = None
                    staged = None
                    try:
                        if _supports_directory_fd():
                            root_fd = _open_directory(self.root)
                            parent_fd = _open_relative_directory(
                                root_fd,
                                self.root,
                                Path(destination).parent,
                                create=True,
                            )
                            _verify_parent_descriptor(path.parent, parent_fd)
                            if _read_at(parent_fd, path.name) != current:
                                raise PatchError(
                                    f"destination '{destination}' changed before history removal"
                                )
                        installed.append(operation)
                        operation["status"] = "in_progress"
                        _write_journal(journal_path, journal)
                        if operation["action"] == "delete":
                            if parent_fd is None:
                                _validate_destination(self.root, destination)
                                if path.read_bytes() != current:
                                    raise PatchError(
                                        f"destination '{destination}' changed before history removal"
                                    )
                                path.unlink()
                            else:
                                _verify_parent_descriptor(path.parent, parent_fd)
                                if _read_at(parent_fd, path.name) != current:
                                    raise PatchError(
                                        f"destination '{destination}' changed before history removal"
                                    )
                                os.unlink(path.name, dir_fd=parent_fd)
                            _fsync_directory(path.parent)
                            if _read_regular(self.root, destination) is not None:
                                raise PatchError(f"post-removal verification failed for '{destination}'")
                            operation["status"] = "removed"
                        else:
                            staged = _temporary_destination(path, original, parent_fd=parent_fd)
                            _replace_destination(
                                staged,
                                path,
                                root=self.root,
                                relative=destination,
                                expected=current,
                                parent_fd=parent_fd,
                            )
                            operation["status"] = "restored"
                            _verify_write(path, operation["after_sha256"])
                        _write_journal(journal_path, journal)
                    finally:
                        if staged is not None:
                            _discard_temporary(staged, parent_fd)
                        if parent_fd is not None:
                            os.close(parent_fd)
                        if root_fd is not None:
                            os.close(root_fd)

                expected_final = {}
                for change in plan.history_changes:
                    if change.status == "prepared":
                        expected_final[change.destination] = change.original_sha256
                    elif change.destination not in expected_final:
                        expected_final[change.destination] = change.current_sha256
                for destination, expected_sha256 in expected_final.items():
                    current = _read_regular(self.root, destination)
                    actual_sha256 = None if current is None else hashlib.sha256(current).hexdigest()
                    if actual_sha256 != expected_sha256:
                        raise PatchError(
                            f"post-removal backup history does not match '{destination}'"
                        )
                journal["status"] = "partial" if plan.partial else "removed"
                _write_journal(journal_path, journal)
                return self._result(transaction_id, journal["status"], journal)
            except Exception as error:
                errors.append(self._message(error))
                rollback_failed = False
                for operation in reversed(installed):
                    destination = operation["destination"]
                    try:
                        _restore_operation(self.root, destination, operation, generation)
                        operation["status"] = "rolled_back"
                    except Exception as rollback_error:
                        rollback_failed = True
                        operation["status"] = "rollback_failed"
                        warnings.append(self._message(rollback_error))
                    try:
                        _write_journal(journal_path, journal)
                    except Exception as journal_error:
                        rollback_failed = True
                        warnings.append(self._message(journal_error))
                status = "rollback_failed" if rollback_failed else "rolled_back"
                journal["status"] = status
                try:
                    _write_journal(journal_path, journal)
                except Exception as journal_error:
                    status = "partial"
                    journal["status"] = status
                    warnings.append(self._message(journal_error))
                return self._result(transaction_id, status, journal, warnings, errors)

    def _allowed_destinations(self):
        return {
            record.destination
            for mod in self.release.mods.values() if mod.kind == "data"
            for record in mod.files
        } | set(self.release.pristine_sha256)

    def _recovery_candidate(self, transaction_id, allowed):
        backup_root = self.backup_dir or self.root / ".htmod/backups"
        try:
            root_metadata = backup_root.lstat()
        except FileNotFoundError:
            if transaction_id is None:
                return None
            raise PatchError(f"recovery transaction '{transaction_id}' was not found")
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise PatchError("recovery backup root is unsafe or not a directory")
        resolved_root = backup_root.resolve(strict=True)
        if transaction_id is not None:
            if (
                not isinstance(transaction_id, str)
                or not transaction_id
                or transaction_id in {".", ".."}
                or Path(transaction_id).name != transaction_id
            ):
                raise PatchError("invalid recovery generation name")
            generation = backup_root / transaction_id
            try:
                metadata = generation.lstat()
            except FileNotFoundError as error:
                raise PatchError(f"recovery transaction '{transaction_id}' was not found") from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise PatchError("recovery generation is unsafe or not a directory")
            resolved_generation = generation.resolve(strict=True)
            if resolved_generation.parent != resolved_root:
                raise PatchError("recovery generation violates backup-root containment")
            journal_path = generation / "journal.json"
            try:
                journal_metadata = journal_path.lstat()
            except FileNotFoundError as error:
                raise PatchError(f"recovery transaction '{transaction_id}' was not found") from error
            if stat.S_ISLNK(journal_metadata.st_mode) or not stat.S_ISREG(journal_metadata.st_mode):
                raise PatchError("recovery journal is unsafe or not a regular file")
            journal = load_recovery_journal(generation, allowed)
            return generation, journal
        candidates = []
        for generation in backup_root.iterdir():
            metadata = generation.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                continue
            if generation.resolve(strict=True).parent != resolved_root:
                raise PatchError("recovery generation violates backup-root containment")
            journal_path = generation / "journal.json"
            if not journal_path.is_file() or journal_path.is_symlink():
                continue
            journal = load_recovery_journal(generation, allowed)
            candidates.append((creation_sequence(journal), generation.name, generation, journal))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, generation, journal in candidates:
            if journal["status"] in {"prepared", "in_progress", "partial", "rollback_failed"}:
                return generation, journal
        return None

    def recover(self, transaction_id=None):
        allowed = self._allowed_destinations()
        candidate = self._recovery_candidate(transaction_id, allowed)
        if candidate is None:
            return Result(transaction_id or "", "no_op", True)
        generation, journal = candidate
        transaction_id = generation.name
        if journal["status"] not in {"prepared", "in_progress", "partial", "rollback_failed"}:
            return Result(transaction_id, "no_op", True)
        recovery_lock_id = f"recover-{uuid.uuid4().hex}"
        with _transaction_lock(self.root, recovery_lock_id):
            locked = load_recovery_journal(generation, allowed)
            if locked != journal:
                raise PatchError("recovery journal changed while acquiring the game lock")
            actions = []
            virtual = {}
            blocked = set()
            try:
                for operation in reversed(journal["operations"]):
                    if operation["status"] in {
                        "rolled_back", "recovered", "missing", "skipped_modified", "skipped_blocked",
                    }:
                        actions.append((operation, "skip"))
                        continue
                    destination = operation["destination"]
                    if destination in blocked:
                        actions.append((operation, "modified"))
                        continue
                    if destination not in virtual:
                        current = _read_regular(self.root, destination)
                        virtual[destination] = (
                            None if current is None else hashlib.sha256(current).hexdigest()
                        )
                    current_sha256 = virtual[destination]
                    if current_sha256 == operation["before_sha256"]:
                        actions.append((operation, "already_restored"))
                        continue
                    if current_sha256 != operation["after_sha256"]:
                        actions.append((operation, "modified"))
                        blocked.add(destination)
                        continue
                    if operation["existed"]:
                        backup = _read_backup(generation, operation)
                        if hashlib.sha256(backup).hexdigest() != operation["before_sha256"]:
                            raise PatchError(f"backup SHA-256 mismatch for '{destination}'")
                    actions.append((operation, "restore"))
                    virtual[destination] = operation["before_sha256"]
            except Exception as error:
                result = self._result(
                    transaction_id,
                    "partial",
                    journal,
                    errors=(self._message(error),),
                )
                return Result(
                    transaction_id, "partial", True, result.operations, result.warnings, result.errors,
                )

            warnings = []
            errors = []
            mutated = 0
            failed = False
            try:
                journal["status"] = "in_progress"
                _write_journal(generation / "journal.json", journal)
                for operation, action in actions:
                    destination = operation["destination"]
                    if action == "skip":
                        continue
                    if action == "modified":
                        operation["status"] = "rollback_failed"
                        failed = True
                        warnings.append(
                            f"destination '{destination}' was modified after install; refusing recovery"
                        )
                    elif action == "already_restored":
                        operation["status"] = "recovered"
                    else:
                        try:
                            _restore_operation(self.root, destination, operation, generation)
                            operation["status"] = "recovered"
                            mutated += 1
                        except Exception as error:
                            operation["status"] = "rollback_failed"
                            failed = True
                            warnings.append(self._message(error))
                    _write_journal(generation / "journal.json", journal)
                journal["status"] = "partial" if failed else "recovered"
                _write_journal(generation / "journal.json", journal)
            except Exception as error:
                failed = True
                errors.append(self._message(error))
                journal["status"] = "partial"
                try:
                    _write_journal(generation / "journal.json", journal)
                except Exception as journal_error:
                    errors.append(self._message(journal_error))
            status = "partial" if failed else ("recovered" if mutated else "no_op")
            result = self._result(transaction_id, status, journal, warnings, errors)
            return Result(
                transaction_id,
                status,
                mutated == 0,
                result.operations,
                result.warnings,
                result.errors,
            )
