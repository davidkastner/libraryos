"""Bounded privacy checks for repositories, exports, and library metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .storage import StorageError

_SKIP_DIRECTORIES = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
_TEXT_SUFFIXES = {
    "",
    ".bib",
    ".csv",
    ".json",
    ".md",
    ".py",
    ".ris",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+\S+"),
}
_URL = re.compile(r"https?://[^\s<>\"']+")
_ABSOLUTE_USER_PATH = re.compile(
    rf"(?:/{'Users'}/|/{'home'}/)[^/\s]+/"
)
_SENSITIVE_QUERY_KEYS = re.compile(
    r"(?i)(?:token|key|signature|credential|authorization|x-amz-[^=]*)="
)


def scan_privacy(
    path: str | Path,
    *,
    allow_publication_bytes: bool = False,
    max_text_bytes: int = 2_000_000,
) -> dict[str, Any]:
    """Scan a tree without emitting matched secret values or publication contents."""

    target = Path(path).expanduser().absolute()
    if not target.exists():
        raise StorageError(
            "Privacy scan target does not exist",
            code="privacy_target_missing",
            path=str(target),
        )
    if max_text_bytes < 1:
        raise StorageError(
            "Privacy scan text limit must be positive",
            code="privacy_limit_invalid",
        )
    candidates = [target] if target.is_file() else sorted(target.rglob("*"))
    findings: list[dict[str, Any]] = []
    scanned = 0
    skipped = 0
    for candidate in candidates:
        relative = candidate.relative_to(target) if target.is_dir() else Path(candidate.name)
        if any(part in _SKIP_DIRECTORIES for part in relative.parts):
            continue
        if candidate.is_symlink():
            findings.append(
                {
                    "code": "privacy_symlink",
                    "path": str(relative),
                    "message": "Symlink content was not followed during privacy scanning.",
                }
            )
            continue
        if not candidate.is_file():
            continue
        scanned += 1
        if candidate.suffix.casefold() == ".pdf" and not allow_publication_bytes:
            findings.append(
                {
                    "code": "publication_bytes_present",
                    "path": str(relative),
                    "message": "PDF bytes are present in a publication-free scan target.",
                }
            )
            continue
        if candidate.suffix.casefold() not in _TEXT_SUFFIXES:
            skipped += 1
            continue
        if candidate.stat().st_size > max_text_bytes:
            skipped += 1
            findings.append(
                {
                    "code": "privacy_text_limit_exceeded",
                    "path": str(relative),
                    "message": "Text-like file exceeded the bounded scan size.",
                }
            )
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeError:
            skipped += 1
            continue
        for code, pattern in _SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(
                    {
                        "code": code,
                        "path": str(relative),
                        "message": "Potential secret material matched a protected pattern.",
                    }
                )
        if _ABSOLUTE_USER_PATH.search(text):
            findings.append(
                {
                    "code": "user_path_present",
                    "path": str(relative),
                    "message": "An absolute user-home path may disclose local identity.",
                }
            )
        urls = [
            urlsplit(match.group(0).rstrip(".,);"))
            for match in _URL.finditer(text)
        ]
        if any(
            url.username is not None
            or bool(_SENSITIVE_QUERY_KEYS.search(url.query))
            for url in urls
        ):
            findings.append(
                {
                    "code": "sensitive_url_present",
                    "path": str(relative),
                    "message": "A URL contains user information, a query, or a fragment.",
                }
            )
    findings.sort(key=lambda item: (item["path"], item["code"]))
    return {
        "path": str(target),
        "valid": not findings,
        "counts": {
            "files_scanned": scanned,
            "files_skipped": skipped,
            "findings": len(findings),
        },
        "findings": findings,
        "contents_emitted": False,
    }
