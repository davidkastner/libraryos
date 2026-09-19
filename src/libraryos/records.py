"""Collections, reviews, and contextual assessments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .collections import (
    COLLECTION_SCHEMA,
    get_collection,
    list_collections,
    put_external_collection,
)
from .identity import resolve_identifier
from .library import open_library, utc_now
from .schemas import validate_record
from .storage import (
    StorageError,
    atomic_bytes,
    atomic_json,
    exclusive_lock,
    read_json_record,
    resolve_library_path,
)
from .works import get_work, list_works

REVIEW_SCHEMA = "https://libraryos.dev/schemas/review/v1"
ASSESSMENT_SCHEMA = "https://libraryos.dev/schemas/assessment/v1"
OCCURRENCE_SCHEMA = "https://libraryos.dev/schemas/occurrence/v1"


class _UniqueAssessmentKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses ambiguous duplicate mapping keys."""


def _construct_unique_assessment_mapping(
    loader: _UniqueAssessmentKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueAssessmentKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_assessment_mapping,
)


def put_collection(library: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Create or replace a collection after schema and work-reference validation."""

    root, _ = open_library(library)
    validate_record(record, COLLECTION_SCHEMA)
    for member in record["members"]:
        reference = member["work"]
        if isinstance(reference, str):
            get_work(root, reference)
    path = resolve_library_path(root, f"collections/{record['id']}.json")
    atomic_json(path, record)
    return record


def create_collection(
    library: str | Path,
    *,
    collection_id: str,
    title: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create an empty active collection."""

    now = utc_now()
    record: dict[str, Any] = {
        "schema": COLLECTION_SCHEMA,
        "schema_version": 1,
        "id": collection_id,
        "title": title,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "members": [],
    }
    if description is not None:
        record["description"] = description
    return put_collection(library, record)


def write_review(library: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append an immutable review bound to source hashes present on the work."""

    root, _ = open_library(library)
    validate_record(record, REVIEW_SCHEMA)
    work = get_work(root, record["work_id"])
    available = {source["sha256"] for source in work["sources"]}
    artifact_ids = {
        artifact["id"] for artifact in [*work["sources"], *work["derivatives"]]
    }
    missing = set(record["source_sha256"]) - available
    if missing:
        raise StorageError(
            "Review references source hashes absent from the work",
            code="review_source_missing",
            path=record["id"],
        )
    unknown_artifacts = {
        locator["artifact_id"] for locator in record["coverage"]
    } - artifact_ids
    if unknown_artifacts:
        raise StorageError(
            "Review coverage references artifacts absent from the work",
            code="review_artifact_missing",
            path=record["id"],
        )
    path = resolve_library_path(root, f"records/reviews/{record['id']}.json")
    if path.exists():
        raise StorageError(
            "Reviews are immutable; create a superseding review",
            code="immutable_record_exists",
            path=str(path),
        )
    atomic_json(path, record)
    return record


def write_assessment(library: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append an immutable project/collection-scoped assessment."""

    root, _ = open_library(library)
    validate_record(record, ASSESSMENT_SCHEMA)
    try:
        get_collection(root, record["collection_id"])
    except StorageError as error:
        raise StorageError(
            "Assessment collection does not exist",
            code="collection_not_found",
            path=record["collection_id"],
        ) from error
    known_reviews = {
        item["id"] for item in list_records(root, "reviews")
    }
    if set(record.get("review_ids", [])) - known_reviews:
        raise StorageError(
            "Assessment references an unknown review",
            code="assessment_review_missing",
            path=record["id"],
        )
    path = resolve_library_path(root, f"records/assessments/{record['id']}.json")
    if path.exists():
        raise StorageError(
            "Assessments are immutable; create a superseding assessment",
            code="immutable_record_exists",
            path=str(path),
        )
    atomic_json(path, record)
    return record


def import_assessment(
    library: str | Path,
    collection_id: str,
    path: str | Path,
) -> dict[str, Any]:
    """Import a human-authored JSON or YAML assessment into canonical storage."""

    source = Path(path).expanduser().absolute()
    if source.is_symlink():
        raise StorageError(
            "An assessment authoring path must not be a symlink",
            code="assessment_path_symlink",
            path=str(source),
        )
    suffix = source.suffix.casefold()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise StorageError(
            "Assessment files must use .json, .yaml, or .yml",
            code="assessment_format_unsupported",
            path=str(source),
        )
    try:
        text = source.read_text(encoding="utf-8")
        record = (
            json.loads(text)
            if suffix == ".json"
            else yaml.load(text, Loader=_UniqueAssessmentKeyLoader)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise StorageError(
            f"Assessment file could not be read: {error}",
            code="assessment_parse_failed",
            path=str(source),
        ) from error
    if not isinstance(record, dict):
        raise StorageError(
            "Assessment file must contain one mapping/object",
            code="assessment_type_invalid",
            path=str(source),
        )
    if record.get("collection_id") != collection_id:
        raise StorageError(
            "Assessment collection ID does not match the requested collection",
            code="assessment_collection_mismatch",
            path=str(source),
        )
    return write_assessment(library, record)


def write_occurrence(library: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one structural citation occurrence without a support inference."""

    root, _ = open_library(library)
    normalized = {**record, "scientific_support": "not_assessed"}
    validate_record(normalized, OCCURRENCE_SCHEMA)
    reference = normalized["work"]
    if isinstance(reference, str):
        get_work(root, reference)
    path = resolve_library_path(root, f"records/occurrences/{normalized['id']}.json")
    if path.exists():
        existing = read_json_record(path)
        if existing == normalized:
            return existing
        raise StorageError(
            "Occurrences are immutable",
            code="immutable_record_exists",
            path=str(path),
        )
    atomic_json(path, normalized)
    return normalized


def list_records(library: str | Path, kind: str) -> list[dict[str, Any]]:
    """List one authoritative record class."""

    root, _ = open_library(library)
    locations = {
        "collections": root / "collections",
        "reviews": root / "records" / "reviews",
        "assessments": root / "records" / "assessments",
        "occurrences": root / "records" / "occurrences",
    }
    if kind not in locations:
        raise StorageError("Unknown record kind", code="record_kind_invalid", path=kind)
    return [read_json_record(path) for path in sorted(locations[kind].glob("*.json"))]


def collection_queues(library: str | Path, collection_id: str) -> dict[str, Any]:
    """Evaluate collection policy and return non-scientific workflow queues."""

    root, _ = open_library(library)
    resolved = get_collection(root, collection_id)
    collection = resolved["collection"]
    policy = collection.get("policy", {})
    require_source = policy.get("citation_requires_local_source", False)
    required_review_purpose = policy.get("required_review_purpose")
    reviews_by_work: dict[str, list[dict[str, Any]]] = {}
    for review in list_records(root, "reviews"):
        reviews_by_work.setdefault(review["work_id"], []).append(review)

    queues: dict[str, list[dict[str, Any]]] = {
        "needs_source": [],
        "needs_preparation": [],
        "needs_review": [],
        "exceptions": [],
        "ready": [],
    }
    members: list[dict[str, Any]] = []
    for index, member in enumerate(collection["members"]):
        reference = member["work"]
        work = None
        exception_reasons: list[dict[str, Any]] = []
        if isinstance(reference, str):
            work = get_work(root, reference)
        else:
            identifier = reference["identifier"]
            work = resolve_identifier(
                root,
                identifier["scheme"],
                identifier["value"],
                normalized=identifier.get("normalized"),
            )
            if work is None:
                exception_reasons.append(
                    {
                        "code": "work_reference_unresolved",
                        "message": "The member identifier does not resolve to a local work.",
                    }
                )

        work_id = work["id"] if work is not None else None
        sources = work["sources"] if work is not None else []
        source_hashes = {source["sha256"] for source in sources}
        prepared = (
            [
                derivative
                for derivative in work["derivatives"]
                if derivative["role"] == "source_faithful_markdown"
                and derivative["quality"] in {"ready", "partial"}
                and set(derivative["input_sha256"]).issubset(source_hashes)
            ]
            if work is not None
            else []
        )
        matching_reviews = (
            [
                review
                for review in reviews_by_work.get(work_id, [])
                if review["purpose"] == required_review_purpose
                and set(review["source_sha256"]).issubset(source_hashes)
            ]
            if work_id is not None and required_review_purpose is not None
            else []
        )

        reasons: dict[str, list[dict[str, Any]]] = {
            "needs_source": [],
            "needs_preparation": [],
            "needs_review": [],
            "exceptions": exception_reasons,
        }
        if work is not None and not sources:
            reasons["needs_source"].append(
                {
                    "code": "local_source_missing",
                    "message": "No immutable local source is registered for this work.",
                    "policy_violation": require_source,
                }
            )
        if work is not None and sources and not prepared:
            reasons["needs_preparation"].append(
                {
                    "code": "source_faithful_derivative_missing",
                    "message": (
                        "No ready or partial source-faithful text derivative "
                        "is bound to a current source."
                    ),
                    "policy_violation": False,
                }
            )
        if (
            work is not None
            and required_review_purpose is not None
            and not matching_reviews
        ):
            reasons["needs_review"].append(
                {
                    "code": "required_review_missing",
                    "message": (
                        "No source-bound review has the collection's exact "
                        "required purpose."
                    ),
                    "policy_violation": True,
                    "required_purpose": required_review_purpose,
                }
            )

        queue_names = [name for name in reasons if reasons[name]]
        policy_compliant = not any(
            reason.get("policy_violation", False)
            for queue_reasons in reasons.values()
            for reason in queue_reasons
        ) and not exception_reasons
        item = {
            "member_index": index,
            "work_reference": reference,
            "work_id": work_id,
            "title": work["work"]["title"] if work is not None else None,
            "queues": queue_names or ["ready"],
            "reasons": reasons,
            "state": {
                "local_source_available": bool(sources),
                "source_faithful_preparation_available": bool(prepared),
                "required_review_available": (
                    None
                    if required_review_purpose is None
                    else bool(matching_reviews)
                ),
                "policy_compliant": policy_compliant,
                "scientific_support": "not_assessed",
            },
        }
        members.append(item)
        if queue_names:
            for name in queue_names:
                queues[name].append(item)
        else:
            queues["ready"].append(item)

    return {
        "collection_id": collection_id,
        "collection_status": collection["status"],
        "policy": policy,
        "members": members,
        "queues": queues,
        "counts": {name: len(items) for name, items in queues.items()},
        "policy_compliant": all(
            member["state"]["policy_compliant"] for member in members
        ),
        "scientific_assessment": "not_performed",
        "queue_semantics": (
            "Workflow readiness only; no queue asserts relevance, correctness, "
            "citation suitability, or scientific support."
        ),
    }


def archive_collection_preview(library: str | Path, collection_id: str) -> dict[str, Any]:
    """Describe the exact non-destructive effect of archiving a collection."""

    root, _ = open_library(library)
    resolved = get_collection(root, collection_id)
    record = resolved["collection"]
    path = Path(resolved["location"]["path"])
    return {
        "collection_id": collection_id,
        "current_status": record["status"],
        "next_status": "archived",
        "records_changed": [] if record["status"] == "archived" else [str(path)],
        "location_kind": resolved["location"]["kind"],
        "works_deleted": [],
        "sources_deleted": [],
        "bytes_deleted": 0,
    }


def _collection_snapshot_payload(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_collection_snapshot(
    root: Path,
    collection_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    payload = _collection_snapshot_payload(record)
    digest = hashlib.sha256(payload).hexdigest()
    path = resolve_library_path(
        root,
        f"records/collection-archives/{collection_id}/{digest}.json",
    )
    created = not path.exists()
    if created:
        atomic_bytes(path, payload)
    return {
        "sha256": digest,
        "path": str(path.relative_to(root)),
        "created": created,
    }


def set_collection_status(
    library: str | Path,
    collection_id: str,
    *,
    status: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Archive or restore a collection without deleting any referenced state."""

    if status not in {"active", "archived"}:
        raise StorageError(
            "Collection status must be active or archived",
            code="collection_status_invalid",
            path=status,
        )
    root, _ = open_library(library)
    resolved = get_collection(root, collection_id)
    current = resolved["collection"]
    snapshot = _write_collection_snapshot(root, collection_id, current)
    if current["status"] == status:
        return {
            "collection": current,
            "snapshot": snapshot,
            "changed": False,
            "works_deleted": [],
            "sources_deleted": [],
        }
    updated = {**current, "status": status, "updated_at": utc_now()}
    if resolved["location"]["kind"] == "external":
        if expected_sha256 is None:
            raise StorageError(
                "External collection status changes require its current hash",
                code="collection_expected_hash_required",
                path=collection_id,
            )
        result = put_external_collection(
            root,
            collection_id,
            updated,
            expected_sha256=expected_sha256,
        )
        registration = result["registration"]
    else:
        path = resolve_library_path(root, f"collections/{collection_id}.json")
        lock = root / ".libraryos" / "locks" / f"collection-{collection_id}.lock"
        with exclusive_lock(lock):
            latest = read_json_record(path)
            if latest != current:
                raise StorageError(
                    "Collection changed; reload it before changing status",
                    code="collection_conflict",
                    path=collection_id,
                )
            atomic_json(path, updated)
        registration = None
    return {
        "collection": updated,
        "snapshot": snapshot,
        "changed": True,
        "registration": registration,
        "works_deleted": [],
        "sources_deleted": [],
    }


def archive_collection(
    library: str | Path,
    collection_id: str,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Freeze a snapshot and mark a collection archived."""

    return set_collection_status(
        library,
        collection_id,
        status="archived",
        expected_sha256=expected_sha256,
    )


def restore_collection(
    library: str | Path,
    collection_id: str,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Restore an archived collection to active status."""

    return set_collection_status(
        library,
        collection_id,
        status="active",
        expected_sha256=expected_sha256,
    )


def purge_preview(library: str | Path, collection_id: str) -> dict[str, Any]:
    """Preview removal of project state without deleting shared evidence."""

    root, _ = open_library(library)
    collection_path = resolve_library_path(
        root, f"collections/{collection_id}.json", must_exist=True
    )
    assessments = [
        path
        for path in (root / "records" / "assessments").glob("*.json")
        if read_json_record(path)["collection_id"] == collection_id
    ]
    return {
        "collection_id": collection_id,
        "records_deleted": [
            str(path.relative_to(root)) for path in [collection_path, *sorted(assessments)]
        ],
        "works_deleted": [],
        "sources_deleted": [],
        "bytes_deleted": 0,
        "requires_capability": "destructive.purge",
        "applied": False,
    }


def _collection_removal_paths(root: Path, collection_id: str) -> list[Path]:
    collection = get_collection(root, collection_id)
    paths = [
        path
        for path in (root / "records" / "assessments").glob("*.json")
        if read_json_record(path)["collection_id"] == collection_id
    ]
    location = collection["location"]
    if location["kind"] == "library":
        paths.append(Path(location["path"]))
    else:
        paths.append(
            resolve_library_path(
                root,
                f"records/collection-registrations/{collection_id}.json",
                must_exist=True,
            )
        )
    return sorted(paths)


def stage_collection_removal(
    library: str | Path,
    collection_id: str,
    *,
    expected_preview: list[str],
) -> dict[str, Any]:
    """Move collection-owned records to recoverable trash after exact preview."""

    root, descriptor = open_library(library)
    paths = _collection_removal_paths(root, collection_id)
    relative_paths = [str(path.relative_to(root)) for path in paths]
    if relative_paths != expected_preview:
        raise StorageError(
            "Removal preview changed; inspect it again before staging",
            code="purge_preview_stale",
            path=collection_id,
        )
    transaction_id = str(uuid.uuid4())
    transaction_root = resolve_library_path(
        root,
        f".libraryos/trash/{transaction_id}",
    )
    lock = root / ".libraryos" / "locks" / f"collection-{collection_id}.lock"
    with exclusive_lock(lock):
        # Recheck beneath the lock so no record is removed on a stale preview.
        if [str(path.relative_to(root)) for path in _collection_removal_paths(root, collection_id)] != relative_paths:
            raise StorageError(
                "Removal preview changed; inspect it again before staging",
                code="purge_preview_stale",
                path=collection_id,
            )
        now = datetime.now(UTC).replace(microsecond=0)
        retention_days = descriptor.get("policies", {}).get("trash_retention_days", 30)
        moved: list[dict[str, str]] = []
        transaction_root.mkdir(parents=True)
        try:
            for source in paths:
                relative = source.relative_to(root)
                destination = transaction_root / "records" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                moved.append(
                    {
                        "original": str(relative),
                        "trashed": str(destination.relative_to(root)),
                    }
                )
            record = {
                "schema": "https://libraryos.dev/schemas/trash-transaction/v1",
                "schema_version": 1,
                "id": transaction_id,
                "kind": "collection_removal",
                "collection_id": collection_id,
                "state": "staged",
                "moved": moved,
                "staged_at": now.isoformat().replace("+00:00", "Z"),
                "purge_after": (now + timedelta(days=retention_days))
                .isoformat()
                .replace("+00:00", "Z"),
            }
            validate_record(record)
            atomic_json(transaction_root / "transaction.json", record)
        except Exception:
            for item in reversed(moved):
                source = root / item["trashed"]
                destination = root / item["original"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.exists():
                    os.replace(source, destination)
            raise
    return {
        "transaction": record,
        "works_deleted": [],
        "sources_deleted": [],
        "bytes_deleted": 0,
    }


def restore_staged_removal(
    library: str | Path,
    transaction_id: str,
) -> dict[str, Any]:
    """Restore every record in one staged collection-removal transaction."""

    root, _ = open_library(library)
    transaction_root = resolve_library_path(
        root,
        f".libraryos/trash/{transaction_id}",
        must_exist=True,
    )
    transaction_path = transaction_root / "transaction.json"
    record = read_json_record(transaction_path)
    if record["state"] != "staged":
        raise StorageError(
            "Only staged removals can be restored",
            code="trash_restore_invalid",
            path=transaction_id,
        )
    lock = root / ".libraryos" / "locks" / f"trash-{transaction_id}.lock"
    with exclusive_lock(lock):
        for item in record["moved"]:
            destination = root / item["original"]
            if destination.exists():
                raise StorageError(
                    "A restored path is already occupied",
                    code="trash_restore_conflict",
                    path=item["original"],
                )
        restored: list[str] = []
        for item in record["moved"]:
            source = root / item["trashed"]
            destination = root / item["original"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            restored.append(item["original"])
        updated = {**record, "state": "restored", "restored_at": utc_now()}
        validate_record(updated)
        atomic_json(transaction_path, updated)
    return {"transaction": updated, "restored": restored}


def purge_staged_removal(
    library: str | Path,
    transaction_id: str,
    *,
    confirm_transaction_id: str,
    allow_before_retention: bool = False,
) -> dict[str, Any]:
    """Permanently remove one staged transaction after exact confirmation."""

    if confirm_transaction_id != transaction_id:
        raise StorageError(
            "Permanent purge requires the exact transaction ID",
            code="purge_confirmation_invalid",
            path=confirm_transaction_id,
        )
    root, _ = open_library(library)
    transaction_root = resolve_library_path(
        root,
        f".libraryos/trash/{transaction_id}",
        must_exist=True,
    )
    transaction_path = transaction_root / "transaction.json"
    lock = root / ".libraryos" / "locks" / f"trash-{transaction_id}.lock"
    with exclusive_lock(lock):
        record = read_json_record(transaction_path)
        if record["state"] != "staged":
            raise StorageError(
                "Only staged removals can be permanently purged",
                code="trash_purge_invalid",
                path=transaction_id,
            )
        now = datetime.now(UTC).replace(microsecond=0)
        purge_after = datetime.fromisoformat(record["purge_after"].replace("Z", "+00:00"))
        if now < purge_after and not allow_before_retention:
            raise StorageError(
                "Trash retention deadline has not passed",
                code="trash_retention_active",
                path=record["purge_after"],
            )
        bytes_deleted = 0
        for item in record["moved"]:
            trashed = resolve_library_path(root, item["trashed"], must_exist=True)
            if trashed.is_file():
                bytes_deleted += trashed.stat().st_size
        tombstone = {
            **record,
            "state": "purged",
            "purged_at": now.isoformat().replace("+00:00", "Z"),
        }
        validate_record(tombstone)
        ledger = resolve_library_path(
            root,
            f".libraryos/trash-ledger/{transaction_id}.json",
        )
        atomic_json(ledger, tombstone)
        shutil.rmtree(transaction_root)
    return {
        "transaction": tombstone,
        "bytes_deleted": bytes_deleted,
        "recoverable": False,
        "audit_record": str(ledger.relative_to(root)),
        "works_deleted": [],
        "sources_deleted": [],
    }


def work_reachability(library: str | Path) -> dict[str, Any]:
    """Report why each work must be retained and which works are unreferenced."""

    root, descriptor = open_library(library)
    works = {manifest["id"]: manifest for manifest in list_works(root)}
    reasons: dict[str, list[dict[str, str]]] = {work_id: [] for work_id in works}

    def add(work_id: str, kind: str, reference_id: str) -> None:
        if work_id in reasons:
            reason = {"kind": kind, "reference_id": reference_id}
            if reason not in reasons[work_id]:
                reasons[work_id].append(reason)

    def resolve_reference(reference: Any) -> str | None:
        if isinstance(reference, str):
            return reference
        if isinstance(reference, dict):
            identifier = reference.get("identifier", reference)
            if isinstance(identifier, dict):
                match = resolve_identifier(
                    root,
                    identifier.get("scheme", ""),
                    identifier.get("value", ""),
                    normalized=identifier.get("normalized"),
                )
                return match["id"] if match is not None else None
        return None

    for item in list_collections(root):
        collection = item.get("collection", {})
        collection_id = collection.get("id")
        for member in collection.get("members", []):
            work_id = resolve_reference(member.get("work"))
            if work_id and collection_id:
                add(work_id, "collection", collection_id)
    for review in list_records(root, "reviews"):
        add(review["work_id"], "review", review["id"])
    source_owners = {
        artifact["id"]: work_id
        for work_id, manifest in works.items()
        for artifact in [*manifest["sources"], *manifest["derivatives"]]
    }
    for assessment in list_records(root, "assessments"):
        subject = assessment["subject"]
        if subject["kind"] == "work":
            add(subject["id"], "assessment", assessment["id"])
        elif subject["kind"] == "source" and subject["id"] in source_owners:
            add(source_owners[subject["id"]], "assessment", assessment["id"])
    for occurrence in list_records(root, "occurrences"):
        work_id = resolve_reference(occurrence["work"])
        if work_id:
            add(work_id, "occurrence", occurrence["id"])
    for source_id, manifest in works.items():
        for index, relation in enumerate(manifest.get("relations", [])):
            reference_id = f"{source_id}:{index}"
            add(source_id, "relation", reference_id)
            target_id = resolve_reference(relation["target"])
            if target_id:
                add(target_id, "relation", reference_id)

    entries = [
        {
            "work_id": work_id,
            "reachable": bool(reasons[work_id]),
            "reasons": sorted(
                reasons[work_id],
                key=lambda item: (item["kind"], item["reference_id"]),
            ),
        }
        for work_id in sorted(works)
    ]
    candidates = [item["work_id"] for item in entries if not item["reachable"]]
    return {
        "library_id": descriptor["id"],
        "works": entries,
        "unreferenced_candidates": candidates,
        "candidate_count": len(candidates),
        "applied": False,
    }
