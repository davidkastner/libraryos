"""Deterministic, domain-neutral work identifier normalization and resolution."""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

from .library import open_library
from .storage import StorageError, read_json_record

_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_NEW = re.compile(r"^\d{4}\.\d{4,5}(?:v[1-9]\d*)?$", re.IGNORECASE)
_ARXIV_OLD = re.compile(
    r"^[a-z][a-z0-9.-]*/\d{7}(?:v[1-9]\d*)?$",
    re.IGNORECASE,
)


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageError(
            f"Identifier {field} must be a nonempty string",
            code="identifier_invalid",
            path=field,
        )
    return value.strip()


def _strip_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    lowered = value.casefold()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def _normalize_doi(value: str) -> str:
    value = _strip_prefix(
        value,
        (
            "doi:",
            "https://doi.org/",
            "http://doi.org/",
            "https://dx.doi.org/",
            "http://dx.doi.org/",
        ),
    )
    try:
        value = urllib.parse.unquote(value, errors="strict")
    except UnicodeDecodeError as error:
        raise StorageError(
            "DOI contains invalid percent encoding",
            code="identifier_invalid",
            path=value,
        ) from error
    normalized = value.strip().casefold()
    if (
        not _DOI.fullmatch(normalized)
        or any(character.isspace() or ord(character) < 32 for character in normalized)
    ):
        raise StorageError(
            "DOI is not syntactically valid",
            code="identifier_invalid",
            path=value,
        )
    return normalized


def _normalize_pmid(value: str) -> str:
    value = _strip_prefix(value, ("pmid:", "https://pubmed.ncbi.nlm.nih.gov/"))
    value = value.strip().rstrip("/")
    if not value.isascii() or not value.isdigit():
        raise StorageError(
            "PMID must contain only decimal digits",
            code="identifier_invalid",
            path=value,
        )
    numeric = int(value)
    if numeric == 0:
        raise StorageError(
            "PMID must be greater than zero",
            code="identifier_invalid",
            path=value,
        )
    return str(numeric)


def _normalize_pmcid(value: str) -> str:
    value = _strip_prefix(
        value,
        (
            "pmcid:",
            "https://www.ncbi.nlm.nih.gov/pmc/articles/",
            "https://pmc.ncbi.nlm.nih.gov/articles/",
        ),
    ).strip().rstrip("/")
    value = _strip_prefix(value, ("pmc",))
    if not value.isascii() or not value.isdigit():
        raise StorageError(
            "PMCID must have the form PMC followed by decimal digits",
            code="identifier_invalid",
            path=value,
        )
    numeric = int(value)
    if numeric == 0:
        raise StorageError(
            "PMCID must be greater than zero",
            code="identifier_invalid",
            path=value,
        )
    return f"PMC{numeric}"


def _normalize_arxiv(value: str) -> str:
    value = _strip_prefix(
        value,
        (
            "arxiv:",
            "https://arxiv.org/abs/",
            "http://arxiv.org/abs/",
            "https://arxiv.org/pdf/",
            "http://arxiv.org/pdf/",
        ),
    ).strip()
    if value.casefold().endswith(".pdf"):
        value = value[:-4]
    normalized = value.rstrip("/").casefold()
    if not (_ARXIV_NEW.fullmatch(normalized) or _ARXIV_OLD.fullmatch(normalized)):
        raise StorageError(
            "arXiv identifier is not syntactically valid",
            code="identifier_invalid",
            path=value,
        )
    return normalized


def _normalize_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise StorageError(
            "URL identifier is not valid",
            code="identifier_invalid",
            path=value,
        ) from error
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise StorageError(
            "URL identifier must be an absolute HTTP or HTTPS URL",
            code="identifier_invalid",
            path=value,
        )
    if parsed.username is not None or parsed.password is not None:
        raise StorageError(
            "URL identifiers must not contain credentials",
            code="identifier_invalid",
            path=value,
        )
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname.casefold()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise StorageError(
            "URL identifier has an invalid hostname",
            code="identifier_invalid",
            path=value,
        ) from error
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    authority = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((scheme, authority, path, parsed.query, ""))


def _normalize_isbn(value: str) -> str:
    normalized = re.sub(r"[\s-]", "", value).upper()
    if not re.fullmatch(r"(?:\d{9}[\dX]|\d{13})", normalized):
        raise StorageError(
            "ISBN must contain 10 or 13 digits (with X allowed as an ISBN-10 check digit)",
            code="identifier_invalid",
            path=value,
        )
    if len(normalized) == 10:
        total = sum((10 - index) * (10 if digit == "X" else int(digit)) for index, digit in enumerate(normalized))
        valid = total % 11 == 0
    else:
        total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(normalized))
        valid = total % 10 == 0
    if not valid:
        raise StorageError(
            "ISBN check digit is invalid",
            code="identifier_invalid",
            path=value,
        )
    return normalized


_NORMALIZERS = {
    "doi": _normalize_doi,
    "pmid": _normalize_pmid,
    "pmcid": _normalize_pmcid,
    "arxiv": _normalize_arxiv,
    "url": _normalize_url,
    "isbn": _normalize_isbn,
}


def normalize_identifier(identifier: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical identifier without discarding its supplied display value."""

    if not isinstance(identifier, dict):
        raise StorageError(
            "Identifier must be an object",
            code="identifier_invalid",
        )
    unknown = set(identifier) - {"scheme", "value", "normalized", "status"}
    if unknown:
        raise StorageError(
            f"Identifier contains unsupported fields: {sorted(unknown)}",
            code="identifier_invalid",
        )
    scheme = _require_text(identifier.get("scheme"), field="scheme").casefold()
    if not re.fullmatch(r"[a-z][a-z0-9._-]*", scheme):
        raise StorageError(
            "Identifier scheme is not valid",
            code="identifier_invalid",
            path=scheme,
        )
    value = _require_text(identifier.get("value"), field="value")
    supplied_normalized = identifier.get("normalized")
    if scheme in _NORMALIZERS:
        normalized = _NORMALIZERS[scheme](value)
        if supplied_normalized is not None and _NORMALIZERS[scheme](
            _require_text(supplied_normalized, field="normalized")
        ) != normalized:
            raise StorageError(
                "Supplied normalized identifier conflicts with its value",
                code="identifier_normalized_conflict",
                path=f"{scheme}:{value}",
            )
    elif supplied_normalized is not None:
        normalized = _require_text(supplied_normalized, field="normalized")
    else:
        # User-defined schemes are deliberately opaque and case-sensitive.
        normalized = value
    result = {"scheme": scheme, "value": value, "normalized": normalized}
    if "status" in identifier:
        result["status"] = identifier["status"]
    return result


def identifier_key(identifier: dict[str, Any]) -> tuple[str, str]:
    """Return the authoritative uniqueness key for an identifier."""

    normalized = normalize_identifier(identifier)
    return normalized["scheme"], normalized["normalized"]


def normalize_identifiers(identifiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a work's identifiers and reject aliases repeated within that work."""

    if not isinstance(identifiers, list):
        raise StorageError(
            "Work identifiers must be a list",
            code="identifier_invalid",
        )
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for identifier in identifiers:
        normalized = normalize_identifier(identifier)
        key = (normalized["scheme"], normalized["normalized"])
        if key in seen:
            raise StorageError(
                "Identifier appears more than once in the work",
                code="identifier_duplicate",
                path=f"{key[0]}:{key[1]}",
            )
        seen.add(key)
        result.append(normalized)
    return result


def resolve_identifier(
    library: str | Path,
    scheme: str,
    value: str,
    *,
    normalized: str | None = None,
) -> dict[str, Any] | None:
    """Resolve an identifier against authoritative manifests.

    Return ``None`` when absent and raise a structured conflict if manual or
    legacy edits have made the alias ambiguous.
    """

    root, _ = open_library(library)
    wanted = normalize_identifier(
        {
            "scheme": scheme,
            "value": value,
            **({"normalized": normalized} if normalized is not None else {}),
        }
    )
    key = (wanted["scheme"], wanted["normalized"])
    matches: list[dict[str, Any]] = []
    for manifest_path in sorted((root / "works").glob("*/manifest.json")):
        manifest = read_json_record(manifest_path)
        if any(identifier_key(item) == key for item in manifest["work"]["identifiers"]):
            matches.append(manifest)
    if len(matches) > 1:
        raise StorageError(
            "Identifier resolves to multiple works",
            code="identifier_conflict",
            path=f"{key[0]}:{key[1]}",
        )
    return matches[0] if matches else None
