"""Conservative, provider-neutral source acquisition."""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .identity import identifier_key, normalize_identifier
from .jobs import append_attempt, append_exception, create_job, update_job
from .library import open_library, sha256_file, utc_now
from .schemas import validate_record
from .storage import StorageError, atomic_json, resolve_library_path
from .works import get_work, import_source

ACCESS_CLASSES = {
    "open_access",
    "public_repository",
    "institutional",
    "restricted",
    "user_provided",
    "unknown",
}
QUARANTINE_SCHEMA = "https://libraryos.dev/schemas/quarantine/v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")
_REJECTION_MARKERS = {
    "access_restricted": (
        b"sign in to access",
        b"log in to access",
        b"institutional login",
        b"access denied",
        b"purchase this article",
        b"subscribe to access",
    ),
    "bot_challenge": (
        b"captcha",
        b"verify you are human",
        b"checking your browser",
        b"enable javascript and cookies",
        b"cloudflare ray id",
    ),
}


def sanitize_url(url: str) -> str:
    """Return stable, secret-free URL provenance suitable for persistence."""

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StorageError("Source URL must be HTTP(S)", code="source_url_invalid")
    hostname = parsed.hostname.encode("idna").decode("ascii")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), f"{hostname}{port}", parsed.path or "/", "", "")
    )


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate_identity_evidence(
    work: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise StorageError(
            "Acquisition requires explicit work-identity evidence",
            code="identity_evidence_required",
        )
    allowed = {
        "method",
        "expected_sha256",
        "identifiers",
        "title",
        "candidate_id",
    }
    if set(evidence) - allowed:
        raise StorageError(
            "Identity evidence contains unsupported fields",
            code="identity_evidence_invalid",
        )
    candidate_id = evidence.get("candidate_id")
    if candidate_id is not None and (
        not isinstance(candidate_id, str) or not candidate_id.strip()
    ):
        raise StorageError(
            "candidate_id must be a nonempty string",
            code="identity_evidence_invalid",
        )
    method = evidence.get("method")
    if candidate_id is None and (not isinstance(method, str) or not method.strip()):
        raise StorageError(
            "Identity evidence requires a nonempty method",
            code="identity_evidence_invalid",
        )
    if method is not None and (not isinstance(method, str) or not method.strip()):
        raise StorageError(
            "Identity evidence method must be a nonempty string when supplied",
            code="identity_evidence_invalid",
        )
    expected_hash = evidence.get("expected_sha256")
    if expected_hash is not None and (
        not isinstance(expected_hash, str)
        or not _HASH.fullmatch(expected_hash.casefold())
    ):
        raise StorageError(
            "Expected source hash must be a SHA-256 digest",
            code="identity_evidence_invalid",
        )
    identifiers = evidence.get("identifiers", [])
    if not isinstance(identifiers, list) or any(
        not isinstance(item, dict)
        or set(item) != {"scheme", "value"}
        or not all(
            isinstance(item[key], str) and item[key].strip()
            for key in ("scheme", "value")
        )
        for item in identifiers
    ):
        raise StorageError(
            "Identity identifiers must contain only nonempty scheme and value strings",
            code="identity_evidence_invalid",
        )
    work_identifiers = {identifier_key(item) for item in work["work"]["identifiers"]}
    normalized_identifiers = [normalize_identifier(item) for item in identifiers]
    asserted_identifiers = {identifier_key(item) for item in normalized_identifiers}
    if asserted_identifiers and not asserted_identifiers <= work_identifiers:
        raise StorageError(
            "Identity evidence does not agree with the target work identifiers",
            code="identity_evidence_conflict",
        )
    title = evidence.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise StorageError(
                "Identity title must be nonempty",
                code="identity_evidence_invalid",
            )
        work_title = work["work"].get("title")
        if not work_title or _normalized_text(title) != _normalized_text(work_title):
            raise StorageError(
                "Identity evidence does not agree with the target work title",
                code="identity_evidence_conflict",
            )
    if (
        not expected_hash
        and not asserted_identifiers
        and title is None
        and candidate_id is None
    ):
        raise StorageError(
            "Identity evidence has no verifiable assertion",
            code="identity_evidence_invalid",
        )
    return {
        **({"method": method.strip()} if method is not None else {}),
        **({"expected_sha256": expected_hash.casefold()} if expected_hash else {}),
        **({"identifiers": normalized_identifiers} if identifiers else {}),
        **({"title": title} if title is not None else {}),
        **({"candidate_id": candidate_id} if candidate_id is not None else {}),
    }


def _read_edges(path: Path) -> tuple[bytes, bytes]:
    with path.open("rb") as stream:
        prefix = stream.read(262_144)
        stream.seek(max(0, path.stat().st_size - 2048))
        suffix = stream.read()
    return prefix, suffix


def _sniff_media_type(prefix: bytes) -> str:
    stripped = prefix.lstrip()
    lowered = stripped.lower()
    if stripped.startswith(b"%PDF-"):
        return "application/pdf"
    if stripped.startswith(b"PK\x03\x04"):
        return "application/zip"
    if stripped.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if any(marker in lowered for marker in _HTML_MARKERS):
        return "text/html"
    if stripped.startswith(b"<?xml") or (
        stripped.startswith(b"<") and b">" in stripped
    ):
        return "application/xml"
    try:
        stripped.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"


def _inspect_response(path: Path, declared_media_type: str) -> tuple[str, str | None]:
    prefix, suffix = _read_edges(path)
    sniffed = _sniff_media_type(prefix)
    sample = prefix.lower()
    declared = declared_media_type.casefold().split(";", 1)[0].strip()
    compatible = (
        declared in {"application/octet-stream", "binary/octet-stream"}
        or declared == sniffed
        or (
            declared in {"text/xml", "application/xml"}
            and sniffed == "application/xml"
        )
        or (declared.startswith("text/") and sniffed == "text/plain")
    )
    if not compatible:
        return sniffed, "type_mismatch"
    if sniffed == "text/html":
        for code, markers in _REJECTION_MARKERS.items():
            if any(marker in sample for marker in markers):
                return sniffed, code
        if (
            b"<abstract" in sample
            or b'class="abstract' in sample
            or b'id="abstract' in sample
        ) and not any(
            marker in sample
            for marker in (b"<article", b"full text", b'class="article', b'id="article')
        ):
            return sniffed, "abstract_only"
        if len(sample) < 1024 and not any(
            marker in sample for marker in (b"<article", b"<main", b"full text")
        ):
            return sniffed, "landing_page"
    if sniffed == "application/pdf" and not suffix.rstrip().endswith(b"%%EOF"):
        return sniffed, "corrupt_source"
    return sniffed, None


def _identity_matches(
    path: Path,
    evidence: dict[str, Any],
    media_type: str,
) -> bool:
    expected_hash = evidence.get("expected_sha256")
    if expected_hash:
        return sha256_file(path) == expected_hash
    if evidence.get("candidate_id"):
        return True
    if media_type not in {"text/plain", "text/html", "application/xml"}:
        return False
    text = path.read_bytes()[:2_000_000].decode("utf-8", errors="ignore")
    normalized = _normalized_text(re.sub(r"<[^>]+>", " ", text))
    identifiers = evidence.get("identifiers", [])
    if identifiers and all(
        _normalized_text(item["value"]) in normalized for item in identifiers
    ):
        return True
    title = evidence.get("title")
    return bool(title and _normalized_text(title) in normalized)


def _quarantine(
    library: str | Path,
    work_id: str,
    source_path: Path,
    *,
    reason: str,
    media_type: str,
    provider: str,
    canonical_url: str,
    identity_method: str,
) -> dict[str, Any]:
    root, _ = open_library(library)
    quarantine_id = str(uuid.uuid4())
    directory = resolve_library_path(root, f"quarantine/{quarantine_id}")
    directory.mkdir(mode=0o700)
    suffix = mimetypes.guess_extension(media_type) or ".bin"
    payload = directory / f"payload{suffix}"
    try:
        shutil.copyfile(source_path, payload)
        os.chmod(payload, 0o600)
        record = {
            "schema": QUARANTINE_SCHEMA,
            "schema_version": 1,
            "id": quarantine_id,
            "work_id": work_id,
            "path": payload.name,
            "sha256": sha256_file(payload),
            "bytes": payload.stat().st_size,
            "media_type": media_type,
            "reason": reason,
            "provider": provider,
            "canonical_url": canonical_url,
            "identity_method": identity_method,
            "created_at": utc_now(),
        }
        validate_record(record)
        atomic_json(directory / "manifest.json", record)
        return record
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def acquire_url(
    library: str | Path,
    work_id: str,
    url: str,
    *,
    role: str,
    access: str,
    allowed_access: list[str],
    identity_evidence: dict[str, Any] | None = None,
    version: str = "unknown",
    expected_media_type: str | None = None,
    max_bytes: int = 100_000_000,
    timeout: float = 60,
    provider: str = "url",
    requested_by: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Acquire an authorized source only when its work identity is defensible."""

    safe_url = sanitize_url(url)
    if access not in ACCESS_CLASSES or access not in set(allowed_access):
        raise StorageError(
            "Source access class was not explicitly authorized",
            code="access_not_authorized",
            path=access,
        )
    if max_bytes < 1:
        raise StorageError("Maximum size must be positive", code="max_bytes_invalid")
    work = get_work(library, work_id)
    evidence = _validate_identity_evidence(work, identity_evidence)
    candidate_id = evidence.get("candidate_id")
    if candidate_id is not None:
        candidate = next(
            (
                item
                for item in work.get("source_candidates", [])
                if item["id"] == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise StorageError(
                "Verified source candidate does not exist on this work",
                code="source_candidate_not_found",
                path=candidate_id,
            )
        if candidate["url"] != safe_url:
            raise StorageError(
                "Acquisition URL does not match the verified source candidate",
                code="source_candidate_mismatch",
                path=candidate_id,
            )
        if candidate["access"] != access:
            raise StorageError(
                "Acquisition access does not match the verified source candidate",
                code="source_candidate_mismatch",
                path=candidate_id,
            )
        if provider == "url":
            provider = candidate["provider"]
        evidence["method"] = f"registered_candidate:{candidate['identity_method']}"
    job = create_job(
        library,
        operation="libraryos.source.acquire",
        arguments={
            "work_id": work_id,
            "canonical_url": safe_url,
            "role": role,
            "access": access,
            "max_bytes": max_bytes,
            "identity_method": evidence["method"],
        },
        requested_by=requested_by or {"kind": "human", "id": "local-user"},
        capabilities=["source.acquire"],
    )
    update_job(library, job["id"], status="running")
    descriptor, temporary_name = tempfile.mkstemp(prefix="libraryos-acquire-")
    os.close(descriptor)
    temporary = Path(temporary_name)
    quarantine: dict[str, Any] | None = None
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "LibraryOS/0.1 (+https://github.com/davidkastner/libraryos)"
            },
        )
        try:
            response_context = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code in {401, 402, 403, 407, 451}:
                code = "access_restricted"
            elif error.code in {404, 410}:
                code = "not_found"
            else:
                code = "retrieval_failed"
            raise StorageError(
                f"Remote source request failed with HTTP {error.code}",
                code=code,
                path=safe_url,
            ) from error
        except urllib.error.URLError as error:
            raise StorageError(
                "Remote source request failed",
                code="retrieval_failed",
                path=safe_url,
            ) from error
        with response_context as response:
            content_length = response.headers.get("Content-Length")
            try:
                declared_size = int(content_length) if content_length else None
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                raise StorageError(
                    "Remote source exceeds configured size limit",
                    code="source_too_large",
                    path=safe_url,
                )
            received = 0
            with temporary.open("wb") as output:
                while chunk := response.read(
                    min(1024 * 1024, max_bytes + 1 - received)
                ):
                    received += len(chunk)
                    if received > max_bytes:
                        raise StorageError(
                            "Remote source exceeds configured size limit",
                            code="source_too_large",
                            path=safe_url,
                        )
                    output.write(chunk)
            declared_media_type = response.headers.get_content_type()
            response_url = sanitize_url(response.geturl())
        if temporary.stat().st_size == 0:
            raise StorageError(
                "Remote source is empty",
                code="corrupt_source",
                path=safe_url,
            )
        media_type, rejection = _inspect_response(temporary, declared_media_type)
        if expected_media_type and media_type != expected_media_type:
            rejection = "type_mismatch"
        if rejection is None and not _identity_matches(
            temporary,
            evidence,
            media_type,
        ):
            rejection = "ambiguous_identity"
        if rejection is not None:
            quarantine = _quarantine(
                library,
                work_id,
                temporary,
                reason=rejection,
                media_type=media_type,
                provider=provider,
                canonical_url=response_url,
                identity_method=evidence["method"],
            )
            raise StorageError(
                "Retrieved bytes were quarantined and not assigned to the work",
                code=rejection,
                path=quarantine["id"],
            )
        suffix = mimetypes.guess_extension(media_type) or ""
        named = temporary.with_suffix(suffix)
        temporary.rename(named)
        temporary = named
        result = import_source(
            library,
            work_id,
            temporary,
            role=role,
            version=version,
            access=access,
            identity_status="verified",
            identity_method=evidence["method"],
            media_type=media_type,
            provider=provider,
            canonical_url=response_url,
        )
        append_attempt(
            library,
            job["id"],
            status="succeeded",
            provider=provider,
        )
        update_job(library, job["id"], status="succeeded", result=result)
        return {**result, "job_id": job["id"]}
    except (OSError, StorageError) as error:
        code = getattr(error, "code", "acquisition_failed")
        if not isinstance(code, str):
            code = "acquisition_failed"
        message = (
            "Retrieved bytes were quarantined"
            if quarantine is not None
            else f"Acquisition failed: {code}"
        )
        append_attempt(
            library,
            job["id"],
            status="failed",
            provider=provider,
            diagnostic=message,
            error_code=code,
        )
        append_exception(
            library,
            job["id"],
            category=code,
            message=message,
        )
        update_job(
            library,
            job["id"],
            status="failed",
            result=(
                {"quarantine_id": quarantine["id"]}
                if quarantine is not None
                else None
            ),
        )
        raise
    finally:
        temporary.unlink(missing_ok=True)
