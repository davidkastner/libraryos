"""Deterministic resolution of the best available reading representation."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from .library import open_library
from .storage import StorageError, resolve_library_path
from .works import get_work

READ_REPRESENTATIONS = (
    "local_pdf",
    "source_faithful_text",
    "local_structured_source",
    "stable_source_url",
)
DEFAULT_READ_PREFERENCE = list(READ_REPRESENTATIONS)


def _artifacts(library: str | Path, work_id: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    root, _ = open_library(library)
    work = get_work(root, work_id)
    return root, work, [*work["sources"], *work["derivatives"]]


def _location(root: Path, work_id: str, artifact: dict[str, Any]) -> Path:
    return resolve_library_path(root / "works" / work_id, artifact["path"], must_exist=True)


def _page(artifact: dict[str, Any]) -> str | None:
    locator = next((x for x in artifact.get("locators", []) if x["type"] == "page"), None)
    return str(locator["value"]) if locator else None


def read_pages(
    library: str | Path, work_id: str, *, start: int = 1, end: int | None = None,
    supplement: bool = False,
) -> dict[str, Any]:
    """Return stable page-labelled text from prepared page derivatives."""
    if start < 1 or end is not None and end < start:
        raise StorageError("Invalid page range", code="read_page_range_invalid")
    root, _, artifacts = _artifacts(library, work_id)
    role = "supplement_page_text" if supplement else "page_text"
    pages = []
    for item in artifacts:
        page = _page(item)
        if item.get("role") != role or page is None or not page.isdigit():
            continue
        number = int(page)
        if number < start or end is not None and number > end:
            continue
        pages.append({
            "page_label": page, "artifact_id": item["id"], "sha256": item["sha256"],
            "text": _location(root, work_id, item).read_text(encoding="utf-8"),
            "locator": {"type": "page", "value": page},
        })
    return _navigation_result(work_id, sorted(pages, key=lambda x: int(x["page_label"])))


def search_passages(
    library: str | Path, work_id: str, *, query: str, regex: bool = False,
    context: int = 180, limit: int = 50,
) -> dict[str, Any]:
    """Search prepared page text and return exact, receipt-ready locations."""
    if not query or context < 0 or limit < 1 or limit > 1000:
        raise StorageError("Invalid passage search", code="read_search_invalid")
    flags = re.I | re.M
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as error:
        raise StorageError(str(error), code="read_search_regex_invalid") from error
    root, _, artifacts = _artifacts(library, work_id)
    hits = []
    for item in artifacts:
        if item.get("role") not in {"page_text", "supplement_page_text", "source_faithful_markdown"}:
            continue
        text = _location(root, work_id, item).read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            hits.append({
                "artifact_id": item["id"], "sha256": item["sha256"],
                "representation": item["role"], "page_label": _page(item),
                "start": match.start(), "end": match.end(),
                "passage": text[max(0, match.start() - context):match.end() + context],
            })
            if len(hits) == limit:
                return _navigation_result(work_id, hits, truncated=True)
    return _navigation_result(work_id, hits)


def list_figures(library: str | Path, work_id: str) -> dict[str, Any]:
    """Inventory prepared figures and their page/caption candidates."""
    root, _, artifacts = _artifacts(library, work_id)
    items = []
    for item in artifacts:
        if item.get("role") != "figure":
            continue
        items.append({
            "artifact_id": item["id"], "sha256": item["sha256"],
            "media_type": item["media_type"], "path": str(_location(root, work_id, item)),
            "locators": item.get("locators", []),
            "caption_candidates": [
                warning.removeprefix("caption_candidate:")
                for warning in item.get("warnings", [])
                if warning.startswith("caption_candidate:")
            ],
            "warnings": item.get("warnings", []),
        })
    return _navigation_result(work_id, items)


def list_supplements(library: str | Path, work_id: str) -> dict[str, Any]:
    """Inventory supplement sources and prepared supplement derivatives."""
    root, _, artifacts = _artifacts(library, work_id)
    items = [
        {
            "artifact_id": item["id"], "sha256": item["sha256"],
            "role": item["role"], "media_type": item["media_type"],
            "path": str(_location(root, work_id, item)),
            "locators": item.get("locators", []),
        }
        for item in artifacts
        if item.get("role") == "supplement" or item.get("role", "").startswith("supplement_")
    ]
    return _navigation_result(work_id, items)


def read_artifact(library: str | Path, work_id: str, *, artifact_id: str) -> dict[str, Any]:
    """Resolve one exact artifact without opening or interpreting it."""
    root, _, artifacts = _artifacts(library, work_id)
    item = next((x for x in artifacts if x["id"] == artifact_id), None)
    if item is None:
        raise StorageError("Artifact does not exist", code="artifact_not_found", path=artifact_id)
    return _navigation_result(work_id, [{
        "artifact_id": item["id"], "sha256": item["sha256"], "role": item["role"],
        "media_type": item["media_type"], "path": str(_location(root, work_id, item)),
        "locators": item.get("locators", []), "warnings": item.get("warnings", []),
    }])


def source_receipt(
    library: str | Path, work_id: str, *, artifact_id: str,
    locator_type: str | None = None, locator_value: str | None = None,
) -> dict[str, Any]:
    """Create a durable navigation receipt; this records no scientific review."""
    artifact = read_artifact(library, work_id, artifact_id=artifact_id)["items"][0]
    locator = (
        {"type": locator_type, "value": locator_value}
        if locator_type is not None and locator_value is not None else None
    )
    return {
        "receipt_version": 1, "work_id": work_id, "artifact_id": artifact_id,
        "sha256": artifact["sha256"], "representation": artifact["role"],
        "locator": locator, "warnings": artifact["warnings"],
        "scientific_inspection": "not_performed", "support_asserted": False,
    }


def _navigation_result(work_id: str, items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "work_id": work_id, "items": items, **extra,
        "scientific_inspection": "not_performed", "support_asserted": False,
    }


def resolve_read(
    library: str | Path,
    work_id: str,
    *,
    preference: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve a representation without opening it or creating a review."""

    root, _ = open_library(library)
    preference = list(preference or DEFAULT_READ_PREFERENCE)
    if len(preference) != len(set(preference)):
        raise StorageError(
            "Read preference cannot contain duplicate representations",
            code="read_preference_invalid",
        )
    unknown = set(preference) - set(READ_REPRESENTATIONS)
    if unknown:
        raise StorageError(
            f"Unknown read representations: {sorted(unknown)}",
            code="read_preference_invalid",
        )
    work = get_work(root, work_id)
    derivatives = work["derivatives"]
    sources = work["sources"]
    routes = {
        "local_pdf": [
            item for item in sources if item["media_type"] == "application/pdf"
        ],
        "source_faithful_text": [
            item
            for item in derivatives
            if item["role"] == "source_faithful_markdown"
            and item["quality"] in {"ready", "partial"}
        ],
        "local_structured_source": [
            item
            for item in sources
            if item["media_type"]
            in {"text/html", "application/xhtml+xml", "application/xml", "text/xml"}
        ],
    }
    urls = sorted(
        {
            item["canonical_url"]
            for item in sources
            if item.get("canonical_url")
        }
        | {item["url"] for item in work.get("source_candidates", [])}
    )
    for representation in preference:
        if representation == "stable_source_url":
            if urls:
                return {
                    "work_id": work_id,
                    "representation": representation,
                    "url": urls[0],
                    "preference": preference,
                    "opened": False,
                    "review_created": False,
                    "scientific_inspection": "not_performed",
                }
            continue
        candidates = routes[representation]
        if candidates:
            artifact = sorted(
                candidates,
                key=lambda item: (item.get("identity_status") != "verified", item["id"]),
            )[0]
            local = resolve_library_path(
                root / "works" / work_id,
                artifact["path"],
                must_exist=True,
            )
            return {
                "work_id": work_id,
                "representation": representation,
                "artifact_id": artifact["id"],
                "path": str(local),
                "media_type": artifact["media_type"],
                "preference": preference,
                "opened": False,
                "review_created": False,
                "scientific_inspection": "not_performed",
            }
    return {
        "work_id": work_id,
        "representation": "unavailable",
        "next_actions": ["discover", "import"],
        "preference": preference,
        "opened": False,
        "review_created": False,
        "scientific_inspection": "not_performed",
    }


def resolve_reads(
    library: str | Path,
    work_ids: list[str],
    *,
    preference: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve reading routes and manifests for a bounded set of works."""

    if not isinstance(work_ids, list) or len(work_ids) > 1000:
        raise StorageError(
            "work_ids must be an array of at most 1000 IDs",
            code="read_batch_invalid",
        )
    items = []
    for work_id in work_ids:
        work = get_work(library, work_id)
        items.append(
            {
                "work": work,
                "reading": resolve_read(library, work_id, preference=preference),
            }
        )
    return {
        "items": items,
        "scientific_inspection": "not_performed",
        "review_created": False,
    }


def open_read(
    library: str | Path,
    work_id: str,
    *,
    application: str = "default",
) -> dict[str, Any]:
    """Resolve and open a local PDF without recording scientific review."""

    if application not in {"default", "preview"}:
        raise StorageError(
            "Unknown reading application",
            code="read_application_invalid",
            path=application,
        )
    resolved = resolve_read(library, work_id, preference=["local_pdf"])
    if resolved["representation"] != "local_pdf":
        raise StorageError(
            "No local PDF is available for this work",
            code="local_pdf_unavailable",
            path=work_id,
        )
    path = resolved["path"]
    try:
        if sys.platform == "darwin":
            command = ["open"]
            if application == "preview":
                command.extend(["-a", "Preview"])
            subprocess.Popen(  # noqa: S603
                [*command, "--", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            opened_with = "Preview" if application == "preview" else "system default"
        elif sys.platform == "win32":
            if application == "preview":
                raise StorageError(
                    "Preview is available only on macOS",
                    code="read_application_unavailable",
                )
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
            opened_with = "system default"
        else:
            if application == "preview":
                raise StorageError(
                    "Preview is available only on macOS",
                    code="read_application_unavailable",
                )
            if not webbrowser.open(Path(path).resolve().as_uri()):
                raise OSError("The system did not accept the file")
            opened_with = "system default"
    except OSError as error:
        raise StorageError(
            "The PDF could not be opened",
            code="read_open_failed",
            path=work_id,
        ) from error
    return {
        "work_id": work_id,
        "artifact_id": resolved["artifact_id"],
        "representation": "local_pdf",
        "opened": True,
        "opened_with": opened_with,
        "review_created": False,
        "scientific_inspection": "not_performed",
    }
