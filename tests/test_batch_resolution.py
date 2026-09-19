from libraryos import create_work, initialize_library
from libraryos.operations import invoke


def test_identifier_batch_resolves_creates_and_preserves_invalid_items(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    existing = create_work(
        root,
        work_type="article",
        title="Existing",
        identifiers=[{"scheme": "doi", "value": "10.1000/existing"}],
    )
    result = invoke(
        "work.resolve_identifiers",
        {
            "library": root,
            "identifiers": [
                {"scheme": "doi", "value": "https://doi.org/10.1000/EXISTING"},
                {"scheme": "doi", "value": "10.1000/new"},
                {"scheme": "doi", "value": "invalid"},
            ],
            "create_missing": True,
        },
    )["result"]
    assert result["items"][0]["work_id"] == existing["id"]
    assert [item["status"] for item in result["items"]] == [
        "resolved",
        "created",
        "invalid",
    ]
    assert result["scientific_inspection"] == "not_performed"


def test_read_batch_returns_full_navigation_manifests_without_review(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    first = create_work(root, work_type="article", title="First")
    second = create_work(root, work_type="report", title="Second")
    result = invoke(
        "read.resolve_many",
        {"library": root, "work_ids": [first["id"], second["id"]]},
    )["result"]
    assert [item["work"]["id"] for item in result["items"]] == [
        first["id"],
        second["id"],
    ]
    assert all(
        item["reading"]["scientific_inspection"] == "not_performed"
        for item in result["items"]
    )
    assert result["review_created"] is False
