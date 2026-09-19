import json
import sqlite3

from libraryos import (
    audit_legacy_library,
    list_legacy_works,
    preview_legacy_migration,
    rehearse_legacy_migration,
    show_legacy_work,
)


def _legacy_library(root):
    (root / "works" / "doi-example").mkdir(parents=True)
    (root / "library.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": "2026-09-16T00:00:00+00:00",
                "description": "Legacy test library",
            }
        )
    )
    source = root / "works" / "doi-example" / "source" / "article.txt"
    source.parent.mkdir()
    source.write_text("redistributable fixture\n")
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "work": {"doi": "10.1234/example"},
        "files": [
            {
                "category": "source",
                "role": "source_text",
                "path": "source/article.txt",
                "sha256": digest,
                "bytes": source.stat().st_size,
            }
        ],
        "attempts": [{"status": "success"}],
        "exceptions": [],
    }
    (root / "works" / "doi-example" / "manifest.json").write_text(json.dumps(manifest))
    inventory = {
        "schema_version": 1,
        "entries": [],
        "occurrences": [
            {
                "source_path": "records/example.json",
                "source_sha256": "0" * 64,
                "reference_id": 1,
                "normalized_doi": "10.1234/example",
                "normalization_error": None,
            }
        ],
    }
    (root / "inventory.json").write_text(json.dumps(inventory))

    connection = sqlite3.connect(root / "index.sqlite")
    for table in ("works", "occurrences", "sources", "artifacts", "attempts", "exceptions"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for table in ("works", "occurrences", "sources", "attempts"):
        connection.execute(f"INSERT INTO {table} DEFAULT VALUES")
    connection.commit()
    connection.close()


def test_legacy_audit_is_read_only_and_matches_index(tmp_path):
    root = tmp_path / "legacy"
    _legacy_library(root)
    # Exercise the WAL sidecars that SQLite may otherwise mutate on a read-only
    # connection. They are part of the legacy runtime state and must be untouched.
    connection = sqlite3.connect(root / "index.sqlite")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    before = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}

    result = audit_legacy_library(root)

    after = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    assert result["valid"] is True
    assert result["mutation"] is False
    assert result["manifests"] == 1
    assert result["manifest_files_checked"] == 1
    assert result["expected_index_rows"]["occurrences"] == 1
    assert before == after


def test_legacy_audit_reports_index_discrepancy(tmp_path):
    root = tmp_path / "legacy"
    _legacy_library(root)
    connection = sqlite3.connect(root / "index.sqlite")
    connection.execute("DELETE FROM sources")
    connection.commit()
    connection.close()

    result = audit_legacy_library(root, verify_hashes=False)

    assert result["valid"] is False
    assert result["findings"][0]["code"] == "legacy_index_parity_mismatch"


def test_legacy_list_search_and_show_are_read_only(tmp_path):
    root = tmp_path / "legacy"
    _legacy_library(root)
    before = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    results = list_legacy_works(root, query="10.1234", limit=1)
    shown = show_legacy_work(root, "10.1234/example")
    after = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    assert results[0]["doi"] == "10.1234/example"
    assert results[0]["scientific_inspection"] == "not_assessed"
    assert shown["work"]["doi"] == "10.1234/example"
    assert before == after


def test_legacy_migration_preview_has_no_effects(tmp_path):
    root = tmp_path / "legacy"
    _legacy_library(root)
    before = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    preview = preview_legacy_migration(root)
    after = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    assert preview["ready"] is True
    assert preview["applied"] is False
    assert preview["counts"]["sources"] == 1
    assert preview["mapping"]["source_bytes"] == "not_moved_or_rewritten"
    assert before == after


def test_legacy_migration_rehearsal_preserves_records_hashes_and_source(tmp_path):
    root = tmp_path / "legacy"
    _legacy_library(root)
    before = {
        path.relative_to(root): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }
    destination = tmp_path / "rehearsal"

    result = rehearse_legacy_migration(root, destination)

    after = {
        path.relative_to(root): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }
    assert result["passed"] is True
    assert result["source_mutation"] is False
    assert result["source_bytes_moved"] is False
    assert result["comparison"] == {
        "works": {"legacy": 1, "migrated": 1},
        "sources": {"legacy": 1, "migrated": 1},
        "derivatives": {"legacy": 0, "migrated": 0},
        "occurrences": {"legacy": 1, "migrated": 1},
        "file_hash_multiset_equal": True,
    }
    assert before == after
