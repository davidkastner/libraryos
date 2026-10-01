"""Conservative manuscript-readiness classification for work manifests."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .storage import resolve_library_path

MANUSCRIPT_STATUSES = (
    "manuscript_ready",
    "source_acquired_not_prepared",
    "browser_retrieval_required",
    "unavailable",
)

_NON_ARTICLE_ROLE_PARTS = {
    "abstract",
    "citation",
    "data",
    "dataset",
    "figure",
    "landing",
    "metadata",
    "supplement",
    "supporting",
}
_ARTICLE_ROLES = {
    "article",
    "article_pdf",
    "article_full_text",
    "accepted_manuscript",
    "author_manuscript",
    "author_manuscript_html",
    "author_manuscript_pdf",
    "authoritative_full_text",
    "full_text",
    "full_text_html",
    "fulltext",
    "fulltext_html",
    "main_article",
    "native_jats",
    "open_access_fulltext",
    "open_access_fulltext_html",
    "open_access_fulltext_xml",
    "pmc_html",
    "preprint",
    "primary",
    "primary_article",
    "primary_full_text",
    "primary_fulltext",
    "primary_source",
    "publisher_full_text",
    "publisher_fulltext",
    "publisher_html",
    "publisher_pdf",
    "publisher_version",
    "repository_copy",
    "repository_fulltext",
    "repository_html",
    "repository_pdf",
    "repository_version",
    "source_pdf",
    "source_text",
    "version_of_record",
}
_STRUCTURED_MEDIA_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
}


def _role_is_article(role: object) -> bool:
    normalized = str(role or "").strip().casefold().replace("-", "_")
    parts = set(filter(None, re.split(r"[^a-z0-9]+", normalized)))
    if parts & _NON_ARTICLE_ROLE_PARTS:
        return False
    return normalized in _ARTICLE_ROLES or normalized.startswith("article_")


def _structured_has_article_body(path: Path, media_type: str) -> bool:
    """Reject metadata/abstract/landing records even if their role says full text."""

    try:
        data = path.read_bytes()
    except OSError:
        return False
    if not data:
        return False
    sample = data[:4_000_000]
    if media_type in {"text/html", "application/xhtml+xml"}:
        text = sample.decode("utf-8", errors="ignore")
        if re.search(r"<\s*(article|main)\b", text, re.I):
            return True
        return bool(
            re.search(r"<\s*(section|div)\b[^>]*(?:article-body|full[-_ ]?text)", text, re.I)
        )
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return False
    names = [element.tag.rsplit("}", 1)[-1].casefold() for element in root.iter()]
    if "body" in names:
        body = next(
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].casefold() == "body"
        )
        return bool(" ".join(body.itertext()).strip())
    # BioC full text has non-front/non-abstract passages; metadata-only exports do not.
    for passage in (
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1].casefold() == "passage"
    ):
        infons = {
            child.attrib.get("key", "").casefold(): (child.text or "").strip().casefold()
            for child in passage
            if child.tag.rsplit("}", 1)[-1].casefold() == "infon"
        }
        kind = infons.get("type", "")
        section = infons.get("section_type", "")
        if kind not in {"", "front", "title", "abstract"} or section not in {
            "",
            "front",
            "abstract",
        }:
            return True
    return False


def _doi_url(manifest: dict[str, Any]) -> str | None:
    for identifier in manifest["work"].get("identifiers", []):
        if identifier.get("scheme") == "doi":
            value = identifier.get("normalized", identifier.get("value"))
            if isinstance(value, str) and value:
                return f"https://doi.org/{value}"
    return None


def classify_manuscript(bundle: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Project one manifest into a strict, agent-safe manuscript state."""

    article_pdfs: list[dict[str, Any]] = []
    structured_sources: list[dict[str, Any]] = []
    acquired_article_sources: list[dict[str, Any]] = []
    verified_article_hashes: set[str] = set()
    for source in manifest["sources"]:
        if not _role_is_article(source.get("role")):
            continue
        media_type = str(source.get("media_type") or "").casefold().split(";", 1)[0]
        if media_type == "application/pdf":
            if source.get("identity_status") == "verified":
                article_pdfs.append(source)
                verified_article_hashes.add(source["sha256"])
            acquired_article_sources.append(source)
        elif media_type in _STRUCTURED_MEDIA_TYPES:
            path = resolve_library_path(bundle, source["path"], must_exist=False)
            has_body = path.is_file() and _structured_has_article_body(path, media_type)
            if has_body:
                acquired_article_sources.append(source)
                if source.get("identity_status") == "verified":
                    structured_sources.append(source)
                    verified_article_hashes.add(source["sha256"])
        else:
            acquired_article_sources.append(source)
            if source.get("identity_status") == "verified":
                verified_article_hashes.add(source["sha256"])
    prepared_text = [
        derivative
        for derivative in manifest["derivatives"]
        if derivative.get("role") == "source_faithful_markdown"
        and derivative.get("quality") in {"ready", "partial"}
        and "full_text_not_present" not in derivative.get("warnings", [])
        and bool(set(derivative.get("input_sha256", [])) & verified_article_hashes)
    ]
    if article_pdfs or prepared_text or structured_sources:
        return {
            "status": "manuscript_ready",
            "readable": True,
            "reason": (
                "A verified local article representation is available; this does not "
                "mean its scientific contents were inspected."
            ),
            "article_pdf_ids": [item["id"] for item in article_pdfs],
            "prepared_text_ids": [item["id"] for item in prepared_text],
            "structured_source_ids": [item["id"] for item in structured_sources],
            "retrieval_url": None,
        }
    if acquired_article_sources:
        return {
            "status": "source_acquired_not_prepared",
            "readable": False,
            "reason": (
                "Article source bytes are present, but no verified readable article "
                "representation is ready."
            ),
            "article_pdf_ids": [],
            "prepared_text_ids": [],
            "structured_source_ids": [],
            "retrieval_url": None,
        }
    urls = sorted(
        {
            str(source["canonical_url"])
            for source in manifest["sources"]
            if source.get("canonical_url")
        }
        | {
            str(candidate["url"])
            for candidate in manifest.get("source_candidates", [])
            if candidate.get("url")
        }
    )
    doi_url = _doi_url(manifest)
    if doi_url or urls:
        return {
            "status": "browser_retrieval_required",
            "readable": False,
            "reason": (
                "No local manuscript is readable. Use an authorized browser session "
                "to retrieve the exact article, then import and prepare it."
            ),
            "article_pdf_ids": [],
            "prepared_text_ids": [],
            "structured_source_ids": [],
            # Prefer the canonical DOI resolver to metadata/API endpoints recorded
            # during discovery. It is the most reliable browser starting point.
            "retrieval_url": doi_url or sorted(set(urls))[0],
        }
    return {
        "status": "unavailable",
        "readable": False,
        "reason": "No local manuscript or stable retrieval route is known.",
        "article_pdf_ids": [],
        "prepared_text_ids": [],
        "structured_source_ids": [],
        "retrieval_url": None,
    }
