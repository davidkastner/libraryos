import hashlib
import json
import uuid
from pathlib import Path

import pytest
import yaml

from libraryos import (
    StorageError,
    create_work,
    export_collection,
    get_collection,
    import_assessment,
    initialize_library,
    list_collections,
    put_external_collection,
    register_external_collection,
    relocate_external_collection,
    revalidate_external_collection,
    validate_library,
    write_assessment,
)


def _collection(collection_id, work_id):
    return {
        "schema": "https://libraryos.dev/schemas/collection/v1",
        "schema_version": 1,
        "id": collection_id,
        "title": "Portable project collection",
        "kind": "reading-list",
        "status": "active",
        "members": [{"work": work_id, "order": 10}],
    }


def test_external_yaml_collection_is_authoritative_and_validated(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="A report")
    project = tmp_path / "project"
    project.mkdir()
    path = project / "literature.yaml"
    path.write_text(yaml.safe_dump(_collection("project-a", work["id"]), sort_keys=False))

    result = register_external_collection(root, path)
    assert result["registration"]["format"] == "yaml"
    assert result["registration"]["path"] == str(path.resolve())
    assert get_collection(root, "project-a")["location"]["kind"] == "external"
    assert list_collections(root)[0]["collection"]["title"] == "Portable project collection"
    assert not (root / "collections" / "project-a.json").exists()
    assert validate_library(root)["valid"] is True

    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": "project-a",
        "subject": {"kind": "work", "id": work["id"]},
        "author": {"kind": "human", "id": "researcher"},
        "purpose": "relevance",
        "summary": "Relevant here.",
        "created_at": "2026-09-19T12:00:00Z",
    }
    assert write_assessment(root, assessment)["collection_id"] == "project-a"


def test_yaml_assessment_import_uses_canonical_immutable_record(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="A report")
    collection = _collection("project-a", work["id"])
    project = tmp_path / "project"
    project.mkdir()
    collection_path = project / "literature.yaml"
    collection_path.write_text(yaml.safe_dump(collection, sort_keys=False))
    register_external_collection(root, collection_path)
    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": "project-a",
        "subject": {"kind": "work", "id": work["id"]},
        "author": {"kind": "human", "id": "researcher"},
        "purpose": "relevance",
        "summary": "Relevant to this project only.",
        "created_at": "2026-09-19T12:00:00Z",
    }
    assessment_path = project / "assessment.yaml"
    assessment_path.write_text(yaml.safe_dump(assessment, sort_keys=False))

    imported = import_assessment(root, "project-a", assessment_path)
    stored = root / "records" / "assessments" / f"{assessment['id']}.json"
    assert imported == assessment
    assert json.loads(stored.read_text()) == assessment

    with pytest.raises(StorageError) as immutable:
        import_assessment(root, "project-a", assessment_path)
    assert immutable.value.code == "immutable_record_exists"


def test_yaml_assessment_import_rejects_duplicate_keys_and_scope_mismatch(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    from libraryos import create_collection

    create_collection(root, collection_id="project-a", title="Project A")
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema: https://libraryos.dev/schemas/assessment/v1\n"
        "schema_version: 1\n"
        f"id: {uuid.uuid4()}\n"
        "collection_id: project-a\n"
        "collection_id: project-b\n"
        "subject: {kind: project_object, id: item}\n"
        "author: {kind: human, id: researcher}\n"
        "purpose: relevance\n"
        "summary: Relevant.\n"
        "created_at: 2026-09-19T12:00:00Z\n"
    )
    with pytest.raises(StorageError) as duplicate_error:
        import_assessment(root, "project-a", duplicate)
    assert duplicate_error.value.code == "assessment_parse_failed"

    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": "project-b",
        "subject": {"kind": "project_object", "id": "item"},
        "author": {"kind": "human", "id": "researcher"},
        "purpose": "relevance",
        "summary": "Relevant.",
        "created_at": "2026-09-19T12:00:00Z",
    }
    mismatch = tmp_path / "mismatch.json"
    mismatch.write_text(json.dumps(assessment))
    with pytest.raises(StorageError) as mismatch_error:
        import_assessment(root, "project-a", mismatch)
    assert mismatch_error.value.code == "assessment_collection_mismatch"


def test_external_collection_write_uses_compare_and_swap(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    project = tmp_path / "project"
    project.mkdir()
    path = project / "literature.json"
    record = _collection(
        "project-a",
        create_work(root, work_type="book", title="A book")["id"],
    )
    path.write_text(json.dumps(record))
    registration = register_external_collection(root, path)["registration"]

    changed = {**record, "title": "Changed through LibraryOS"}
    result = put_external_collection(
        root,
        "project-a",
        changed,
        expected_sha256=registration["sha256"],
    )
    assert result["collection"]["title"] == "Changed through LibraryOS"
    assert result["registration"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    path.write_text(path.read_text().replace("Changed through LibraryOS", "Changed in Git"))
    with pytest.raises(StorageError) as caught:
        put_external_collection(
            root,
            "project-a",
            changed,
            expected_sha256=result["registration"]["sha256"],
        )
    assert caught.value.code == "collection_external_conflict"


def test_external_collection_rejects_duplicate_yaml_keys(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        "schema: https://libraryos.dev/schemas/collection/v1\n"
        "schema_version: 1\n"
        "id: duplicate\n"
        "title: First\n"
        "title: Second\n"
        "status: active\n"
        "members: []\n"
    )
    with pytest.raises(StorageError) as caught:
        register_external_collection(root, path)
    assert caught.value.code == "collection_parse_failed"


def test_external_collection_detects_out_of_band_change_on_read(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    path = tmp_path / "collection.yaml"
    work = create_work(root, work_type="dataset", title="Data")
    path.write_text(
        yaml.safe_dump(_collection("project-a", work["id"]), sort_keys=False)
    )
    registration = register_external_collection(root, path)["registration"]
    path.write_text(path.read_text().replace("Portable project collection", "Edited"))
    with pytest.raises(StorageError) as caught:
        get_collection(root, "project-a")
    assert caught.value.code == "collection_external_conflict"
    report = validate_library(root)
    assert report["valid"] is False
    assert report["findings"][0]["code"] == "collection_external_conflict"
    adopted = revalidate_external_collection(
        root,
        "project-a",
        expected_registered_sha256=registration["sha256"],
    )
    assert adopted["collection"]["title"] == "Edited"
    assert get_collection(root, "project-a")["collection"]["title"] == "Edited"
    assert validate_library(root)["valid"] is True


def test_external_collection_registration_can_follow_an_unchanged_move(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    work = create_work(root, work_type="dataset", title="Data")
    old = tmp_path / "old" / "literature.yaml"
    old.parent.mkdir()
    old.write_text(yaml.safe_dump(_collection("project-a", work["id"]), sort_keys=False))
    registration = register_external_collection(root, old)["registration"]
    new = tmp_path / "new" / "renamed.yaml"
    new.parent.mkdir()
    old.rename(new)

    relocated = relocate_external_collection(
        root,
        "project-a",
        new,
        expected_registered_sha256=registration["sha256"],
    )
    assert relocated["registration"]["path"] == str(new.resolve())
    assert get_collection(root, "project-a")["location"]["path"] == str(new.resolve())

    changed = tmp_path / "changed.yaml"
    changed.write_text(new.read_text().replace("Portable", "Altered"))
    with pytest.raises(StorageError) as mismatch:
        relocate_external_collection(
            root,
            "project-a",
            changed,
            expected_registered_sha256=registration["sha256"],
        )
    assert mismatch.value.code == "collection_relocation_content_mismatch"


def test_collection_exports_are_deterministic_metadata_only(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    first = create_work(
        root,
        work_type="article",
        title="A general paper",
        identifiers=[{"scheme": "doi", "value": "10.1234/example"}],
        metadata={
            "authors": [{"family": "Ng", "given": "Ada"}],
            "container_title": "Journal of Examples",
            "issued": "2025-03-02",
        },
    )
    second = create_work(
        root,
        work_type="report",
        title="A public report",
        identifiers=[{"scheme": "url", "value": "https://example.org/report"}],
        metadata={"authors": ["Example Institute"], "issued": 2024},
    )
    project = tmp_path / "project"
    project.mkdir()
    path = project / "collection.yaml"
    record = _collection("project-a", first["id"])
    record["members"] = [
        {"work": second["id"], "order": 20},
        {"work": first["id"], "order": 10, "citation_key": "Ng2025"},
    ]
    record["outputs"] = [
        {"kind": "overview_markdown", "path": "README.literature.md"},
        {"kind": "bibtex", "path": "references.bib"},
        {"kind": "csl_json", "path": "references.json"},
        {"kind": "ris", "path": "references.ris"},
        {"kind": "canonical_json", "path": "libraryos.collection.json"},
    ]
    path.write_text(yaml.safe_dump(record, sort_keys=False))
    register_external_collection(root, path)

    first_result = export_collection(root, "project-a")
    first_payloads = {
        item["path"]: Path(item["path"]).read_bytes()
        for item in first_result["generated"]
    }
    second_result = export_collection(root, "project-a")
    second_payloads = {
        item["path"]: Path(item["path"]).read_bytes()
        for item in second_result["generated"]
    }
    assert first_payloads == second_payloads
    assert first_result["publication_bytes_exported"] is False
    assert "Scientific support is not inferred" in (
        project / "README.literature.md"
    ).read_text()
    assert "@article{Ng2025" in (project / "references.bib").read_text()
    assert "10.1234/example" in (project / "references.ris").read_text()
    canonical = json.loads((project / "libraryos.collection.json").read_text())
    assert [item["libraryos_id"] for item in canonical["works"]] == [
        first["id"],
        second["id"],
    ]
    assert all("sources" not in item for item in canonical["works"])


def test_collection_export_rejects_path_escape(tmp_path):
    root = tmp_path / "private-library"
    initialize_library(root)
    work = create_work(root, work_type="standard", title="Standard")
    project = tmp_path / "project"
    project.mkdir()
    record = _collection("project-a", work["id"])
    record["outputs"] = [{"kind": "bibtex", "path": "../escaped.bib"}]
    path = project / "collection.json"
    path.write_text(json.dumps(record))
    register_external_collection(root, path)
    with pytest.raises(StorageError) as caught:
        export_collection(root, "project-a")
    assert caught.value.code == "collection_output_path_unsafe"
