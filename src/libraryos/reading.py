"""Deterministic resolution of the best available reading representation."""

from __future__ import annotations

import os
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
