"""Atomic synchronization of occurrences contributed by external documents."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .library import open_library, utc_now
from .schemas import validate_record
from .storage import (
    StorageError,
    atomic_json,
    exclusive_lock,
    read_json_record,
    resolve_library_path,
)
from .works import get_work

DOCUMENT_SCHEMA = "https://libraryos.dev/schemas/external-document/v1"
OCCURRENCE_SCHEMA = "https://libraryos.dev/schemas/occurrence/v1"
_IDENTITY_NAMESPACE = uuid.UUID("841cb7e3-2cbc-4c34-b334-b7e716eba9d8")


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageError(
            f"{field} must be a nonempty string",
            code="external_document_field_invalid",
            path=field,
        )
    return value.strip()


def _document_uuid(collection_id: str, adapter: str, document_id: str) -> str:
    return str(uuid.uuid5(_IDENTITY_NAMESPACE, f"{collection_id}\0{adapter}\0{document_id}"))


def _document_root(root: Path, collection_id: str, adapter: str, document_id: str) -> Path:
    identifier = _document_uuid(collection_id, adapter, document_id)
    return resolve_library_path(root, f"records/external-documents/{identifier}")


def _canonical_request(
    *,
    collection_id: str,
    adapter: str,
    document_id: str,
    location: str,
    source_sha256: str,
    occurrences: list[dict[str, Any]],
    expected_previous_sha256: str | None,
    extensions: dict[str, Any] | None,
) -> dict[str, Any]:
    collection_id = _require_text(collection_id, "collection_id")
    adapter = _require_text(adapter, "adapter")
    document_id = _require_text(document_id, "document_id")
    location = _require_text(location, "location")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise StorageError(
            "source_sha256 must be a lowercase SHA-256 digest",
            code="external_document_hash_invalid",
            path="source_sha256",
        )
    if expected_previous_sha256 is not None and (
        not isinstance(expected_previous_sha256, str)
        or len(expected_previous_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_previous_sha256
        )
    ):
        raise StorageError(
            "expected_previous_sha256 must be null or a lowercase SHA-256 digest",
            code="external_document_hash_invalid",
            path="expected_previous_sha256",
        )
    if not isinstance(occurrences, list):
        raise StorageError(
            "occurrences must be an array",
            code="occurrence_set_invalid",
            path="occurrences",
        )
    if extensions is not None and not isinstance(extensions, dict):
        raise StorageError(
            "extensions must be an object",
            code="external_document_extensions_invalid",
            path="extensions",
        )
    normalized_occurrences: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, dict):
            raise StorageError(
                "Each occurrence must be an object",
                code="occurrence_set_invalid",
                path=f"occurrences.{index}",
            )
        unknown = set(occurrence) - {
            "external_key",
            "work",
            "normalization_error",
            "attachment_class",
            "external_object_paths",
            "extensions",
        }
        if unknown:
            raise StorageError(
                f"Occurrence contains unsupported fields: {sorted(unknown)}",
                code="occurrence_set_invalid",
                path=f"occurrences.{index}",
            )
        external_key = _require_text(occurrence.get("external_key"), "external_key")
        if external_key in seen_keys:
            raise StorageError(
                "external_key must be unique within an external document",
                code="occurrence_external_key_duplicate",
                path=external_key,
            )
        seen_keys.add(external_key)
        work = occurrence.get("work")
        normalization_error = occurrence.get("normalization_error")
        if work is None and not isinstance(normalization_error, str):
            raise StorageError(
                "An unresolved occurrence requires normalization_error",
                code="occurrence_work_unresolved",
                path=external_key,
            )
        object_paths = occurrence.get("external_object_paths", [])
        if (
            not isinstance(object_paths, list)
            or any(not isinstance(value, str) or not value for value in object_paths)
        ):
            raise StorageError(
                "external_object_paths must be an array of nonempty strings",
                code="occurrence_set_invalid",
                path=external_key,
            )
        normalized_occurrences.append(
            {
                "external_key": external_key,
                "work": work,
                **(
                    {"normalization_error": normalization_error}
                    if normalization_error is not None
                    else {}
                ),
                "attachment_class": _require_text(
                    occurrence.get("attachment_class"), "attachment_class"
                ),
                "external_object_paths": sorted(set(object_paths)),
                **(
                    {"extensions": occurrence["extensions"]}
                    if "extensions" in occurrence
                    else {}
                ),
            }
        )
    normalized_occurrences.sort(key=lambda item: item["external_key"])
    return {
        "collection_id": collection_id,
        "adapter": adapter,
        "document_id": document_id,
        "location": location,
        "source_sha256": source_sha256,
        "occurrences": normalized_occurrences,
        "expected_previous_sha256": expected_previous_sha256,
        "extensions": extensions or {},
    }


def _current_document(path: Path) -> dict[str, Any] | None:
    record = path / "document.json"
    return read_json_record(record) if record.is_file() else None


def _occurrences_in(path: Path) -> list[dict[str, Any]]:
    directory = path / "occurrences"
    if not directory.is_dir():
        return []
    return [read_json_record(item) for item in sorted(directory.glob("*.json"))]


def _materialize(
    request: dict[str, Any],
    *,
    inventoried_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document_uuid = _document_uuid(
        request["collection_id"], request["adapter"], request["document_id"]
    )
    source = {
        "location": request["location"],
        "sha256": request["source_sha256"],
        "record_id": request["document_id"],
    }
    records: list[dict[str, Any]] = []
    for item in request["occurrences"]:
        occurrence_id = str(
            uuid.uuid5(uuid.UUID(document_uuid), item["external_key"])
        )
        record = {
            "schema": OCCURRENCE_SCHEMA,
            "schema_version": 1,
            "id": occurrence_id,
            "adapter": request["adapter"],
            "external_key": item["external_key"],
            "external_source": source,
            "work": item["work"],
            "attachment_class": item["attachment_class"],
            "external_object_paths": item["external_object_paths"],
            "inventoried_at": inventoried_at,
            "scientific_support": "not_assessed",
            **(
                {"normalization_error": item["normalization_error"]}
                if "normalization_error" in item
                else {}
            ),
            **({"extensions": item["extensions"]} if "extensions" in item else {}),
        }
        validate_record(record, OCCURRENCE_SCHEMA)
        records.append(record)
    document = {
        "schema": DOCUMENT_SCHEMA,
        "schema_version": 1,
        "id": document_uuid,
        "collection_id": request["collection_id"],
        "adapter": request["adapter"],
        "document_id": request["document_id"],
        "source": {
            "location": request["location"],
            "sha256": request["source_sha256"],
        },
        "occurrence_ids": [item["id"] for item in records],
        "updated_at": inventoried_at,
        **({"extensions": request["extensions"]} if request["extensions"] else {}),
    }
    validate_record(document, DOCUMENT_SCHEMA)
    return document, records


def _preview_token(request: dict[str, Any], current_sha256: str | None) -> str:
    payload = json.dumps(
        {"request": request, "current_sha256": current_sha256},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def sync_external_document_preview(
    library: str | Path,
    *,
    collection_id: str,
    adapter: str,
    document_id: str,
    location: str,
    source_sha256: str,
    occurrences: list[dict[str, Any]],
    expected_previous_sha256: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview replacement of one external document's complete occurrence set."""

    root, _ = open_library(library)
    request = _canonical_request(
        collection_id=collection_id,
        adapter=adapter,
        document_id=document_id,
        location=location,
        source_sha256=source_sha256,
        occurrences=occurrences,
        expected_previous_sha256=expected_previous_sha256,
        extensions=extensions,
    )
    path = _document_root(root, collection_id, adapter, document_id)
    current = _current_document(path)
    current_sha = current["source"]["sha256"] if current else None
    if expected_previous_sha256 != current_sha:
        raise StorageError(
            "External document revision does not match expected_previous_sha256",
            code="external_document_stale",
            path=document_id,
        )
    _, proposed = _materialize(request, inventoried_at="1970-01-01T00:00:00Z")
    existing = {item["external_key"]: item for item in _occurrences_in(path)}
    incoming = {item["external_key"]: item for item in proposed}

    def semantic(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key not in {"id", "inventoried_at", "external_source"}
        }

    additions = sorted(set(incoming) - set(existing))
    removals = sorted(set(existing) - set(incoming))
    updates = sorted(
        key
        for key in set(existing) & set(incoming)
        if semantic(existing[key]) != semantic(incoming[key])
        or existing[key]["external_source"]["location"] != location
        or existing[key]["external_source"]["sha256"] != source_sha256
    )
    unchanged = sorted(set(existing) & set(incoming) - set(updates))
    return {
        "collection_id": collection_id,
        "adapter": adapter,
        "document_id": document_id,
        "current_source_sha256": current_sha,
        "new_source_sha256": source_sha256,
        "additions": additions,
        "removals": removals,
        "updates": updates,
        "unchanged": unchanged,
        "occurrence_count": len(incoming),
        "preview_token": _preview_token(request, current_sha),
        "scientific_support": "not_assessed",
        "scientific_inspection": "not_performed",
    }


def sync_external_document_apply(
    library: str | Path,
    *,
    collection_id: str,
    adapter: str,
    document_id: str,
    location: str,
    source_sha256: str,
    occurrences: list[dict[str, Any]],
    preview_token: str,
    expected_previous_sha256: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically replace one external document and its occurrence set."""

    root, _ = open_library(library)
    request = _canonical_request(
        collection_id=collection_id,
        adapter=adapter,
        document_id=document_id,
        location=location,
        source_sha256=source_sha256,
        occurrences=occurrences,
        expected_previous_sha256=expected_previous_sha256,
        extensions=extensions,
    )
    target = _document_root(root, collection_id, adapter, document_id)
    lock = root / ".libraryos" / "locks" / f"external-document-{target.name}.lock"
    with exclusive_lock(lock):
        current = _current_document(target)
        current_sha = current["source"]["sha256"] if current else None
        if expected_previous_sha256 != current_sha:
            raise StorageError(
                "External document changed after preview",
                code="external_document_stale",
                path=document_id,
            )
        expected_token = _preview_token(request, current_sha)
        if preview_token != expected_token:
            raise StorageError(
                "Preview token does not match this synchronization request",
                code="external_document_preview_mismatch",
                path=document_id,
            )
        now = utc_now()
        document, records = _materialize(request, inventoried_at=now)
        for record in records:
            if isinstance(record["work"], str):
                get_work(root, record["work"])
        parent = target.parent
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
        backup = parent / f".{target.name}.backup"
        try:
            (staging / "occurrences").mkdir()
            atomic_json(staging / "document.json", document)
            for record in records:
                atomic_json(staging / "occurrences" / f"{record['id']}.json", record)
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except BaseException:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {
        "document": document,
        "occurrences": records,
        "removed_occurrence_count": len(
            set(current.get("occurrence_ids", []) if current else [])
            - set(document["occurrence_ids"])
        ),
        "scientific_support": "not_assessed",
        "scientific_inspection": "not_performed",
    }


def query_external_documents(
    library: str | Path,
    *,
    collection_id: str | None = None,
    adapter: str | None = None,
    document_id: str | None = None,
    location: str | None = None,
    source_sha256: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a bounded, deterministic page of external document revisions."""

    root, _ = open_library(library)
    if not 1 <= limit <= 1000 or offset < 0:
        raise StorageError("Invalid query bounds", code="query_bounds_invalid")
    items = []
    for path in sorted((root / "records" / "external-documents").glob("*/document.json")):
        record = read_json_record(path)
        if collection_id is not None and record["collection_id"] != collection_id:
            continue
        if adapter is not None and record["adapter"] != adapter:
            continue
        if document_id is not None and record["document_id"] != document_id:
            continue
        if location is not None and record["source"]["location"] != location:
            continue
        if source_sha256 is not None and record["source"]["sha256"] != source_sha256:
            continue
        items.append(record)
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


def query_occurrences(
    library: str | Path,
    *,
    collection_id: str | None = None,
    adapter: str | None = None,
    document_id: str | None = None,
    work_id: str | None = None,
    external_key: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a bounded occurrence page across legacy and synchronized records."""

    root, _ = open_library(library)
    if not 1 <= limit <= 1000 or offset < 0:
        raise StorageError("Invalid query bounds", code="query_bounds_invalid")
    items = [
        read_json_record(path)
        for path in sorted((root / "records" / "occurrences").glob("*.json"))
    ]
    document_membership: dict[tuple[str, str], str] = {}
    for document_path in sorted(
        (root / "records" / "external-documents").glob("*/document.json")
    ):
        document = read_json_record(document_path)
        document_membership[(document["adapter"], document["document_id"])] = document[
            "collection_id"
        ]
    for path in sorted(
        (root / "records" / "external-documents").glob("*/occurrences/*.json")
    ):
        items.append(read_json_record(path))
    filtered = []
    for record in items:
        if adapter is not None and record["adapter"] != adapter:
            continue
        if document_id is not None and record["external_source"].get("record_id") != document_id:
            continue
        if work_id is not None and record["work"] != work_id:
            continue
        if external_key is not None and record.get("external_key") != external_key:
            continue
        if collection_id is not None:
            key = (record["adapter"], record["external_source"].get("record_id"))
            if document_membership.get(key) != collection_id:
                continue
        filtered.append(record)
    filtered.sort(key=lambda item: (item["adapter"], item["id"]))
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
        "scientific_support": "not_assessed",
    }
