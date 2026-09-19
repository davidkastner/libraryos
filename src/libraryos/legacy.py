"""Read-only audit support for the legacy evidence-bundle schema."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .library import initialize_library, sha256_file, utc_now, validate_library
from .schemas import validate_record
from .storage import StorageError, atomic_json, resolve_library_path, safe_library_root

LEGACY_INDEX_TABLES = (
    "works",
    "occurrences",
    "sources",
    "artifacts",
    "attempts",
    "exceptions",
)
LEGACY_NAMESPACE = uuid.UUID("c284343b-22fd-50e4-9e13-9b3a87d317cb")


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StorageError(
            f"Could not read legacy JSON record: {error}",
            code="legacy_record_invalid",
            path=str(path),
        ) from error
    if not isinstance(value, dict):
        raise StorageError(
            "Legacy record must be a JSON object",
            code="legacy_record_invalid",
            path=str(path),
        )
    return value


def _open_legacy_root(path: str | Path) -> tuple[Path, dict[str, Any]]:
    root = safe_library_root(path)
    descriptor_path = root / "library.json"
    if not descriptor_path.is_file():
        raise StorageError(
            "Legacy library lacks library.json",
            code="library_descriptor_missing",
            path=str(descriptor_path),
        )
    descriptor = _read_object(descriptor_path)
    if descriptor.get("schema_version") != 1 or "schema" in descriptor:
        raise StorageError(
            "Directory is not a supported legacy library",
            code="legacy_schema_unsupported",
            path=str(descriptor_path),
        )
    return root, descriptor


def _inventory(root: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    path = root / "inventory.json"
    if not path.is_file():
        return None, []
    record = _read_object(path)
    occurrences = record.get("occurrences", [])
    if not isinstance(occurrences, list):
        raise StorageError(
            "Legacy inventory occurrences must be an array",
            code="legacy_inventory_invalid",
            path=str(path),
        )
    return record, occurrences


def _index_report(root: Path) -> dict[str, Any]:
    path = root / "index.sqlite"
    if not path.is_file():
        return {"present": False, "rows": {}, "quick_check": None, "foreign_key_violations": None}

    # Even a SQLite ``mode=ro`` connection can update or create WAL shared-memory
    # state beside the database. Audit an exact temporary snapshot so the legacy
    # library remains byte-for-byte and metadata-for-metadata untouched.
    try:
        with tempfile.TemporaryDirectory(prefix="libraryos-legacy-audit-") as temporary:
            snapshot = Path(temporary) / path.name
            shutil.copyfile(path, snapshot)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{path}{suffix}")
                if sidecar.is_file():
                    shutil.copyfile(sidecar, Path(f"{snapshot}{suffix}"))
            uri = f"{snapshot.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            try:
                rows = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in LEGACY_INDEX_TABLES
                }
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
                foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()
    except sqlite3.Error as error:
        raise StorageError(
            f"Could not read legacy index: {error}",
            code="legacy_index_invalid",
            path=str(path),
        ) from error
    return {
        "present": True,
        "schema_version": schema_version,
        "rows": rows,
        "quick_check": quick_check,
        "foreign_key_violations": foreign_keys,
    }


def audit_legacy_library(path: str | Path, *, verify_hashes: bool = True) -> dict[str, Any]:
    """Audit a legacy library without mutating files or opening SQLite for write."""

    root, descriptor = _open_legacy_root(path)
    findings: list[dict[str, Any]] = []
    manifests = 0
    files_checked = 0
    work_dois: set[str] = set()
    expected_sources = 0
    expected_artifacts = 0
    expected_attempts = 0
    exception_keys: set[str] = set()
    roles: dict[str, int] = {}

    for manifest_path in sorted((root / "works").glob("*/manifest.json")):
        manifests += 1
        try:
            manifest = _read_object(manifest_path)
            if manifest.get("schema_version") != 1:
                raise StorageError(
                    "Unsupported legacy work schema",
                    code="legacy_work_schema_unsupported",
                    path=str(manifest_path),
                )
            doi = manifest.get("work", {}).get("doi")
            if isinstance(doi, str) and doi:
                work_dois.add(doi.lower())
            else:
                findings.append(
                    {
                        "code": "legacy_work_identifier_missing",
                        "path": str(manifest_path),
                    }
                )
            expected_attempts += len(manifest.get("attempts", []))
            exception_keys.update(
                item["key"]
                for item in manifest.get("exceptions", [])
                if isinstance(item, dict) and item.get("key")
            )
            for record in manifest.get("files", []):
                category = record.get("category")
                if category == "source":
                    expected_sources += 1
                elif category == "artifact":
                    expected_artifacts += 1
                else:
                    findings.append(
                        {
                            "code": "legacy_file_category_unknown",
                            "path": str(manifest_path),
                            "value": category,
                        }
                    )
                role = str(record.get("role"))
                roles[role] = roles.get(role, 0) + 1
                try:
                    target = resolve_library_path(
                        manifest_path.parent,
                        record["path"],
                        must_exist=True,
                    )
                except (KeyError, OSError, StorageError) as error:
                    findings.append(
                        {
                            "code": getattr(error, "code", "legacy_file_missing"),
                            "path": str(manifest_path),
                            "message": str(error),
                        }
                    )
                    continue
                if not target.is_file():
                    findings.append({"code": "legacy_file_not_regular", "path": str(target)})
                    continue
                files_checked += 1
                if verify_hashes:
                    if target.stat().st_size != record.get("bytes") or sha256_file(target) != record.get("sha256"):
                        findings.append(
                            {
                                "code": "legacy_file_hash_or_size_mismatch",
                                "path": str(target),
                            }
                        )
        except StorageError as error:
            findings.append(
                {
                    "code": error.code,
                    "path": error.path or str(manifest_path),
                    "message": str(error),
                }
            )

    inventory, occurrences = _inventory(root)
    inventory_dois = {
        item["normalized_doi"].lower()
        for item in occurrences
        if isinstance(item, dict) and isinstance(item.get("normalized_doi"), str)
    }
    for item in occurrences:
        if isinstance(item, dict) and item.get("normalization_error"):
            identity = f"{item.get('source_path')}:{item.get('reference_id')}"
            exception_keys.add(
                "invalid-citation-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            )

    expected_rows = {
        "works": len(work_dois | inventory_dois),
        "occurrences": len(occurrences),
        "sources": expected_sources,
        "artifacts": expected_artifacts,
        "attempts": expected_attempts,
        "exceptions": len(exception_keys),
    }
    index = _index_report(root)
    if index["present"]:
        for table, expected in expected_rows.items():
            actual = index["rows"].get(table)
            if actual != expected:
                findings.append(
                    {
                        "code": "legacy_index_parity_mismatch",
                        "table": table,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        if index["quick_check"] != "ok":
            findings.append(
                {
                    "code": "legacy_index_integrity_failed",
                    "message": index["quick_check"],
                }
            )
        if index["foreign_key_violations"]:
            findings.append(
                {
                    "code": "legacy_index_foreign_key_violation",
                    "count": index["foreign_key_violations"],
                }
            )

    return {
        "library": str(root),
        "legacy_schema_version": descriptor["schema_version"],
        "inventory": {
            "present": inventory is not None,
            "entries": len(inventory.get("entries", [])) if inventory else 0,
            "occurrences": len(occurrences),
            "sha256": sha256_file(root / "inventory.json") if inventory else None,
        },
        "manifests": manifests,
        "manifest_files_checked": files_checked,
        "expected_index_rows": expected_rows,
        "index": index,
        "roles": dict(sorted(roles.items())),
        "findings": findings,
        "valid": not findings,
        "verified_hashes": verify_hashes,
        "mutation": False,
        "scientific_inspection": "not_assessed",
    }


def _legacy_summary(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    work = manifest.get("work", {})
    return {
        "legacy_key": path.parent.name,
        "doi": work.get("doi"),
        "pmid": work.get("pmid"),
        "pmcid": work.get("pmcid"),
        "title": work.get("title"),
        "authors": work.get("authors", []),
        "year": work.get("year"),
        "container_title": work.get("journal"),
        "status": manifest.get("status", {}),
        "sources": sum(
            item.get("category") == "source" for item in manifest.get("files", [])
        ),
        "derivatives": sum(
            item.get("category") == "artifact" for item in manifest.get("files", [])
        ),
        "scientific_inspection": "not_assessed",
    }


def list_legacy_works(
    path: str | Path, *, query: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """List or filter legacy work metadata without opening its SQLite index."""

    root, _ = _open_legacy_root(path)
    needle = query.casefold() if query else None
    results = []
    for manifest_path in sorted((root / "works").glob("*/manifest.json")):
        manifest = _read_object(manifest_path)
        summary = _legacy_summary(manifest_path, manifest)
        if needle is not None:
            searchable = " ".join(
                str(value)
                for value in (
                    summary["doi"],
                    summary["pmid"],
                    summary["pmcid"],
                    summary["title"],
                    summary["container_title"],
                    *summary["authors"],
                )
                if value is not None
            ).casefold()
            if needle not in searchable:
                continue
        results.append(summary)
        if limit is not None and len(results) >= limit:
            break
    return results


def show_legacy_work(path: str | Path, identifier: str) -> dict[str, Any]:
    """Return one legacy manifest by bundle key, DOI, PMID, or PMCID."""

    root, _ = _open_legacy_root(path)
    direct = root / "works" / identifier / "manifest.json"
    if direct.is_file():
        return _read_object(direct)
    needle = identifier.casefold()
    for manifest_path in sorted((root / "works").glob("*/manifest.json")):
        manifest = _read_object(manifest_path)
        work = manifest.get("work", {})
        candidates = (work.get("doi"), work.get("pmid"), work.get("pmcid"))
        if any(isinstance(value, str) and value.casefold() == needle for value in candidates):
            return manifest
    raise StorageError(
        "Legacy work does not exist",
        code="legacy_work_not_found",
        path=identifier,
    )


def preview_legacy_migration(path: str | Path) -> dict[str, Any]:
    """Preview legacy-to-v1 semantic mapping without writing any records."""

    root, _ = _open_legacy_root(path)
    counts = {
        "works": 0,
        "sources": 0,
        "derivatives": 0,
        "attempts": 0,
        "exceptions": 0,
        "prepared_with_nonacquired_terminal_status": 0,
    }
    terminal_statuses: dict[str, int] = {}
    issues: list[dict[str, Any]] = []
    for manifest_path in sorted((root / "works").glob("*/manifest.json")):
        manifest = _read_object(manifest_path)
        counts["works"] += 1
        files = manifest.get("files", [])
        sources = [item for item in files if item.get("category") == "source"]
        derivatives = [item for item in files if item.get("category") == "artifact"]
        counts["sources"] += len(sources)
        counts["derivatives"] += len(derivatives)
        counts["attempts"] += len(manifest.get("attempts", []))
        counts["exceptions"] += len(manifest.get("exceptions", []))
        status = manifest.get("status", {})
        terminal = status.get("acquisition", "unknown")
        terminal_statuses[terminal] = terminal_statuses.get(terminal, 0) + 1
        if status.get("conversion") == "prepared" and terminal != "acquired":
            counts["prepared_with_nonacquired_terminal_status"] += 1
            if not sources:
                issues.append(
                    {
                        "code": "prepared_without_source",
                        "legacy_key": manifest_path.parent.name,
                    }
                )
        source_hashes = {item.get("sha256") for item in sources}
        for derivative in derivatives:
            input_hash = derivative.get("source_sha256")
            if input_hash and input_hash not in source_hashes:
                issues.append(
                    {
                        "code": "derivative_input_missing",
                        "legacy_key": manifest_path.parent.name,
                        "path": derivative.get("path"),
                    }
                )
    return {
        "library": str(root),
        "mode": "preview",
        "applied": False,
        "mutation": False,
        "counts": counts,
        "legacy_terminal_acquisition": dict(sorted(terminal_statuses.items())),
        "mapping": {
            "source_availability": "derived_from_registered_source_records",
            "terminal_acquisition_status": "preserved_as_legacy_provenance",
            "attempts_and_exceptions": "preserved_as_history",
            "scientific_inspection": "not_migrated_without_hash_bound_review",
            "source_bytes": "not_moved_or_rewritten",
        },
        "issues": issues,
        "ready": not issues,
        "scientific_inspection": "not_assessed",
    }


def _legacy_work_id(doi: str) -> str:
    return str(uuid.uuid5(LEGACY_NAMESPACE, f"work:doi:{doi.casefold()}"))


def _legacy_artifact_id(
    legacy_key: str,
    category: str,
    relative_path: str,
) -> str:
    return str(
        uuid.uuid5(
            LEGACY_NAMESPACE,
            f"artifact:{legacy_key}:{category}:{relative_path}",
        )
    )


def _copy_legacy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.clonefile(source, destination)
    except (AttributeError, OSError):
        shutil.copy2(source, destination)


def _legacy_identifiers(work: dict[str, Any]) -> list[dict[str, str]]:
    identifiers = []
    for scheme in ("doi", "pmid", "pmcid"):
        value = work.get(scheme)
        if isinstance(value, str) and value:
            identifiers.append(
                {
                    "scheme": scheme,
                    "value": value,
                    "normalized": value.casefold() if scheme == "doi" else value,
                    "status": "verified",
                }
            )
    return identifiers


def _migrated_source(
    legacy_key: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": _legacy_artifact_id(legacy_key, "source", record["path"]),
        "path": record["path"],
        "sha256": record["sha256"],
        "bytes": record["bytes"],
        "media_type": record.get("media_type")
        or mimetypes.guess_type(record["path"])[0]
        or "application/octet-stream",
        "role": record.get("role") or "source",
        "version": record.get("version", "unknown"),
        "access": record.get("access", "unknown"),
        "license": record.get("license"),
        "license_source": record.get("license_source"),
        "provider": record.get("provider"),
        "canonical_url": record.get("canonical_url"),
        "original_filename": record.get("original_filename"),
        "retrieved_at": record.get("created_at"),
        "identity_status": record.get("identity_status", "unverified"),
        "identity_method": record.get("identity_method"),
        "warnings": [],
        "extensions": {
            "org.libraryos.legacy": {
                key: value
                for key, value in record.items()
                if key
                not in {
                    "access",
                    "bytes",
                    "canonical_url",
                    "category",
                    "created_at",
                    "identity_method",
                    "identity_status",
                    "license",
                    "license_source",
                    "media_type",
                    "original_filename",
                    "path",
                    "provider",
                    "role",
                    "sha256",
                    "version",
                }
            }
        },
    }


def _migrated_derivative(
    legacy_key: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    locators = [
        {
            "artifact_id": _legacy_artifact_id(
                legacy_key,
                "source",
                next(
                    item["path"]
                    for item in record["_sources"]
                    if item["sha256"] == record["source_sha256"]
                ),
            ),
            "type": "page",
            "value": page,
        }
        for page in record.get("source_pages", [])
    ]
    media_type = record.get("media_type")
    guessed = mimetypes.guess_type(record["path"])[0]
    if not media_type or media_type == "application/octet-stream":
        media_type = guessed or "application/octet-stream"
    return {
        "id": _legacy_artifact_id(legacy_key, "artifact", record["path"]),
        "path": record["path"],
        "sha256": record["sha256"],
        "bytes": record["bytes"],
        "media_type": media_type,
        "role": record.get("role") or "derivative",
        "input_sha256": [record["source_sha256"]],
        "generator": {
            "name": record.get("generator") or "legacy-unknown",
            "version": record.get("generator_version") or "unknown",
        },
        "generated_at": record.get("created_at") or utc_now(),
        "quality": (
            "partial"
            if record.get("quality_state") in {"partial", "failed"}
            else "ready"
        ),
        "locators": locators,
        "warnings": ["migrated_generated_not_inspected"],
        "extensions": {
            "org.libraryos.legacy": {
                key: value
                for key, value in record.items()
                if key
                not in {
                    "_sources",
                    "bytes",
                    "category",
                    "created_at",
                    "generator",
                    "generator_version",
                    "media_type",
                    "path",
                    "quality_state",
                    "role",
                    "sha256",
                    "source_pages",
                    "source_sha256",
                }
            }
        },
    }


def rehearse_legacy_migration(
    path: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    """Copy and translate a legacy library into a new disposable v1 library."""

    source_root, descriptor = _open_legacy_root(path)
    destination_root = safe_library_root(destination, must_exist=False)
    if destination_root == source_root or source_root in destination_root.parents:
        raise StorageError(
            "Migration destination must be separate from the legacy library",
            code="legacy_migration_destination_unsafe",
            path=str(destination_root),
        )
    if destination_root.exists() and (
        not destination_root.is_dir() or any(destination_root.iterdir())
    ):
        raise StorageError(
            "Migration destination must not exist or must be empty",
            code="legacy_migration_destination_not_empty",
            path=str(destination_root),
        )
    before = audit_legacy_library(source_root)
    preview = preview_legacy_migration(source_root)
    if not before["valid"] or not preview["ready"]:
        raise StorageError(
            "Legacy library must pass audit and migration preview",
            code="legacy_migration_not_ready",
            path=str(source_root),
        )
    try:
        initialize_library(
            destination_root,
            title=descriptor.get("title") or "Migrated research library",
            description="Disposable LibraryOS migration rehearsal.",
        )
        doi_to_work: dict[str, str] = {}
        migrated_hashes: list[str] = []
        for manifest_path in sorted((source_root / "works").glob("*/manifest.json")):
            legacy = _read_object(manifest_path)
            legacy_key = manifest_path.parent.name
            legacy_work = legacy["work"]
            doi = legacy_work["doi"].casefold()
            work_id = _legacy_work_id(doi)
            doi_to_work[doi] = work_id
            bundle = destination_root / "works" / work_id
            bundle.mkdir(mode=0o700)
            sources = [
                _migrated_source(legacy_key, item)
                for item in legacy.get("files", [])
                if item.get("category") == "source"
            ]
            source_records = [
                item
                for item in legacy.get("files", [])
                if item.get("category") == "source"
            ]
            derivatives = []
            for item in legacy.get("files", []):
                if item.get("category") != "artifact":
                    continue
                derivatives.append(
                    _migrated_derivative(
                        legacy_key,
                        {**item, "_sources": source_records},
                    )
                )
            for item in legacy.get("files", []):
                source_file = resolve_library_path(
                    manifest_path.parent,
                    item["path"],
                    must_exist=True,
                )
                target = resolve_library_path(bundle, item["path"])
                _copy_legacy_file(source_file, target)
                if sha256_file(target) != item["sha256"]:
                    raise StorageError(
                        "A migrated file does not match its legacy hash",
                        code="legacy_migration_hash_mismatch",
                        path=str(source_file),
                    )
                migrated_hashes.append(item["sha256"])
            now = legacy.get("updated_at") or utc_now()
            work = {
                "schema": "https://libraryos.dev/schemas/work/v1",
                "schema_version": 1,
                "id": work_id,
                "work": {
                    "type": "article",
                    "identifiers": _legacy_identifiers(legacy_work),
                    "title": legacy_work.get("title"),
                    "authors": legacy_work.get("authors", []),
                    "container_title": legacy_work.get("journal"),
                    "issued": legacy_work.get("year"),
                    "metadata_status": (
                        "verified"
                        if legacy.get("status", {}).get("metadata") == "verified"
                        else "unresolved"
                    ),
                    "extensions": {
                        "org.libraryos.legacy": {
                            "metadata": legacy.get("metadata", {}),
                            "source_candidates": legacy.get("source_candidates", []),
                        }
                    },
                },
                "source_candidates": [],
                "sources": sources,
                "derivatives": derivatives,
                "warnings": legacy.get("warnings", []),
                "created_at": now,
                "updated_at": now,
                "extensions": {
                    "org.libraryos.legacy": {
                        "bundle_key": legacy_key,
                        "status": legacy.get("status", {}),
                        "preparation": legacy.get("preparation", {}),
                        "attempts": legacy.get("attempts", []),
                        "exceptions": legacy.get("exceptions", []),
                        "supplements": legacy.get("supplements", []),
                    }
                },
            }
            validate_record(work)
            atomic_json(bundle / "manifest.json", work)

        inventory, occurrences = _inventory(source_root)
        if inventory is not None:
            inventoried_at = inventory.get("generated_at") or utc_now()
            for index, item in enumerate(occurrences):
                doi = item.get("normalized_doi")
                work_reference: str | dict[str, str]
                if isinstance(doi, str) and doi.casefold() in doi_to_work:
                    work_reference = doi_to_work[doi.casefold()]
                else:
                    work_reference = {
                        "scheme": "local",
                        "value": (
                            f"legacy-unresolved:{item.get('source_path')}#"
                            f"{item.get('reference_id', index)}"
                        ),
                        "status": "unverified",
                    }
                external_paths = sorted(
                    {
                        value
                        for values in item.get("object_paths", {}).values()
                        for value in values
                    }
                )
                occurrence = {
                    "schema": "https://libraryos.dev/schemas/occurrence/v1",
                    "schema_version": 1,
                    "id": str(
                        uuid.uuid5(
                            LEGACY_NAMESPACE,
                            f"occurrence:{item.get('source_path')}:{item.get('reference_id')}",
                        )
                    ),
                    "adapter": "org.mechanismatlas.mech",
                    "external_source": {
                        "location": item["source_path"],
                        "sha256": item["source_sha256"],
                        "record_id": str(item.get("entry_id") or item.get("reference_id")),
                    },
                    "work": work_reference,
                    "attachment_class": item.get("attachment_class") or "citation",
                    "external_object_paths": external_paths,
                    "inventoried_at": inventoried_at,
                    "scientific_support": "not_assessed",
                    "extensions": {"org.libraryos.legacy": item},
                }
                if item.get("normalization_error"):
                    occurrence["normalization_error"] = item["normalization_error"]
                validate_record(occurrence)
                atomic_json(
                    destination_root
                    / "records"
                    / "occurrences"
                    / f"{occurrence['id']}.json",
                    occurrence,
                )

        validation = validate_library(destination_root)
        after_hashes = sorted(
            artifact["sha256"]
            for work_path in sorted((destination_root / "works").glob("*/manifest.json"))
            for artifact in (
                *_read_object(work_path).get("sources", []),
                *_read_object(work_path).get("derivatives", []),
            )
        )
        expected_hashes = sorted(migrated_hashes)
        comparison = {
            "works": {
                "legacy": before["manifests"],
                "migrated": validation["counts"]["works"],
            },
            "sources": {
                "legacy": before["expected_index_rows"]["sources"],
                "migrated": validation["counts"]["sources"],
            },
            "derivatives": {
                "legacy": before["expected_index_rows"]["artifacts"],
                "migrated": validation["counts"]["derivatives"],
            },
            "occurrences": {
                "legacy": before["expected_index_rows"]["occurrences"],
                "migrated": validation["counts"]["occurrences"],
            },
            "file_hash_multiset_equal": expected_hashes == after_hashes,
        }
        passed = validation["valid"] and all(
            value["legacy"] == value["migrated"]
            for value in comparison.values()
            if isinstance(value, dict)
        ) and comparison["file_hash_multiset_equal"]
        return {
            "source": str(source_root),
            "destination": str(destination_root),
            "mode": "disposable_rehearsal",
            "source_mutation": False,
            "source_bytes_moved": False,
            "comparison": comparison,
            "validation": validation,
            "passed": passed,
            "scientific_inspection": "not_assessed",
        }
    except Exception:
        if destination_root.is_dir():
            shutil.rmtree(destination_root)
        raise
