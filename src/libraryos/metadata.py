"""Explicit bibliographic assertion, acceptance, and discovery operations."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .identity import identifier_key, normalize_identifier
from .library import open_library, utc_now
from .providers import (
    CrossrefProvider,
    MetadataProvider,
    OpenAlexProvider,
    RelationDiscoveryProvider,
    SourceDiscoveryProvider,
)
from .schemas import validate_record
from .storage import StorageError, atomic_json, exclusive_lock, read_json_record
from .works import get_work, register_source_candidate

_BIBLIOGRAPHIC_FIELDS = {
    "title",
    "authors",
    "container_title",
    "publisher",
    "issued",
}


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).casefold().split())


def _conflicts(work: dict[str, Any], assertion: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    for field in sorted(_BIBLIOGRAPHIC_FIELDS):
        current = work["work"].get(field)
        proposed = assertion["data"].get(field)
        if current not in (None, "", []) and proposed not in (None, "", []):
            if _text(current) != _text(proposed):
                conflicts.append(field)
    return conflicts


def resolve_metadata(
    library: str | Path,
    work_id: str,
    *,
    provider: MetadataProvider,
    scheme: str | None = None,
) -> dict[str, Any]:
    """Store a provider result as an assertion without changing accepted metadata."""

    root, _ = open_library(library)
    work = get_work(root, work_id)
    identifiers = work["work"]["identifiers"]
    requested = next(
        (
            item
            for item in identifiers
            if scheme is None or item["scheme"] == scheme.casefold()
        ),
        None,
    )
    if requested is None:
        raise StorageError(
            "Work has no identifier supported by the requested resolution",
            code="provider_identifier_missing",
            path=scheme,
        )
    result = provider.resolve(requested)
    if identifier_key(result.requested_identifier) != identifier_key(requested):
        raise StorageError(
            "Provider result does not identify the requested work",
            code="provider_identity_conflict",
        )
    returned_keys = {identifier_key(item) for item in result.identifiers}
    if identifier_key(requested) not in returned_keys:
        raise StorageError(
            "Provider did not return the requested identifier",
            code="provider_identity_conflict",
        )
    now = utc_now()
    assertion = {
        "id": str(uuid.uuid4()),
        "provider": provider.name,
        "provider_version": provider.version,
        "requested_identifier": result.requested_identifier,
        "returned_identifiers": result.identifiers,
        "retrieved_at": now,
        "data": result.bibliographic,
        "source_url": result.source_url,
        "raw_response_sha256": result.raw_response_sha256,
        "warnings": list(result.warnings),
        "status": "candidate",
    }
    lock = root / ".libraryos" / "locks" / f"work-{work_id}.lock"
    with exclusive_lock(lock):
        path = root / "works" / work_id / "manifest.json"
        current = read_json_record(path)
        conflicts = _conflicts(current, assertion)
        if conflicts:
            assertion["status"] = "conflicting"
            assertion["conflicts"] = conflicts
        updated = {
            **current,
            "work": {
                **current["work"],
                "metadata_status": assertion["status"],
                "metadata_assertions": [
                    *current["work"].get("metadata_assertions", []),
                    assertion,
                ],
            },
            "updated_at": now,
        }
        validate_record(updated)
        atomic_json(path, updated)
        from .catalog import index_work_if_present

        index_work_if_present(root, updated)
    return {
        "work_id": work_id,
        "assertion": assertion,
        "accepted": False,
        "next_actions": [
            {
                "operation": "metadata.assertion.accept",
                "purpose": "Review and explicitly accept the resolved bibliographic metadata.",
                "arguments": {
                    "library": str(root),
                    "work_id": work_id,
                    "assertion_id": assertion["id"],
                },
            }
        ],
    }


def accept_metadata_assertion(
    library: str | Path,
    work_id: str,
    assertion_id: str,
    *,
    accept_conflicts: bool = False,
) -> dict[str, Any]:
    """Explicitly accept one assertion and apply its bibliographic fields."""

    root, _ = open_library(library)
    lock = root / ".libraryos" / "locks" / f"work-{work_id}.lock"
    with exclusive_lock(lock):
        path = root / "works" / work_id / "manifest.json"
        manifest = read_json_record(path)
        assertions = manifest["work"].get("metadata_assertions", [])
        selected = next((item for item in assertions if item["id"] == assertion_id), None)
        if selected is None:
            raise StorageError(
                "Metadata assertion does not exist",
                code="metadata_assertion_not_found",
                path=assertion_id,
            )
        conflicts = _conflicts(manifest, selected)
        if conflicts and not accept_conflicts:
            raise StorageError(
                f"Metadata assertion conflicts with accepted fields: {conflicts}",
                code="metadata_conflict_requires_acceptance",
                path=assertion_id,
            )
        now = utc_now()
        revised = [
            {
                **item,
                "status": (
                    "accepted"
                    if item["id"] == assertion_id
                    else "rejected"
                    if item.get("status") == "accepted"
                    else item.get("status", "candidate")
                ),
            }
            for item in assertions
        ]
        accepted_data = {
            key: value
            for key, value in selected["data"].items()
            if key in _BIBLIOGRAPHIC_FIELDS
        }
        updated = {
            **manifest,
            "work": {
                **manifest["work"],
                **accepted_data,
                "metadata_status": "verified",
                "metadata_assertions": revised,
            },
            "updated_at": now,
        }
        validate_record(updated)
        atomic_json(path, updated)
        from .catalog import index_work_if_present

        index_work_if_present(root, updated)
    return {
        "work_id": work_id,
        "assertion_id": assertion_id,
        "accepted": True,
        "next_actions": [
            {
                "operation": "source.crossref.discover",
                "purpose": "Discover source candidates without retrieving bytes.",
                "arguments": {"library": str(root), "work_id": work_id},
            }
        ],
    }


def discover_sources(
    library: str | Path,
    work_id: str,
    *,
    provider: SourceDiscoveryProvider,
) -> dict[str, Any]:
    """Persist provider candidates without retrieving bytes."""

    work = get_work(library, work_id)
    discovered = provider.discover(work)
    results = []
    verified_at = utc_now()
    for candidate in discovered:
        results.append(
            register_source_candidate(
                library,
                work_id,
                url=candidate.url,
                provider=provider.name,
                access=candidate.access,
                identity_method=candidate.identity_method,
                verified_by=f"{provider.name}/{provider.version}",
                verified_at=verified_at,
                media_type=candidate.media_type,
                version=candidate.version,
            )
        )
    next_actions = [
        {
            "operation": "source.acquire",
            "purpose": "Acquire this candidate after choosing its source role and authorizing its access class.",
            "arguments": {
                "library": str(Path(library).expanduser().resolve()),
                "work_id": work_id,
                "url": item["candidate"]["url"],
                "access": item["candidate"]["access"],
                "allowed_access": [item["candidate"]["access"]],
                "identity_evidence": {
                    "candidate_id": item["candidate"]["id"],
                },
            },
            "required_arguments": ["role"],
        }
        for item in results
    ]
    return {
        "work_id": work_id,
        "provider": {"name": provider.name, "version": provider.version},
        "candidates": results,
        "acquired": False,
        "scientific_inspection": "not_assessed",
        "next_actions": next_actions,
    }


def resolve_crossref(
    library: str | Path,
    work_id: str,
    *,
    email: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    """Resolve a DOI through the concrete Crossref adapter."""

    return resolve_metadata(
        library,
        work_id,
        provider=CrossrefProvider(email=email, timeout=timeout),
        scheme="doi",
    )


def discover_crossref(
    library: str | Path,
    work_id: str,
    *,
    email: str | None = None,
    timeout: float = 30,
    publisher_access: str = "unknown",
) -> dict[str, Any]:
    """Discover Crossref candidates without acquiring their bytes."""

    return discover_sources(
        library,
        work_id,
        provider=CrossrefProvider(
            email=email,
            timeout=timeout,
            publisher_access=publisher_access,
        ),
    )


def discover_relations(
    identifier: dict[str, Any],
    *,
    provider: RelationDiscoveryProvider,
    directions: list[str],
    limit: int = 25,
) -> dict[str, Any]:
    """Discover scholarly neighbors without persisting or assessing them."""

    requested = normalize_identifier(identifier)
    candidates = provider.discover(
        requested,
        directions=directions,
        limit=limit,
    )
    return {
        "seed_identifier": requested,
        "provider": {"name": provider.name, "version": provider.version},
        "directions": list(directions),
        "candidates": [
            {
                "relation": candidate.relation,
                "identifiers": candidate.identifiers,
                "title": candidate.title,
                "publication_year": candidate.publication_year,
                "provider_work_id": candidate.provider_work_id,
                "source_url": candidate.source_url,
                "inspection_status": "not_inspected",
                "scientific_support": "not_assessed",
            }
            for candidate in candidates
        ],
        "persisted": False,
        "scientific_inspection": "not_assessed",
        "scientific_support": "not_assessed",
    }


def discover_openalex_relations(
    doi: str,
    *,
    directions: list[str] | None = None,
    limit: int = 25,
    email: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    """Discover DOI-linked references and citations through OpenAlex."""

    return discover_relations(
        {"scheme": "doi", "value": doi},
        provider=OpenAlexProvider(email=email, timeout=timeout),
        directions=directions or ["references", "citations"],
        limit=limit,
    )
