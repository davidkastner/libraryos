"""Provider contracts for bibliographic assertions and source discovery."""

from __future__ import annotations

import hashlib
import json
import re
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


@dataclass(frozen=True)
class RelatedWorkResult:
    """One provider-discovered scholarly relation; never an evidence judgment."""

    relation: str
    identifiers: list[dict[str, Any]]
    title: str | None
    publication_year: int | None
    source_url: str
    provider_work_id: str


class MetadataProvider(Protocol):
    name: str
    version: str

    def resolve(self, identifier: dict[str, Any]) -> MetadataProviderResult: ...


class SourceDiscoveryProvider(Protocol):
    name: str
    version: str

    def discover(self, work: dict[str, Any]) -> list[SourceCandidateResult]: ...


class RelationDiscoveryProvider(Protocol):
    name: str
    version: str

    def discover(
        self,
        identifier: dict[str, Any],
        *,
        directions: list[str],
        limit: int,
    ) -> list[RelatedWorkResult]: ...


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


class OpenAlexProvider:
    """Backward and forward scholarly-relation discovery through OpenAlex."""

    name = "openalex"
    version = "api-v1"
    _DIRECTIONS = {"references", "citations"}

    def __init__(
        self,
        *,
        email: str | None = None,
        timeout: float = 30,
        transport: JsonTransport | None = None,
    ) -> None:
        self.email = email
        self.timeout = timeout
        self.transport = transport or _default_json_transport

    def _request(self, url: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                f"libraryos/0.1 (mailto:{self.email})"
                if self.email
                else "libraryos/0.1"
            ),
        }
        response, _ = self.transport(url, headers, self.timeout)
        return response

    @staticmethod
    def _openalex_id(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        identifier = value.rstrip("/").rsplit("/", 1)[-1]
        return identifier if re.fullmatch(r"W[1-9]\d*", identifier) else None

    @staticmethod
    def _related_work(message: dict[str, Any], relation: str) -> RelatedWorkResult | None:
        provider_id = OpenAlexProvider._openalex_id(message.get("id"))
        if provider_id is None:
            return None
        identifiers: list[dict[str, Any]] = [
            {"scheme": "openalex", "value": provider_id, "normalized": provider_id}
        ]
        doi = message.get("doi")
        if isinstance(doi, str):
            try:
                identifiers.insert(0, normalize_identifier({"scheme": "doi", "value": doi}))
            except StorageError:
                pass
        year = message.get("publication_year")
        return RelatedWorkResult(
            relation=relation,
            identifiers=identifiers,
            title=message.get("display_name") if isinstance(message.get("display_name"), str) else None,
            publication_year=year if isinstance(year, int) else None,
            source_url=f"https://api.openalex.org/works/{provider_id}",
            provider_work_id=provider_id,
        )

    def discover(
        self,
        identifier: dict[str, Any],
        *,
        directions: list[str],
        limit: int,
    ) -> list[RelatedWorkResult]:
        requested = normalize_identifier(identifier)
        if requested["scheme"] != "doi":
            raise StorageError(
                "OpenAlex relation discovery currently requires a DOI",
                code="provider_identifier_unsupported",
                path=requested["scheme"],
            )
        unknown = sorted(set(directions) - self._DIRECTIONS)
        if unknown:
            raise StorageError(
                f"Unknown relation direction: {', '.join(unknown)}",
                code="relation_direction_invalid",
                path=unknown[0],
            )
        if not directions:
            raise StorageError(
                "At least one relation direction is required",
                code="relation_direction_invalid",
            )
        if not 1 <= limit <= 100:
            raise StorageError(
                "Relation discovery limit must be between 1 and 100",
                code="relation_limit_invalid",
                path=str(limit),
            )

        doi_url = f"https://doi.org/{requested['normalized']}"
        seed_url = (
            "https://api.openalex.org/works/"
            + urllib.parse.quote(doi_url, safe="")
        )
        seed = self._request(seed_url)
        seed_id = self._openalex_id(seed.get("id"))
        if seed_id is None:
            raise StorageError(
                "OpenAlex returned a seed work without a valid work ID",
                code="provider_response_invalid",
                path=seed_url,
            )

        query_urls: list[tuple[str, str]] = []
        if "references" in directions:
            referenced_ids = [
                candidate
                for value in seed.get("referenced_works", [])[:limit]
                if (candidate := self._openalex_id(value)) is not None
            ]
            if referenced_ids:
                query_urls.append(
                    (
                        "references",
                        "https://api.openalex.org/works?filter=openalex_id:"
                        + urllib.parse.quote("|".join(referenced_ids), safe="|")
                        + f"&per-page={min(limit, len(referenced_ids))}",
                    )
                )
        if "citations" in directions:
            query_urls.append(
                (
                    "citations",
                    "https://api.openalex.org/works?filter="
                    + urllib.parse.quote(f"cites:{seed_id}", safe=":")
                    + f"&per-page={limit}",
                )
            )

        discovered: list[RelatedWorkResult] = []
        seen: set[tuple[str, str]] = set()
        for relation, url in query_urls:
            response = self._request(url)
            results = response.get("results")
            if not isinstance(results, list):
                raise StorageError(
                    "OpenAlex relation response has no results array",
                    code="provider_response_invalid",
                    path=url,
                )
            for message in results:
                if not isinstance(message, dict):
                    continue
                item = self._related_work(message, relation)
                if item is None or (relation, item.provider_work_id) in seen:
                    continue
                seen.add((relation, item.provider_work_id))
                discovered.append(item)
        return discovered
