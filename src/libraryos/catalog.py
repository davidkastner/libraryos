"""Disposable SQLite catalog rebuilt from authoritative records."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .library import open_library
from .storage import read_json_record

CATALOG_VERSION = 2


def _warning_count(record: dict[str, Any]) -> int:
    return len(record.get("warnings", [])) + sum(
        len(artifact.get("warnings", []))
        for artifact in [*record["sources"], *record["derivatives"]]
    )


def _author_label(author: object) -> str:
    if isinstance(author, str):
        return author
    if not isinstance(author, dict):
        return ""
    if author.get("literal"):
        return str(author["literal"])
    return " ".join(
        str(author[field]).strip()
        for field in ("given", "family")
        if author.get(field)
    )


def _insert_work(
    connection: sqlite3.Connection,
    root: Path,
    record: dict[str, Any],
) -> None:
    work = record["work"]
    authors = [_author_label(author) for author in work.get("authors", [])]
    identifiers = work["identifiers"]
    sources = record["sources"]
    searchable = " ".join(
        [
            str(work.get("title") or ""),
            *authors,
            str(work.get("container_title") or ""),
            str(work.get("issued") or ""),
            work["type"],
            *(f"{item['scheme']} {item.get('normalized', item['value'])}" for item in identifiers),
        ]
    ).casefold()
    connection.execute(
        """
        INSERT INTO works (
            id, type, title, issued, authors_json, container_title,
            created_at, updated_at, source_count, pdf_count,
            derivative_count, warning_count, search_text, manifest_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type=excluded.type, title=excluded.title, issued=excluded.issued,
            authors_json=excluded.authors_json,
            container_title=excluded.container_title,
            created_at=excluded.created_at, updated_at=excluded.updated_at,
            source_count=excluded.source_count, pdf_count=excluded.pdf_count,
            derivative_count=excluded.derivative_count,
            warning_count=excluded.warning_count,
            search_text=excluded.search_text, manifest_path=excluded.manifest_path
        """,
        (
            record["id"],
            work["type"],
            work.get("title"),
            str(work.get("issued", "")),
            json.dumps(authors, ensure_ascii=False),
            work.get("container_title"),
            record["created_at"],
            record["updated_at"],
            len(sources),
            sum(source["media_type"] == "application/pdf" for source in sources),
            len(record["derivatives"]),
            _warning_count(record),
            searchable,
            str((root / "works" / record["id"] / "manifest.json").relative_to(root)),
        ),
    )
    connection.execute("DELETE FROM identifiers WHERE work_id = ?", (record["id"],))
    connection.executemany(
        "INSERT INTO identifiers VALUES (?, ?, ?, ?)",
        [
            (
                record["id"],
                identifier["scheme"],
                identifier["value"],
                identifier.get("normalized", identifier["value"]),
            )
            for identifier in identifiers
        ],
    )


def _insert_collection(connection: sqlite3.Connection, collection: dict[str, Any]) -> None:
    collection_id = collection["id"]
    connection.execute(
        "INSERT OR REPLACE INTO collections (id, title, status) VALUES (?, ?, ?)",
        (collection_id, collection["title"], collection["status"]),
    )
    connection.execute(
        "DELETE FROM collection_members WHERE collection_id = ?", (collection_id,)
    )
    for position, member in enumerate(collection["members"]):
        reference = member["work"]
        if isinstance(reference, str):
            work_id = reference
        else:
            identifier = reference["identifier"]
            normalized = identifier.get("normalized", identifier["value"])
            row = connection.execute(
                """
                SELECT work_id FROM identifiers
                WHERE scheme = ? AND normalized = ?
                """,
                (identifier["scheme"], normalized),
            ).fetchone()
            if row is None:
                continue
            work_id = row[0]
        connection.execute(
            """
            INSERT INTO collection_members
                (collection_id, work_id, member_order, position)
            VALUES (?, ?, ?, ?)
            """,
            (collection_id, work_id, member.get("order"), position),
        )


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
                    issued TEXT, authors_json TEXT NOT NULL,
                    container_title TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, source_count INTEGER NOT NULL,
                    pdf_count INTEGER NOT NULL, derivative_count INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL, search_text TEXT NOT NULL,
                    manifest_path TEXT NOT NULL
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
                CREATE INDEX works_recent ON works(created_at DESC, id DESC);
                CREATE INDEX works_year ON works(issued DESC, title DESC, id DESC);
                CREATE INDEX artifacts_work ON artifacts(work_id);
                CREATE TABLE collections (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE collection_members (
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                    member_order REAL, position INTEGER NOT NULL,
                    PRIMARY KEY(collection_id, work_id)
                );
                CREATE INDEX collection_members_work ON collection_members(work_id);
                CREATE INDEX collection_members_order
                    ON collection_members(collection_id, member_order, position);
                CREATE VIRTUAL TABLE prepared_text USING fts5(
                    work_id UNINDEXED, artifact_id UNINDEXED, path UNINDEXED, content
                );
                PRAGMA user_version = 2;
                """
            )
            for path in sorted((root / "works").glob("*/manifest.json")):
                record = read_json_record(path)
                _insert_work(connection, root, record)
                counts["works"] += 1
                work = record["work"]
                for identifier in work["identifiers"]:
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
            from .collections import list_collections

            for entry in list_collections(root):
                if "collection" in entry:
                    _insert_collection(connection, entry["collection"])
            connection.commit()
        finally:
            connection.close()
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return counts


def ensure_catalog(library: str | Path) -> tuple[Path, Path]:
    """Return the current catalog, rebuilding an absent or obsolete version."""

    root, _ = open_library(library)
    path = root / ".libraryos" / "index.sqlite"
    rebuild = not path.is_file()
    if not rebuild:
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
                rebuild = connection.execute("PRAGMA user_version").fetchone()[0] != CATALOG_VERSION
        except sqlite3.DatabaseError:
            rebuild = True
    if rebuild:
        rebuild_catalog(root)
    return root, path


def index_work_if_present(library: str | Path, record: dict[str, Any]) -> None:
    """Synchronize one work when a disposable catalog already exists."""

    root, _ = open_library(library)
    path = root / ".libraryos" / "index.sqlite"
    if not path.is_file():
        return
    with sqlite3.connect(path) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != CATALOG_VERSION:
            return
        connection.execute("PRAGMA foreign_keys = ON")
        _insert_work(connection, root, record)


def index_collection_if_present(library: str | Path, collection: dict[str, Any]) -> None:
    """Synchronize one collection when a disposable catalog already exists."""

    root, _ = open_library(library)
    path = root / ".libraryos" / "index.sqlite"
    if not path.is_file():
        return
    with sqlite3.connect(path) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != CATALOG_VERSION:
            return
        connection.execute("PRAGMA foreign_keys = ON")
        _insert_collection(connection, collection)


def search_catalog(library: str | Path, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Search normalized metadata without assigning evidentiary meaning."""

    root, _ = open_library(library)
    root, path = ensure_catalog(root)
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
    root, path = ensure_catalog(root)
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
