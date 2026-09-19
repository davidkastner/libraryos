"""Authoritative work and immutable-source operations."""

from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .identity import identifier_key, normalize_identifiers
from .library import WORK_SCHEMA, open_library, sha256_file, utc_now
from .schemas import validate_record
from .storage import (
    StorageError,
    atomic_json,
    exclusive_lock,
    read_json_record,
    resolve_library_path,
)


def _work_path(root: Path, work_id: str) -> Path:
    try:
        canonical = str(uuid.UUID(work_id))
    except ValueError as error:
        raise StorageError("Work ID must be a UUID", code="work_id_invalid", path=work_id) from error
    path = resolve_library_path(root, f"works/{canonical}")
    if not path.is_dir():
        raise StorageError("Work does not exist", code="work_not_found", path=work_id)
    return path


def create_work(
    library: str | Path,
    *,
    work_type: str,
    title: str | None,
    identifiers: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create an empty authoritative work bundle atomically."""

    root, _ = open_library(library)
    if metadata is not None and not isinstance(metadata, dict):
        raise StorageError("Work metadata must be an object", code="work_metadata_invalid")
    reserved = {"type", "identifiers", "title"} & set(metadata or {})
    if reserved:
        raise StorageError(
            f"Work metadata cannot replace core fields: {sorted(reserved)}",
            code="work_metadata_reserved",
        )
    normalized_identifiers = normalize_identifiers(identifiers or [])
    work_id = str(uuid.uuid4())
    now = utc_now()
    work = {
        "type": work_type,
        "identifiers": normalized_identifiers,
        "title": title,
        **(metadata or {}),
    }
    manifest = {
        "schema": WORK_SCHEMA,
        "schema_version": 1,
        "id": work_id,
        "work": work,
        "source_candidates": [],
        "sources": [],
        "derivatives": [],
        **({"relations": relations} if relations is not None else {}),
        "created_at": now,
        "updated_at": now,
    }
    validate_record(manifest)
    with exclusive_lock(root / ".libraryos" / "locks" / "identity.lock"):
        requested = {identifier_key(item) for item in normalized_identifiers}
        for manifest_path in sorted((root / "works").glob("*/manifest.json")):
            existing = read_json_record(manifest_path)
            for identifier in existing["work"]["identifiers"]:
                key = identifier_key(identifier)
                if key in requested:
                    raise StorageError(
                        "Identifier is already assigned to another work",
                        code="identifier_conflict",
                        path=f"{key[0]}:{key[1]}",
                    )
        staging = Path(tempfile.mkdtemp(prefix=".work-", dir=root / "works"))
        target = root / "works" / work_id
        try:
            (staging / "source").mkdir(mode=0o700)
            (staging / "derived").mkdir(mode=0o700)
            (staging / "supplements").mkdir(mode=0o700)
            atomic_json(staging / "manifest.json", manifest)
            os.replace(staging, target)
            directory = os.open(root / "works", os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return manifest


def get_work(library: str | Path, work_id: str) -> dict[str, Any]:
    """Return one validated work manifest."""

    root, _ = open_library(library)
    return read_json_record(_work_path(root, work_id) / "manifest.json")


def list_works(library: str | Path) -> list[dict[str, Any]]:
    """Return validated work manifests in stable ID order."""

    root, _ = open_library(library)
    return [
        read_json_record(path)
        for path in sorted((root / "works").glob("*/manifest.json"))
    ]


def register_source_candidate(
    library: str | Path,
    work_id: str,
    *,
    url: str,
    provider: str,
    access: str,
    identity_method: str,
    verified_by: str,
    verified_at: str,
    media_type: str | None = None,
    version: str = "unknown",
) -> dict[str, Any]:
    """Record a provider-verified source candidate without retrieving it."""

    # Import locally to keep URL persistence policy defined in one place
    # without introducing an import cycle at module import time.
    from .acquisition import ACCESS_CLASSES, sanitize_url

    root, _ = open_library(library)
    bundle = _work_path(root, work_id)
    canonical_url = sanitize_url(url)
    if access not in ACCESS_CLASSES:
        raise StorageError(
            "Source candidate has an unknown access class",
            code="access_class_invalid",
            path=access,
        )
    if not all(
        isinstance(value, str) and value.strip()
        for value in (provider, identity_method, verified_by, verified_at)
    ):
        raise StorageError(
            "Candidate verification provenance must be complete",
            code="candidate_verification_invalid",
        )
    with exclusive_lock(root / ".libraryos" / "locks" / f"work-{work_id}.lock"):
        manifest_path = bundle / "manifest.json"
        manifest = read_json_record(manifest_path)
        for existing in manifest.get("source_candidates", []):
            if (
                existing["url"] == canonical_url
                and existing["provider"] == provider
                and existing["access"] == access
            ):
                return {"created": False, "candidate": existing, "work_id": work_id}
        candidate = {
            "id": str(uuid.uuid4()),
            "url": canonical_url,
            "provider": provider,
            "access": access,
            "media_type": media_type,
            "version": version,
            "identity_status": "verified",
            "identity_method": identity_method,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "created_at": utc_now(),
        }
        updated = {
            **manifest,
            "source_candidates": [
                *manifest.get("source_candidates", []),
                candidate,
            ],
            "updated_at": utc_now(),
        }
        validate_record(updated)
        atomic_json(manifest_path, updated)
    return {"created": True, "candidate": candidate, "work_id": work_id}


def import_source(
    library: str | Path,
    work_id: str,
    source_path: str | Path,
    *,
    role: str,
    version: str = "unknown",
    access: str = "user_provided",
    identity_status: str = "unverified",
    identity_method: str | None = None,
    media_type: str | None = None,
    provider: str | None = None,
    canonical_url: str | None = None,
    license_name: str | None = None,
    license_source: str | None = None,
) -> dict[str, Any]:
    """Copy bytes into a work as an immutable source and publish its record."""

    root, _ = open_library(library)
    bundle = _work_path(root, work_id)
    supplied = Path(source_path).expanduser().resolve()
    if not supplied.is_file():
        raise StorageError("Source is not a regular file", code="source_not_found", path=str(supplied))
    digest = sha256_file(supplied)
    with exclusive_lock(root / ".libraryos" / "locks" / f"work-{work_id}.lock"):
        manifest_path = bundle / "manifest.json"
        manifest = read_json_record(manifest_path)
        for existing in manifest["sources"]:
            if existing["sha256"] == digest:
                return {"created": False, "source": existing, "work_id": work_id}

        source_id = str(uuid.uuid4())
        suffix = supplied.suffix.lower()
        relative = f"source/{source_id}{suffix}"
        destination = resolve_library_path(bundle, relative)
        source = {
            "id": source_id,
            "path": relative,
            "sha256": digest,
            "bytes": supplied.stat().st_size,
            "media_type": media_type
            or mimetypes.guess_type(supplied.name)[0]
            or "application/octet-stream",
            "role": role,
            "version": version,
            "access": access,
            "license": license_name,
            "license_source": license_source,
            "provider": provider,
            "canonical_url": canonical_url,
            "original_filename": supplied.name,
            "retrieved_at": utc_now(),
            "identity_status": identity_status,
            "identity_method": identity_method,
            "warnings": [],
        }
        updated = {
            **manifest,
            "sources": [*manifest["sources"], source],
            "updated_at": utc_now(),
        }
        # Validate all metadata before any source bytes enter the library.
        validate_record(updated)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-", dir=destination.parent
        )
        temporary = Path(temporary_name)
        published = False
        try:
            with supplied.open("rb") as incoming, os.fdopen(descriptor, "wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if temporary.stat().st_size != source["bytes"] or sha256_file(temporary) != digest:
                raise StorageError(
                    "Copied source failed hash verification",
                    code="source_copy_hash_mismatch",
                    path=str(supplied),
                )
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            published = True
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            atomic_json(manifest_path, updated)
        except Exception:
            if published:
                destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return {"created": True, "source": source, "work_id": work_id}


def register_derivative(
    library: str | Path,
    work_id: str,
    derivative_path: str | Path,
    *,
    role: str,
    input_sha256: list[str],
    generator_name: str,
    generator_version: str,
    configuration_sha256: str | None = None,
    quality: str = "ready",
    media_type: str | None = None,
    warnings: list[str] | None = None,
    locators: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Register generated bytes with exact input lineage."""

    root, _ = open_library(library)
    bundle = _work_path(root, work_id)
    supplied = Path(derivative_path).expanduser().resolve()
    if not supplied.is_file():
        raise StorageError(
            "Derivative is not a regular file",
            code="derivative_not_found",
            path=str(supplied),
        )
    digest = sha256_file(supplied)
    with exclusive_lock(root / ".libraryos" / "locks" / f"work-{work_id}.lock"):
        manifest_path = bundle / "manifest.json"
        manifest = read_json_record(manifest_path)
        available = {source["sha256"] for source in manifest["sources"]}
        missing = set(input_sha256) - available
        if missing:
            raise StorageError(
                "Derivative inputs are not registered sources",
                code="derivative_input_missing",
                path=str(supplied),
            )
        for existing in manifest["derivatives"]:
            if (
                existing["sha256"] == digest
                and existing["role"] == role
                and existing["input_sha256"] == input_sha256
                and existing["generator"]
                == {
                    "name": generator_name,
                    "version": generator_version,
                    **(
                        {"configuration_sha256": configuration_sha256}
                        if configuration_sha256 is not None
                        else {}
                    ),
                }
                and existing.get("locators", []) == (locators or [])
            ):
                return {"created": False, "derivative": existing, "work_id": work_id}

        derivative_id = str(uuid.uuid4())
        suffix = supplied.suffix.lower()
        relative = f"derived/{derivative_id}{suffix}"
        destination = resolve_library_path(bundle, relative)
        derivative = {
            "id": derivative_id,
            "path": relative,
            "sha256": digest,
            "bytes": supplied.stat().st_size,
            "media_type": media_type
            or mimetypes.guess_type(supplied.name)[0]
            or "application/octet-stream",
            "role": role,
            "input_sha256": input_sha256,
            "generator": {
                "name": generator_name,
                "version": generator_version,
                **(
                    {"configuration_sha256": configuration_sha256}
                    if configuration_sha256 is not None
                    else {}
                ),
            },
            "generated_at": utc_now(),
            "quality": quality,
            "warnings": warnings or [],
            "locators": locators or [],
        }
        updated = {
            **manifest,
            "derivatives": [*manifest["derivatives"], derivative],
            "updated_at": utc_now(),
        }
        validate_record(updated)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".derivative-", dir=destination.parent
        )
        temporary = Path(temporary_name)
        published = False
        try:
            with supplied.open("rb") as incoming, os.fdopen(descriptor, "wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if temporary.stat().st_size != derivative["bytes"] or sha256_file(temporary) != digest:
                raise StorageError(
                    "Copied derivative failed hash verification",
                    code="derivative_copy_hash_mismatch",
                    path=str(supplied),
                )
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            published = True
            atomic_json(manifest_path, updated)
        except Exception:
            if published:
                destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return {"created": True, "derivative": derivative, "work_id": work_id}
