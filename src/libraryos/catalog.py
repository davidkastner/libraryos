"""Disposable SQLite catalog rebuilt from authoritative records."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .library import open_library
from .storage import read_json_record


def rebuild_catalog(library: str | Path) -> dict[str, int]:
    """Replace the disposable catalog from authoritative JSON files."""

    root, _ = open_library(library)
    destination = root / ".libraryos" / "index.sqlite"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".index-", suffix=".sqlite", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    counts = {"works": 0, "identifiers": 0, "sources": 0, "derivatives": 0}
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE works (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT,
                    issued TEXT, manifest_path TEXT NOT NULL
                );
                CREATE TABLE identifiers (
                    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                    scheme TEXT NOT NULL, value TEXT NOT NULL, normalized TEXT,
                    UNIQUE(scheme, normalized)
                );
                CREATE TABLE artifacts (
                    id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
                    kind TEXT NOT NULL, role TEXT NOT NULL, path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, media_type TEXT NOT NULL
                );
                CREATE INDEX works_title ON works(title);
                CREATE INDEX artifacts_work ON artifacts(work_id);
                CREATE VIRTUAL TABLE prepared_text USING fts5(
                    work_id UNINDEXED, artifact_id UNINDEXED, path UNINDEXED, content
                );
                PRAGMA user_version = 1;
                """
            )
            for path in sorted((root / "works").glob("*/manifest.json")):
                record = read_json_record(path)
                work = record["work"]
                connection.execute(
                    "INSERT INTO works VALUES (?, ?, ?, ?, ?)",
                    (record["id"], work["type"], work["title"], str(work.get("issued", "")), str(path.relative_to(root))),
                )
                counts["works"] += 1
                for identifier in work["identifiers"]:
                    connection.execute(
                        "INSERT INTO identifiers VALUES (?, ?, ?, ?)",
                        (
                            record["id"],
                            identifier["scheme"],
                            identifier["value"],
                            identifier.get("normalized", identifier["value"]),
                        ),
                    )
                    counts["identifiers"] += 1
                for kind, key in (("source", "sources"), ("derivative", "derivatives")):
                    for artifact in record[key]:
                        connection.execute(
                            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                artifact["id"], record["id"], kind, artifact["role"],
                                artifact["path"], artifact["sha256"], artifact["media_type"],
                            ),
                        )
                        counts[key] += 1
                        media_type = artifact["media_type"]
                        if kind == "derivative" and (
                            media_type.startswith("text/")
                            or media_type in {"application/xml", "application/xhtml+xml"}
                        ):
                            artifact_path = path.parent / artifact["path"]
                            try:
                                content = artifact_path.read_text(
                                    encoding="utf-8", errors="replace"
                                )
                            except OSError:
                                continue
                            connection.execute(
                                "INSERT INTO prepared_text VALUES (?, ?, ?, ?)",
                                (
                                    record["id"],
                                    artifact["id"],
                                    artifact["path"],
                                    content,
                                ),
                            )
            connection.commit()
        finally:
            connection.close()
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return counts


def search_catalog(library: str | Path, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Search normalized metadata without assigning evidentiary meaning."""

    root, _ = open_library(library)
    path = root / ".libraryos" / "index.sqlite"
    if not path.is_file():
        rebuild_catalog(root)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        pattern = f"%{query.casefold()}%"
        rows = connection.execute(
            """
            SELECT DISTINCT w.*
            FROM works w LEFT JOIN identifiers i ON i.work_id = w.id
            WHERE lower(coalesce(w.title, '')) LIKE ?
               OR lower(coalesce(i.value, '')) LIKE ?
               OR lower(coalesce(i.normalized, '')) LIKE ?
            ORDER BY w.title, w.id LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            **dict(row),
            "scientific_support": "not_assessed",
            "search_scope": "metadata",
        }
        for row in rows
    ]


def search_prepared(
    library: str | Path, query: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """Navigate prepared text with exact artifact routing and no support claim."""

    root, _ = open_library(library)
    path = root / ".libraryos" / "index.sqlite"
    if not path.is_file():
        rebuild_catalog(root)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT work_id, artifact_id, path,
                   snippet(prepared_text, 3, '[', ']', ' … ', 24) AS context
            FROM prepared_text WHERE prepared_text MATCH ? LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError as error:
        raise ValueError(f"Invalid full-text query: {error}") from error
    finally:
        connection.close()
    return [
        {
            **dict(row),
            "locator": {
                "artifact_id": row["artifact_id"],
                "type": "passage",
                "value": row["context"],
            },
            "scientific_support": "not_assessed",
            "search_scope": "prepared_text",
        }
        for row in rows
    ]
