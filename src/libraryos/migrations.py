"""Previewable, recoverable transactions for authoritative JSON migrations."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .library import open_library, utc_now, validate_library
from .schemas import SchemaError, validate_record
from .storage import (
    StorageError,
    atomic_bytes,
    atomic_json,
    exclusive_lock,
    read_json_record,
    resolve_library_path,
)

MIGRATION_SCHEMA = "https://libraryos.dev/schemas/migration/v1"


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _migration_target(root: Path, relative: str) -> Path:
    path = resolve_library_path(root, relative, must_exist=True)
    parts = Path(relative).parts
    allowed = (
        parts == ("library.json",)
        or (len(parts) == 3 and parts[0] == "works" and parts[2] == "manifest.json")
        or (len(parts) == 2 and parts[0] == "collections" and path.suffix == ".json")
        or (
            len(parts) == 3
            and parts[:2]
            in {
                ("records", "reviews"),
                ("records", "assessments"),
                ("records", "occurrences"),
            }
            and path.suffix == ".json"
        )
    )
    if not allowed or not path.is_file() or path.is_symlink():
        raise StorageError(
            "Schema migrations may only replace supported authoritative JSON records",
            code="migration_target_invalid",
            path=relative,
        )
    return path


def preview_schema_migration(
    library: str | Path,
    replacements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate proposed record replacements and return an exact, write-free plan."""

    root, _ = open_library(library)
    if not replacements:
        raise StorageError(
            "A schema migration must contain at least one replacement",
            code="migration_empty",
        )
    paths = [item.get("path") for item in replacements]
    if any(not isinstance(path, str) for path in paths) or len(paths) != len(set(paths)):
        raise StorageError(
            "Migration paths must be unique strings",
            code="migration_plan_invalid",
        )
    changes: list[dict[str, Any]] = []
    for replacement in sorted(replacements, key=lambda item: item["path"]):
        if set(replacement) != {"path", "record"} or not isinstance(
            replacement["record"], dict
        ):
            raise StorageError(
                "Each migration replacement requires only path and record",
                code="migration_plan_invalid",
                path=str(replacement.get("path")),
            )
        target = _migration_target(root, replacement["path"])
        before = target.read_bytes()
        before_record = read_json_record(target)
        try:
            validate_record(replacement["record"])
        except SchemaError as error:
            raise StorageError(
                str(error),
                code=error.code,
                path=f"{replacement['path']}{error.path}",
            ) from error
        after = _json_bytes(replacement["record"])
        if before_record == replacement["record"]:
            raise StorageError(
                "Migration replacement does not change the record",
                code="migration_no_change",
                path=replacement["path"],
            )
        changes.append(
            {
                "path": replacement["path"],
                "before_sha256": _sha256(before),
                "after_sha256": _sha256(after),
                "before_schema": before_record["schema"],
                "after_schema": replacement["record"]["schema"],
            }
        )
    digest_payload = json.dumps(changes, sort_keys=True, separators=(",", ":")).encode()
    return {
        "plan_sha256": _sha256(digest_payload),
        "changes": changes,
        "records_changed": [item["path"] for item in changes],
        "applied": False,
        "external_collections_changed": [],
        "source_bytes_changed": [],
    }


def apply_schema_migration(
    library: str | Path,
    replacements: list[dict[str, Any]],
    *,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    """Apply a validated migration transaction with persisted rollback bytes."""

    root, _ = open_library(library)
    lock = root / ".libraryos" / "locks" / "schema-migration.lock"
    with exclusive_lock(lock):
        preview = preview_schema_migration(root, replacements)
        if preview["plan_sha256"] != expected_plan_sha256:
            raise StorageError(
                "Migration plan changed; inspect a new preview",
                code="migration_preview_stale",
            )
        migration_id = str(uuid.uuid4())
        recovery_root = resolve_library_path(root, f"records/migrations/{migration_id}")
        now = utc_now()
        changes = []
        records_by_path = {item["path"]: item["record"] for item in replacements}
        for change in preview["changes"]:
            target = _migration_target(root, change["path"])
            backup_relative = f"records/migrations/{migration_id}/before/{change['path']}"
            backup = resolve_library_path(root, backup_relative)
            atomic_bytes(backup, target.read_bytes())
            changes.append(
                {
                    "path": change["path"],
                    "before_sha256": change["before_sha256"],
                    "after_sha256": change["after_sha256"],
                    "backup_path": backup_relative,
                }
            )
        record = {
            "schema": MIGRATION_SCHEMA,
            "schema_version": 1,
            "id": migration_id,
            "plan_sha256": expected_plan_sha256,
            "status": "applying",
            "created_at": now,
            "updated_at": now,
            "changes": changes,
        }
        record_path = recovery_root / "migration.json"
        atomic_json(record_path, record)
        try:
            for change in changes:
                target = _migration_target(root, change["path"])
                if _sha256(target.read_bytes()) != change["before_sha256"]:
                    raise StorageError(
                        "Migration target changed after preview",
                        code="migration_target_stale",
                        path=change["path"],
                    )
                atomic_json(target, records_by_path[change["path"]])
            validation = validate_library(root)
            if not validation["valid"]:
                raise StorageError(
                    "Migrated library did not pass full validation",
                    code="migration_validation_failed",
                )
        except (OSError, StorageError) as error:
            rollback_failed = False
            for change in reversed(changes):
                try:
                    backup = resolve_library_path(root, change["backup_path"], must_exist=True)
                    atomic_bytes(resolve_library_path(root, change["path"]), backup.read_bytes())
                except (OSError, StorageError):
                    rollback_failed = True
            record = {
                **record,
                "status": "rollback_required" if rollback_failed else "rolled_back",
                "updated_at": utc_now(),
                "error_code": getattr(error, "code", "migration_write_failed"),
            }
            atomic_json(record_path, record)
            raise
        record = {**record, "status": "applied", "updated_at": utc_now()}
        atomic_json(record_path, record)
        return {
            "migration": record,
            "records_changed": preview["records_changed"],
            "source_bytes_changed": [],
            "validation": validation,
        }


def rollback_schema_migration(
    library: str | Path,
    migration_id: str,
) -> dict[str, Any]:
    """Restore an applied migration when no target has changed since application."""

    root, _ = open_library(library)
    record_path = resolve_library_path(
        root, f"records/migrations/{migration_id}/migration.json", must_exist=True
    )
    lock = root / ".libraryos" / "locks" / "schema-migration.lock"
    with exclusive_lock(lock):
        record = read_json_record(record_path)
        if record["status"] != "applied":
            raise StorageError(
                "Only an applied migration can be rolled back explicitly",
                code="migration_not_applied",
                path=migration_id,
            )
        for change in record["changes"]:
            target = _migration_target(root, change["path"])
            if _sha256(target.read_bytes()) != change["after_sha256"]:
                raise StorageError(
                    "A migrated record changed; rollback would overwrite later work",
                    code="migration_rollback_stale",
                    path=change["path"],
                )
        for change in reversed(record["changes"]):
            backup = resolve_library_path(root, change["backup_path"], must_exist=True)
            atomic_bytes(resolve_library_path(root, change["path"]), backup.read_bytes())
        validation = validate_library(root)
        if not validation["valid"]:
            raise StorageError(
                "Rollback completed but restored library validation failed",
                code="migration_rollback_validation_failed",
            )
        record = {**record, "status": "rolled_back", "updated_at": utc_now()}
        atomic_json(record_path, record)
        return {"migration": record, "validation": validation}
