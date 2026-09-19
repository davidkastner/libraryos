"""Deterministic resolution of the best available reading representation."""

from __future__ import annotations

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
