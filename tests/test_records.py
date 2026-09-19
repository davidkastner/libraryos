import uuid
from pathlib import Path

import pytest

from libraryos import (
    StorageError,
    archive_collection,
    archive_collection_preview,
    create_collection,
    create_work,
    import_source,
    initialize_library,
    purge_preview,
    purge_staged_removal,
    register_external_collection,
    restore_collection,
    restore_staged_removal,
    stage_collection_removal,
    work_reachability,
    write_assessment,
    write_review,
)


def test_review_and_assessment_remain_distinct(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Evidence")
    source_path = tmp_path / "paper.txt"
    source_path.write_text("source evidence")
    source = import_source(root, work["id"], source_path, role="full_text")["source"]
    collection = create_collection(root, collection_id="project-a", title="Project A")

    review = {
        "schema": "https://libraryos.dev/schemas/review/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "work_id": work["id"],
        "source_sha256": [source["sha256"]],
        "reviewer": {"kind": "human", "id": "researcher"},
        "purpose": "Check methods",
        "coverage": [{"artifact_id": source["id"], "type": "page", "value": 1}],
        "completed_at": "2026-09-19T12:00:00Z",
    }
    assert write_review(root, review)["purpose"] == "Check methods"
    with pytest.raises(StorageError, match="immutable"):
        write_review(root, review)

    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": collection["id"],
        "subject": {"kind": "work", "id": work["id"]},
        "author": {"kind": "agent", "id": "test-agent"},
        "purpose": "Project relevance",
        "summary": "Relevant to this project only.",
        "review_ids": [review["id"]],
        "created_at": "2026-09-19T12:01:00Z",
    }
    assert write_assessment(root, assessment)["collection_id"] == "project-a"


def test_lifecycle_previews_never_claim_shared_deletion(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    create_collection(root, collection_id="temporary", title="Temporary")
    archive = archive_collection_preview(root, "temporary")
    purge = purge_preview(root, "temporary")
    assert archive["works_deleted"] == []
    assert purge["sources_deleted"] == []
    assert purge["applied"] is False


def test_archive_and_restore_are_reversible_and_preserve_evidence(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Shared evidence")
    source_path = tmp_path / "paper.txt"
    source_path.write_text("evidence")
    source = import_source(root, work["id"], source_path, role="full_text")["source"]
    collection = create_collection(root, collection_id="project-a", title="Project A")
    collection["members"] = [{"work": work["id"]}]
    from libraryos import put_collection

    put_collection(root, collection)

    archived = archive_collection(root, "project-a")
    snapshot_path = root / archived["snapshot"]["path"]
    assert archived["collection"]["status"] == "archived"
    assert archived["snapshot"]["created"] is True
    assert snapshot_path.is_file()
    assert archived["works_deleted"] == []
    assert archived["sources_deleted"] == []
    assert Path(root / "works" / work["id"] / source["path"]).is_file()

    restored = restore_collection(root, "project-a")
    assert restored["collection"]["status"] == "active"
    assert snapshot_path.is_file()
    assert restore_collection(root, "project-a")["changed"] is False


def test_external_archive_requires_compare_and_swap_hash(tmp_path):
    import json

    root = tmp_path / "library"
    initialize_library(root)
    project = tmp_path / "project"
    project.mkdir()
    collection_path = project / "collection.json"
    collection_path.write_text(
        json.dumps(
            {
                "schema": "https://libraryos.dev/schemas/collection/v1",
                "schema_version": 1,
                "id": "external",
                "title": "External",
                "status": "active",
                "members": [],
            }
        )
    )
    registration = register_external_collection(root, collection_path)["registration"]

    with pytest.raises(StorageError) as missing:
        archive_collection(root, "external")
    assert missing.value.code == "collection_expected_hash_required"
    archived = archive_collection(
        root,
        "external",
        expected_sha256=registration["sha256"],
    )
    assert archived["collection"]["status"] == "archived"
    assert json.loads(collection_path.read_text())["status"] == "archived"
    with pytest.raises(StorageError) as stale:
        restore_collection(
            root,
            "external",
            expected_sha256=registration["sha256"],
        )
    assert stale.value.code == "collection_external_conflict"


def test_collection_removal_is_staged_and_recoverable(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Shared")
    collection = create_collection(root, collection_id="temporary", title="Temporary")
    collection["members"] = [{"work": work["id"]}]
    from libraryos import put_collection

    put_collection(root, collection)
    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": "temporary",
        "subject": {"kind": "work", "id": work["id"]},
        "author": {"kind": "human", "id": "researcher"},
        "purpose": "temporary",
        "summary": "Project-scoped only",
        "created_at": "2026-09-19T12:00:00Z",
    }
    write_assessment(root, assessment)
    preview = purge_preview(root, "temporary")

    with pytest.raises(StorageError) as stale:
        stage_collection_removal(
            root,
            "temporary",
            expected_preview=["collections/temporary.json"],
        )
    assert stale.value.code == "purge_preview_stale"
    staged = stage_collection_removal(
        root,
        "temporary",
        expected_preview=preview["records_deleted"],
    )
    assert staged["works_deleted"] == []
    assert staged["sources_deleted"] == []
    assert not (root / "collections" / "temporary.json").exists()
    assert (root / "works" / work["id"] / "manifest.json").is_file()
    transaction = staged["transaction"]
    assert transaction["state"] == "staged"
    assert transaction["purge_after"] > transaction["staged_at"]

    restored = restore_staged_removal(root, transaction["id"])
    assert restored["transaction"]["state"] == "restored"
    assert (root / "collections" / "temporary.json").is_file()
    assert (root / "records" / "assessments" / f"{assessment['id']}.json").is_file()


def test_permanent_purge_is_separately_confirmed_and_audited(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    create_collection(root, collection_id="temporary", title="Temporary")
    preview = purge_preview(root, "temporary")
    staged = stage_collection_removal(
        root,
        "temporary",
        expected_preview=preview["records_deleted"],
    )
    transaction_id = staged["transaction"]["id"]

    with pytest.raises(StorageError) as wrong:
        purge_staged_removal(
            root,
            transaction_id,
            confirm_transaction_id="not-the-id",
        )
    assert wrong.value.code == "purge_confirmation_invalid"
    with pytest.raises(StorageError) as retained:
        purge_staged_removal(
            root,
            transaction_id,
            confirm_transaction_id=transaction_id,
        )
    assert retained.value.code == "trash_retention_active"

    purged = purge_staged_removal(
        root,
        transaction_id,
        confirm_transaction_id=transaction_id,
        allow_before_retention=True,
    )
    assert purged["transaction"]["state"] == "purged"
    assert purged["recoverable"] is False
    assert not (root / ".libraryos" / "trash" / transaction_id).exists()
    assert (root / purged["audit_record"]).is_file()


def test_work_reachability_is_conservative_and_read_only(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    member = create_work(root, work_type="article", title="Member")
    reviewed = create_work(root, work_type="article", title="Reviewed")
    related = create_work(root, work_type="preprint", title="Related")
    orphan = create_work(
        root,
        work_type="report",
        title="Unreferenced",
        relations=[{"type": "references", "target": related["id"]}],
    )
    collection = create_collection(root, collection_id="project-a", title="Project A")
    collection["members"] = [{"work": member["id"]}]
    from libraryos import put_collection

    put_collection(root, collection)
    source_path = tmp_path / "review.txt"
    source_path.write_text("review source")
    source = import_source(root, reviewed["id"], source_path, role="full_text")["source"]
    write_review(
        root,
        {
            "schema": "https://libraryos.dev/schemas/review/v1",
            "schema_version": 1,
            "id": str(uuid.uuid4()),
            "work_id": reviewed["id"],
            "source_sha256": [source["sha256"]],
            "reviewer": {"kind": "human", "id": "researcher"},
            "purpose": "retain",
            "coverage": [
                {"artifact_id": source["id"], "type": "whole_artifact", "value": "all"}
            ],
            "completed_at": "2026-09-19T12:00:00Z",
        },
    )
    truly_unreferenced = create_work(root, work_type="book", title="Candidate")

    report = work_reachability(root)
    by_id = {item["work_id"]: item for item in report["works"]}
    assert {item["kind"] for item in by_id[member["id"]]["reasons"]} == {"collection"}
    assert {item["kind"] for item in by_id[reviewed["id"]]["reasons"]} == {"review"}
    assert {item["kind"] for item in by_id[related["id"]]["reasons"]} == {"relation"}
    assert {item["kind"] for item in by_id[orphan["id"]]["reasons"]} == {"relation"}
    assert report["unreferenced_candidates"] == [truly_unreferenced["id"]]
    assert report["applied"] is False
