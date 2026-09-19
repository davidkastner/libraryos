import hashlib

import pytest

from libraryos import create_work, initialize_library, validate_library
from libraryos.operations import invoke
from libraryos.storage import StorageError


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _arguments(root, work_id, *, new_hash, expected=None):
    return {
        "library": root,
        "collection_id": "example-corpus",
        "adapter": "org.example.documents",
        "document_id": "document-1",
        "location": "records/document-1.json",
        "source_sha256": new_hash,
        "expected_previous_sha256": expected,
        "occurrences": [
            {
                "external_key": "reference:1",
                "work": work_id,
                "attachment_class": "bibliography",
                "external_object_paths": ["/references/0"],
                "extensions": {"org.example.documents": {"reference_id": 1}},
            },
            {
                "external_key": "reference:invalid",
                "work": None,
                "normalization_error": "Identifier is malformed",
                "attachment_class": "bibliography",
                "external_object_paths": ["/references/1"],
            },
        ],
    }


def _apply(root, arguments):
    preview = invoke("external_document.sync.preview", arguments)["result"]
    return invoke(
        "external_document.sync.apply",
        {**arguments, "preview_token": preview["preview_token"]},
    )["result"]


def test_external_document_sync_is_atomic_queryable_and_structural(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title=None,
        identifiers=[{"scheme": "doi", "value": "10.1000/example"}],
    )
    initial = _arguments(root, work["id"], new_hash=_hash(b"first"))

    preview = invoke("external_document.sync.preview", initial)["result"]
    assert preview["additions"] == ["reference:1", "reference:invalid"]
    assert preview["removals"] == []
    applied = invoke(
        "external_document.sync.apply",
        {**initial, "preview_token": preview["preview_token"]},
    )["result"]
    assert applied["scientific_support"] == "not_assessed"
    assert applied["scientific_inspection"] == "not_performed"

    documents = invoke(
        "external_document.query",
        {
            "library": root,
            "collection_id": "example-corpus",
            "document_id": "document-1",
        },
    )["result"]
    assert documents["total"] == 1
    occurrences = invoke(
        "occurrence.query",
        {
            "library": root,
            "collection_id": "example-corpus",
            "document_id": "document-1",
        },
    )["result"]
    assert occurrences["total"] == 2
    assert {item["work"] for item in occurrences["items"]} == {work["id"], None}
    assert occurrences["scientific_support"] == "not_assessed"
    validation = validate_library(root)
    assert validation["valid"] is True
    assert validation["counts"]["external_documents"] == 1
    assert validation["counts"]["occurrences"] == 2


def test_replace_removes_only_document_occurrences_and_preserves_work(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Preserved")
    first_hash = _hash(b"first")
    _apply(root, _arguments(root, work["id"], new_hash=first_hash))
    replacement = {
        **_arguments(
            root,
            work["id"],
            new_hash=_hash(b"second"),
            expected=first_hash,
        ),
        "occurrences": [],
    }
    preview = invoke("external_document.sync.preview", replacement)["result"]
    assert preview["removals"] == ["reference:1", "reference:invalid"]
    result = _apply(root, replacement)
    assert result["occurrences"] == []
    assert invoke("work.show", {"library": root, "work_id": work["id"]})["result"][
        "id"
    ] == work["id"]
    documents = invoke(
        "external_document.query",
        {"library": root, "document_id": "document-1"},
    )["result"]
    assert documents["total"] == 1
    assert documents["items"][0]["occurrence_ids"] == []
    assert invoke(
        "occurrence.query", {"library": root, "document_id": "document-1"}
    )["result"]["total"] == 0


def test_sync_rejects_stale_revision_and_mismatched_preview(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Concurrent")
    first_hash = _hash(b"first")
    _apply(root, _arguments(root, work["id"], new_hash=first_hash))

    stale = _arguments(root, work["id"], new_hash=_hash(b"second"))
    with pytest.raises(StorageError) as caught:
        invoke("external_document.sync.preview", stale)
    assert caught.value.code == "external_document_stale"

    current = _arguments(
        root,
        work["id"],
        new_hash=_hash(b"second"),
        expected=first_hash,
    )
    preview = invoke("external_document.sync.preview", current)["result"]
    changed = {**current, "location": "moved/document-1.json"}
    with pytest.raises(StorageError) as mismatch:
        invoke(
            "external_document.sync.apply",
            {**changed, "preview_token": preview["preview_token"]},
        )
    assert mismatch.value.code == "external_document_preview_mismatch"


def test_external_keys_are_adapter_owned_and_extensions_are_opaque(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Generic")
    arguments = _arguments(root, work["id"], new_hash=_hash(b"generic"))
    arguments["occurrences"][0]["external_key"] = "arbitrary-domain-key"
    arguments["occurrences"][0]["extensions"] = {
        "org.example.clinical": {"recommendation": "R17"}
    }
    applied = _apply(root, arguments)
    assert applied["occurrences"][0]["external_key"] == "arbitrary-domain-key"
    assert applied["occurrences"][0]["extensions"] == {
        "org.example.clinical": {"recommendation": "R17"}
    }


def test_external_document_sync_uses_collection_scoped_authority(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Scoped")
    arguments = _arguments(root, work["id"], new_hash=_hash(b"scoped"))

    with pytest.raises(StorageError) as denied:
        invoke(
            "external_document.sync.preview",
            arguments,
            capabilities=["collection.write:another-corpus"],
        )
    assert denied.value.code == "capability_denied"
    assert denied.value.path == "collection.write:example-corpus"

    preview = invoke(
        "external_document.sync.preview",
        arguments,
        capabilities=["collection.write:example-corpus"],
    )["result"]
    applied = invoke(
        "external_document.sync.apply",
        {**arguments, "preview_token": preview["preview_token"]},
        capabilities=["collection.write:example-corpus"],
    )["result"]
    assert applied["document"]["collection_id"] == "example-corpus"
