"""Library instance lifecycle and integrity checks."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import SchemaError, validate_record
from .storage import (
    StorageError,
    atomic_json,
    git_ancestor,
    read_json_record,
    resolve_library_path,
    safe_library_root,
)

LIBRARY_SCHEMA = "https://libraryos.dev/schemas/library/v1"
WORK_SCHEMA = "https://libraryos.dev/schemas/work/v1"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def initialize_library(
    path: str | Path,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a private LibraryOS instance outside a Git worktree."""

    root = safe_library_root(path, must_exist=False)
    repository = git_ancestor(root)
    if repository is not None:
        raise StorageError(
            f"Library instances must remain outside Git worktrees; root is inside {repository}",
            code="library_inside_git",
            path=str(root),
        )
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise StorageError(
            "Library root exists and is not empty",
            code="library_root_not_empty",
            path=str(root),
        )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    for relative in (
        "works",
        "records/reviews",
        "records/assessments",
        "records/occurrences",
        "records/external-documents",
        "records/collection-registrations",
        "records/collection-archives",
        "records/migrations",
        "collections",
        "quarantine",
        ".libraryos/cache",
        ".libraryos/jobs",
        ".libraryos/locks",
        ".libraryos/search",
        ".libraryos/trash",
        ".libraryos/trash-ledger",
    ):
        resolve_library_path(root, relative).mkdir(parents=True, exist_ok=True)
    now = utc_now()
    descriptor: dict[str, Any] = {
        "schema": LIBRARY_SCHEMA,
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
        "policies": {
            "retain_unreferenced_metadata": True,
            "trash_retention_days": 30,
        },
    }
    if title is not None:
        descriptor["title"] = title
    if description is not None:
        descriptor["description"] = description
    validate_record(descriptor)
    atomic_json(root / "library.json", descriptor)
    return {
        "id": descriptor["id"],
        "path": str(root),
        "schema": descriptor["schema"],
    }


def open_library(path: str | Path) -> tuple[Path, dict[str, Any]]:
    """Open and validate a LibraryOS instance descriptor."""

    root = safe_library_root(path)
    marker = root / "library.json"
    if not marker.is_file():
        raise StorageError(
            "Directory is not an initialized LibraryOS library",
            code="library_descriptor_missing",
            path=str(marker),
        )
    descriptor = read_json_record(marker)
    if descriptor["schema"] != LIBRARY_SCHEMA:
        raise StorageError(
            "Library descriptor uses an unsupported schema",
            code="library_schema_unsupported",
            path=str(marker),
        )
    return root, descriptor


def _validate_manifest_files(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    bundle = manifest_path.parent
    for artifact in (*manifest.get("sources", []), *manifest.get("derivatives", [])):
        relative = artifact["path"]
        try:
            path = resolve_library_path(bundle, relative, must_exist=True)
        except (OSError, StorageError) as error:
            findings.append(
                {
                    "code": getattr(error, "code", "artifact_missing"),
                    "path": str(manifest_path),
                    "message": f"{relative}: {error}",
                }
            )
            continue
        if not path.is_file():
            findings.append(
                {
                    "code": "artifact_not_file",
                    "path": str(path),
                    "message": "Manifest artifact is not a regular file",
                }
            )
            continue
        actual_size = path.stat().st_size
        if actual_size != artifact["bytes"]:
            findings.append(
                {
                    "code": "artifact_size_mismatch",
                    "path": str(path),
                    "message": f"Expected {artifact['bytes']} bytes; found {actual_size}",
                }
            )
        actual_hash = sha256_file(path)
        if actual_hash != artifact["sha256"]:
            findings.append(
                {
                    "code": "artifact_hash_mismatch",
                    "path": str(path),
                    "message": f"Expected {artifact['sha256']}; found {actual_hash}",
                }
            )
    return findings


def _validate_manifest_integrity(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    artifacts = [*manifest.get("sources", []), *manifest.get("derivatives", [])]
    source_hashes = {source["sha256"] for source in manifest.get("sources", [])}
    for field in ("id", "path"):
        values = [artifact[field] for artifact in artifacts]
        if len(values) != len(set(values)):
            findings.append(
                {
                    "code": f"artifact_{field}_duplicate",
                    "path": str(manifest_path),
                    "message": f"Artifact {field}s must be unique within a work",
                }
            )
    for derivative in manifest.get("derivatives", []):
        missing = set(derivative["input_sha256"]) - source_hashes
        if missing:
            findings.append(
                {
                    "code": "derivative_input_missing",
                    "path": str(manifest_path),
                    "message": f"{derivative['id']} has unavailable inputs: {sorted(missing)}",
                }
            )
    return findings


def validate_library(path: str | Path, *, verify_hashes: bool = True) -> dict[str, Any]:
    """Validate authoritative records and optional artifact hashes."""

    root, descriptor = open_library(path)
    findings: list[dict[str, str]] = []
    record_count = 1
    work_count = 0
    source_count = 0
    derivative_count = 0
    quarantine_count = 0
    occurrence_count = 0
    external_document_count = 0
    collection_count = 0
    identifier_owners: dict[tuple[str, str], tuple[str, str]] = {}

    work_root = root / "works"
    if work_root.is_dir():
        for manifest_path in sorted(work_root.glob("*/manifest.json")):
            record_count += 1
            work_count += 1
            try:
                manifest = read_json_record(manifest_path)
                if manifest["schema"] != WORK_SCHEMA:
                    raise StorageError(
                        "Work manifest uses an unsupported schema",
                        code="work_schema_unsupported",
                        path=str(manifest_path),
                    )
                source_count += len(manifest["sources"])
                derivative_count += len(manifest["derivatives"])
                # Import locally because identity resolution opens libraries,
                # while library opening must remain independent of identity.
                from .identity import identifier_key

                local_identifiers: set[tuple[str, str]] = set()
                for identifier in manifest["work"]["identifiers"]:
                    key = identifier_key(identifier)
                    display = f"{key[0]}:{key[1]}"
                    if key in local_identifiers:
                        findings.append(
                            {
                                "code": "identifier_duplicate",
                                "path": str(manifest_path),
                                "message": f"Identifier is repeated within the work: {display}",
                            }
                        )
                    local_identifiers.add(key)
                    previous = identifier_owners.get(key)
                    if previous is not None and previous[0] != manifest["id"]:
                        findings.append(
                            {
                                "code": "identifier_conflict",
                                "path": str(manifest_path),
                                "message": (
                                    f"Identifier {display} is also assigned to work "
                                    f"{previous[0]} at {previous[1]}"
                                ),
                            }
                        )
                    else:
                        identifier_owners[key] = (manifest["id"], str(manifest_path))
                findings.extend(_validate_manifest_integrity(manifest_path, manifest))
                if verify_hashes:
                    findings.extend(_validate_manifest_files(root, manifest_path, manifest))
            except (KeyError, SchemaError, StorageError) as error:
                findings.append(
                    {
                        "code": getattr(error, "code", "manifest_invalid"),
                        "path": getattr(error, "path", None) or str(manifest_path),
                        "message": str(error),
                    }
                )

    for directory, expected_schema in (
        (root / "records" / "reviews", "https://libraryos.dev/schemas/review/v1"),
        (root / "records" / "assessments", "https://libraryos.dev/schemas/assessment/v1"),
        (root / "records" / "occurrences", "https://libraryos.dev/schemas/occurrence/v1"),
        (
            root / "records" / "external-documents",
            "https://libraryos.dev/schemas/external-document/v1",
        ),
        (
            root / "records" / "collection-registrations",
            "https://libraryos.dev/schemas/collection-registration/v1",
        ),
        (root / "collections", "https://libraryos.dev/schemas/collection/v1"),
        (root / ".libraryos" / "jobs", "https://libraryos.dev/schemas/job/v1"),
    ):
        if not directory.is_dir():
            continue
        record_paths = (
            directory.glob("*/document.json")
            if expected_schema.endswith("/external-document/v1")
            else directory.glob("*.json")
        )
        for record_path in sorted(record_paths):
            record_count += 1
            try:
                record = read_json_record(record_path)
                if record["schema"] != expected_schema:
                    raise StorageError(
                        "Record is stored under the wrong record class",
                        code="record_schema_location_mismatch",
                        path=str(record_path),
                    )
                if expected_schema.endswith("/review/v1"):
                    work_path = root / "works" / record["work_id"] / "manifest.json"
                    if not work_path.is_file():
                        raise StorageError(
                            "Review references an unknown work",
                            code="review_work_missing",
                            path=str(record_path),
                        )
                    work = read_json_record(work_path)
                    source_hashes = {source["sha256"] for source in work["sources"]}
                    if set(record["source_sha256"]) - source_hashes:
                        raise StorageError(
                            "Review references unavailable source bytes",
                            code="review_source_missing",
                            path=str(record_path),
                        )
                if expected_schema.endswith("/assessment/v1"):
                    from .collections import get_collection

                    try:
                        get_collection(root, record["collection_id"])
                    except StorageError as error:
                        raise StorageError(
                            "Assessment references an unknown collection",
                            code="assessment_collection_missing",
                            path=str(record_path),
                        ) from error
                if expected_schema.endswith("/occurrence/v1"):
                    occurrence_count += 1
                if expected_schema.endswith("/external-document/v1"):
                    external_document_count += 1
                    occurrence_paths = sorted(
                        (record_path.parent / "occurrences").glob("*.json")
                    )
                    actual_ids: list[str] = []
                    for occurrence_path in occurrence_paths:
                        occurrence = read_json_record(occurrence_path)
                        if occurrence["schema"] != "https://libraryos.dev/schemas/occurrence/v1":
                            raise StorageError(
                                "External document contains a non-occurrence record",
                                code="record_schema_location_mismatch",
                                path=str(occurrence_path),
                            )
                        actual_ids.append(occurrence["id"])
                        occurrence_count += 1
                    if set(actual_ids) != set(record["occurrence_ids"]):
                        raise StorageError(
                            "External document occurrence IDs do not match its records",
                            code="external_document_occurrences_mismatch",
                            path=str(record_path),
                        )
                if expected_schema.endswith("/collection/v1"):
                    collection_count += 1
                if expected_schema.endswith("/collection-registration/v1"):
                    from .collections import get_collection

                    collection_count += 1
                    get_collection(root, record["id"])
            except (KeyError, SchemaError, StorageError) as error:
                findings.append(
                    {
                        "code": getattr(error, "code", "record_invalid"),
                        "path": getattr(error, "path", None) or str(record_path),
                        "message": str(error),
                    }
                )

    migration_root = root / "records" / "migrations"
    if migration_root.is_dir():
        for record_path in sorted(migration_root.glob("*/migration.json")):
            record_count += 1
            try:
                record = read_json_record(record_path)
                if record["schema"] != "https://libraryos.dev/schemas/migration/v1":
                    raise StorageError(
                        "Record is stored under the wrong record class",
                        code="record_schema_location_mismatch",
                        path=str(record_path),
                    )
                if record["id"] != record_path.parent.name:
                    raise StorageError(
                        "Migration identifier does not match its recovery directory",
                        code="migration_id_location_mismatch",
                        path=str(record_path),
                    )
                for change in record["changes"]:
                    backup = resolve_library_path(
                        root,
                        change["backup_path"],
                        must_exist=True,
                    )
                    if not backup.is_file():
                        raise StorageError(
                            "Migration recovery backup is not a regular file",
                            code="migration_backup_missing",
                            path=str(backup),
                        )
                    if verify_hashes and sha256_file(backup) != change["before_sha256"]:
                        raise StorageError(
                            "Migration recovery backup hash does not match its record",
                            code="migration_backup_hash_mismatch",
                            path=str(backup),
                        )
            except (KeyError, SchemaError, StorageError) as error:
                findings.append(
                    {
                        "code": getattr(error, "code", "record_invalid"),
                        "path": getattr(error, "path", None) or str(record_path),
                        "message": str(error),
                    }
                )

    quarantine_root = root / "quarantine"
    if quarantine_root.is_dir():
        for record_path in sorted(quarantine_root.glob("*/manifest.json")):
            record_count += 1
            quarantine_count += 1
            try:
                record = read_json_record(record_path)
                if record["schema"] != "https://libraryos.dev/schemas/quarantine/v1":
                    raise StorageError(
                        "Record is stored under the wrong record class",
                        code="record_schema_location_mismatch",
                        path=str(record_path),
                    )
                payload = resolve_library_path(
                    record_path.parent,
                    record["path"],
                    must_exist=True,
                )
                if not payload.is_file():
                    raise StorageError(
                        "Quarantine payload is not a regular file",
                        code="quarantine_payload_missing",
                        path=str(payload),
                    )
                if payload.stat().st_size != record["bytes"]:
                    raise StorageError(
                        "Quarantine payload size does not match its record",
                        code="quarantine_size_mismatch",
                        path=str(payload),
                    )
                if verify_hashes and sha256_file(payload) != record["sha256"]:
                    raise StorageError(
                        "Quarantine payload hash does not match its record",
                        code="quarantine_hash_mismatch",
                        path=str(payload),
                    )
            except (KeyError, SchemaError, StorageError) as error:
                findings.append(
                    {
                        "code": getattr(error, "code", "record_invalid"),
                        "path": getattr(error, "path", None) or str(record_path),
                        "message": str(error),
                    }
                )

    return {
        "library": {
            "id": descriptor["id"],
            "path": str(root),
        },
        "counts": {
            "records": record_count,
            "works": work_count,
            "sources": source_count,
            "derivatives": derivative_count,
            "quarantine": quarantine_count,
            "collections": collection_count,
            "occurrences": occurrence_count,
            "external_documents": external_document_count,
            "findings": len(findings),
        },
        "findings": findings,
        "valid": not findings,
        "verified_hashes": verify_hashes,
    }
