import uuid

from libraryos import (
    collection_queues,
    create_collection,
    create_work,
    import_source,
    initialize_library,
    prepare_source,
    put_collection,
    write_review,
)
from libraryos.operations import invoke


def _review(work, source, *, purpose):
    return {
        "schema": "https://libraryos.dev/schemas/review/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "work_id": work["id"],
        "source_sha256": [source["sha256"]],
        "reviewer": {"kind": "human", "id": "researcher"},
        "purpose": purpose,
        "coverage": [
            {
                "artifact_id": source["id"],
                "type": "whole_artifact",
                "value": "entire source",
            }
        ],
        "completed_at": "2026-09-19T12:00:00Z",
    }


def test_collection_queues_keep_source_preparation_and_review_distinct(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    no_source = create_work(root, work_type="article", title="No source")
    source_only = create_work(root, work_type="article", title="Source only")
    prepared = create_work(root, work_type="article", title="Prepared")
    reviewed = create_work(root, work_type="article", title="Reviewed")

    sources = {}
    for label, work in (("source", source_only), ("prepared", prepared), ("reviewed", reviewed)):
        path = tmp_path / f"{label}.txt"
        path.write_text(f"{label} evidence")
        sources[label] = import_source(
            root, work["id"], path, role="full_text"
        )["source"]
    prepare_source(root, prepared["id"], sources["prepared"]["id"])
    prepare_source(root, reviewed["id"], sources["reviewed"]["id"])
    write_review(root, _review(reviewed, sources["reviewed"], purpose="scope-review"))

    collection = create_collection(root, collection_id="project-a", title="Project A")
    collection["policy"] = {
        "citation_requires_local_source": True,
        "required_review_purpose": "scope-review",
    }
    collection["members"] = [
        {"work": no_source["id"]},
        {"work": source_only["id"]},
        {"work": prepared["id"]},
        {"work": reviewed["id"]},
    ]
    put_collection(root, collection)

    result = collection_queues(root, "project-a")
    queues = {
        name: [item["work_id"] for item in items]
        for name, items in result["queues"].items()
    }
    assert queues["needs_source"] == [no_source["id"]]
    assert queues["needs_preparation"] == [source_only["id"]]
    assert queues["needs_review"] == [
        no_source["id"],
        source_only["id"],
        prepared["id"],
    ]
    assert queues["exceptions"] == []
    assert queues["ready"] == [reviewed["id"]]
    assert result["policy_compliant"] is False
    assert all(
        member["state"]["scientific_support"] == "not_assessed"
        for member in result["members"]
    )
    assert result["scientific_assessment"] == "not_performed"


def test_collection_queue_policy_is_exact_and_collection_scoped(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Evidence")
    path = tmp_path / "report.txt"
    path.write_text("evidence")
    source = import_source(root, work["id"], path, role="full_text")["source"]
    prepare_source(root, work["id"], source["id"])
    write_review(root, _review(work, source, purpose="different-purpose"))
    collection = create_collection(root, collection_id="project-a", title="Project A")
    collection["policy"] = {"required_review_purpose": "required-purpose"}
    collection["members"] = [{"work": work["id"]}]
    put_collection(root, collection)

    result = invoke(
        "collection.queues",
        {"library": root, "collection_id": "project-a"},
        capabilities=["collection.read:project-a"],
    )["result"]
    assert result["counts"]["needs_review"] == 1
    reason = result["queues"]["needs_review"][0]["reasons"]["needs_review"][0]
    assert reason["required_purpose"] == "required-purpose"
    assert result["counts"]["needs_source"] == 0
    assert result["counts"]["needs_preparation"] == 0


def test_unresolved_identifier_is_an_exception_not_a_support_claim(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    collection = create_collection(root, collection_id="project-a", title="Project A")
    collection["members"] = [
        {
            "work": {
                "identifier": {
                    "scheme": "doi",
                    "value": "10.1000/not-local",
                    "normalized": "10.1000/not-local",
                }
            }
        }
    ]
    put_collection(root, collection)

    result = collection_queues(root, "project-a")
    member = result["queues"]["exceptions"][0]
    assert member["work_id"] is None
    assert member["reasons"]["exceptions"][0]["code"] == "work_reference_unresolved"
    assert member["state"]["scientific_support"] == "not_assessed"
    assert result["counts"] == {
        "needs_source": 0,
        "needs_preparation": 0,
        "needs_review": 0,
        "exceptions": 1,
        "ready": 0,
    }
