"""Provider contracts for bibliographic assertions and source discovery."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .identity import identifier_key, normalize_identifier
from .storage import StorageError


@dataclass(frozen=True)
class MetadataProviderResult:
    """One provider's exact bibliographic assertion and its provenance."""

    requested_identifier: dict[str, Any]
    identifiers: list[dict[str, Any]]
    bibliographic: dict[str, Any]
    source_url: str
    raw_response_sha256: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceCandidateResult:
    """A discovered location; discovery never implies acquisition."""

    url: str
    access: str
    identity_method: str
    media_type: str | None = None
    version: str = "unknown"


class MetadataProvider(Protocol):
    name: str
    version: str

    def resolve(self, identifier: dict[str, Any]) -> MetadataProviderResult: ...


class SourceDiscoveryProvider(Protocol):
    name: str
    version: str

    def discover(self, work: dict[str, Any]) -> list[SourceCandidateResult]: ...


JsonTransport = Callable[[str, dict[str, str], float], tuple[dict[str, Any], bytes]]


def _default_json_transport(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> tuple[dict[str, Any], bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(10_000_001)
    except urllib.error.HTTPError as error:
        raise StorageError(
            f"Metadata provider returned HTTP {error.code}",
            code="provider_http_error",
            path=url,
        ) from error
    except urllib.error.URLError as error:
        raise StorageError(
            f"Metadata provider request failed: {error.reason}",
            code="provider_network_error",
            path=url,
        ) from error
    if len(payload) > 10_000_000:
        raise StorageError(
            "Metadata provider response exceeds 10 MB",
            code="provider_response_too_large",
            path=url,
        )
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StorageError(
            "Metadata provider returned invalid JSON",
            code="provider_response_invalid",
            path=url,
        ) from error
    if not isinstance(value, dict):
        raise StorageError(
            "Metadata provider response must be an object",
            code="provider_response_invalid",
            path=url,
        )
    return value, payload


class CrossrefProvider:
    """Crossref metadata and link discovery with an injectable transport."""

    name = "crossref"
    version = "rest-api-v1"

    def __init__(
        self,
        *,
        email: str | None = None,
        timeout: float = 30,
        transport: JsonTransport | None = None,
        publisher_access: str = "unknown",
    ) -> None:
        if publisher_access not in {"unknown", "institutional"}:
            raise StorageError(
                "Crossref publisher access must be unknown or institutional",
                code="access_class_invalid",
                path=publisher_access,
            )
        self.email = email
        self.timeout = timeout
        self.transport = transport or _default_json_transport
        self.publisher_access = publisher_access
        self._responses: dict[str, tuple[dict[str, Any], bytes, str]] = {}

    def _request(self, identifier: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
        normalized = normalize_identifier(identifier)
        if normalized["scheme"] != "doi":
            raise StorageError(
                "Crossref resolves DOI identifiers only",
                code="provider_identifier_unsupported",
                path=normalized["scheme"],
            )
        doi = normalized["normalized"]
        cached = self._responses.get(doi)
        if cached is not None:
            return cached
        endpoint = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
        agent = "libraryos/0.1"
        if self.email:
            agent += f" (mailto:{self.email})"
        response, payload = self.transport(
            endpoint,
            {"Accept": "application/json", "User-Agent": agent},
            self.timeout,
        )
        message = response.get("message")
        if not isinstance(message, dict):
            raise StorageError(
                "Crossref response has no work message",
                code="provider_response_invalid",
                path=endpoint,
            )
        result = (message, payload, endpoint)
        self._responses[doi] = result
        return result

    def resolve(self, identifier: dict[str, Any]) -> MetadataProviderResult:
        requested = normalize_identifier(identifier)
        message, payload, endpoint = self._request(requested)
        returned = normalize_identifier({"scheme": "doi", "value": message.get("DOI")})
        if identifier_key(returned) != identifier_key(requested):
            raise StorageError(
                "Crossref returned a different DOI",
                code="provider_identity_conflict",
                path=returned["normalized"],
            )
        authors: list[dict[str, str]] = []
        for author in message.get("author", []):
            if not isinstance(author, dict):
                continue
            entry = {
                key: value
                for key, value in (
                    ("family", author.get("family")),
                    ("given", author.get("given")),
                    ("orcid", author.get("ORCID")),
                )
                if isinstance(value, str) and value
            }
            if entry.get("family"):
                authors.append(entry)
        dates = (
            message.get("published-print")
            or message.get("published-online")
            or message.get("issued")
            or {}
        )
        parts = dates.get("date-parts", [[]]) if isinstance(dates, dict) else [[]]
        issued = parts[0][0] if parts and parts[0] else None
        bibliographic = {
            "title": (message.get("title") or [None])[0],
            "authors": authors,
            "container_title": (message.get("container-title") or [None])[0],
            "publisher": message.get("publisher"),
            "issued": issued,
            "type": message.get("type"),
        }
        bibliographic = {key: value for key, value in bibliographic.items() if value is not None}
        return MetadataProviderResult(
            requested_identifier=requested,
            identifiers=[returned],
            bibliographic=bibliographic,
            source_url=endpoint,
            raw_response_sha256=hashlib.sha256(payload).hexdigest(),
        )

    def discover(self, work: dict[str, Any]) -> list[SourceCandidateResult]:
        doi = next(
            (
                identifier
                for identifier in work["work"]["identifiers"]
                if identifier["scheme"] == "doi"
            ),
            None,
        )
        if doi is None:
            raise StorageError(
                "Crossref discovery requires a DOI",
                code="provider_identifier_missing",
            )
        message, _, _ = self._request(doi)
        results: list[SourceCandidateResult] = []
        for link in message.get("link", []):
            if not isinstance(link, dict) or not isinstance(link.get("URL"), str):
                continue
            media_type = link.get("content-type")
            results.append(
                SourceCandidateResult(
                    url=link["URL"],
                    access=self.publisher_access,
                    identity_method="crossref_exact_doi_link",
                    media_type=media_type if isinstance(media_type, str) else None,
                )
            )
        resource = message.get("resource")
        primary = resource.get("primary") if isinstance(resource, dict) else None
        if isinstance(primary, dict) and isinstance(primary.get("URL"), str):
            results.append(
                SourceCandidateResult(
                    url=primary["URL"],
                    access=self.publisher_access,
                    identity_method="crossref_exact_doi_resource",
                    media_type="text/html",
                )
            )
        return results
