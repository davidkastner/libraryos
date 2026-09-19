import hashlib
import uuid
import zipfile

from libraryos import (
    create_work,
    export_collection,
    get_work,
    import_source,
    initialize_library,
    prepare_source,
    put_collection,
    register_derivative,
    restore_staged_removal,
    stage_collection_removal,
    validate_library,
    write_assessment,
    write_occurrence,
)
from libraryos.records import purge_preview


def _collection(collection_id, title, kind, members):
    return {
        "schema": "https://libraryos.dev/schemas/collection/v1",
        "schema_version": 1,
        "id": collection_id,
        "title": title,
        "kind": kind,
        "status": "active",
        "members": members,
    }


def test_physics_versions_and_equation_locator_are_domain_neutral(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    preprint = create_work(
        root,
        work_type="preprint",
        title="A result in condensed matter",
        identifiers=[{"scheme": "arxiv", "value": "2401.01234v2"}],
    )
    article = create_work(
        root,
        work_type="article",
        title="A result in condensed matter",
        identifiers=[{"scheme": "doi", "value": "10.1000/physics"}],
        relations=[{"type": "is-version-of", "target": preprint["id"]}],
    )
    source_path = tmp_path / "preprint.txt"
    source_path.write_text("Equation 4 establishes the bound.\n")
    source = import_source(
        root,
        preprint["id"],
        source_path,
        role="full_text",
        identity_status="verified",
        identity_method="fixture",
    )["source"]
    prepared_path = tmp_path / "prepared.md"
    prepared_path.write_text("# Result\n\nEquation 4 establishes the bound.\n")
    derivative = register_derivative(
        root,
        preprint["id"],
        prepared_path,
        role="source_faithful_markdown",
        input_sha256=[source["sha256"]],
        generator_name="fixture",
        generator_version="1",
        locators=[
            {
                "artifact_id": source["id"],
                "type": "equation",
                "value": "4",
            }
        ],
    )["derivative"]
    put_collection(
        root,
        _collection(
            "dissertation-chapter",
            "Dissertation chapter",
            "chapter-reading",
            [{"work": preprint["id"], "order": 1}, {"work": article["id"], "order": 2}],
        ),
    )
    assert derivative["locators"][0]["type"] == "equation"
    assert get_work(root, article["id"])["relations"][0]["target"] == preprint["id"]


def test_standard_revision_restricted_source_and_section_assessment(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    old = create_work(
        root,
        work_type="standard",
        title="Example Standard 2020",
        identifiers=[{"scheme": "url", "value": "https://example.org/std/2020"}],
    )
    current = create_work(
        root,
        work_type="standard",
        title="Example Standard 2026",
        identifiers=[{"scheme": "local", "value": "STD-2026"}],
        relations=[{"type": "is-version-of", "target": old["id"]}],
    )
    source_path = tmp_path / "standard.txt"
    source_path.write_text("Section 7. Requirements.\n")
    imported = import_source(
        root,
        current["id"],
        source_path,
        role="full_text",
        access="restricted",
        identity_status="verified",
        identity_method="manual-title-page-check",
    )
    put_collection(
        root,
        _collection(
            "standards-review",
            "Standards review",
            "compliance-review",
            [{"work": current["id"], "order": 1}],
        ),
    )
    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": "standards-review",
        "subject": {"kind": "source", "id": imported["source"]["id"]},
        "author": {"kind": "human", "id": "reviewer"},
        "purpose": "section-applicability",
        "summary": "Section 7 is in scope for this review.",
        "created_at": "2026-09-19T12:00:00Z",
        "locators": [
            {
                "artifact_id": imported["source"]["id"],
                "type": "section",
                "value": "7",
            }
        ],
    }
    assert write_assessment(root, assessment)["purpose"] == "section-applicability"


def test_dataset_supplement_and_generic_occurrence(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="dataset",
        title="Reusable measurements",
        identifiers=[{"scheme": "url", "value": "https://example.org/dataset"}],
    )
    archive = tmp_path / "supplement.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("measurements.csv", "x,y\n1,2\n")
    source = import_source(
        root,
        work["id"],
        archive,
        role="supplement",
        identity_status="verified",
        identity_method="dataset-landing-page",
        media_type="application/zip",
    )["source"]
    prepared = prepare_source(root, work["id"], source["id"])
    external = tmp_path / "source-record.json"
    external.write_text('{"dataset": "Reusable measurements"}\n')
    occurrence = {
        "schema": "https://libraryos.dev/schemas/occurrence/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "adapter": "example.dataset-records",
        "external_source": {
            "location": str(external),
            "sha256": hashlib.sha256(external.read_bytes()).hexdigest(),
            "record_id": "dataset",
        },
        "work": work["id"],
        "attachment_class": "dataset-citation",
        "external_object_paths": ["/dataset"],
        "inventoried_at": "2026-09-19T12:00:00Z",
    }
    recorded = write_occurrence(root, occurrence)
    assert prepared["derivatives"][0]["role"] == "supplement_extracted"
    assert recorded["scientific_support"] == "not_assessed"


def test_historical_local_scan_and_short_lived_collection_recovery(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="book",
        title="Local historical scan",
        identifiers=[{"scheme": "local", "value": "archive-box-12-item-4"}],
    )
    scan = tmp_path / "scan.txt"
    scan.write_text("OCR transcription requiring page-image verification.\n")
    source = import_source(
        root,
        work["id"],
        scan,
        role="ocr_transcription",
        identity_status="unverified",
    )["source"]
    prepared = prepare_source(root, work["id"], source["id"])
    assert "source_identity_unverified" in prepared["derivatives"][0]["warnings"]

    collection = _collection(
        "temporary-exploration",
        "Temporary exploration",
        "short-lived",
        [{"work": work["id"], "order": 1}],
    )
    collection["outputs"] = [{"kind": "canonical_json", "path": "snapshot.json"}]
    put_collection(root, collection)
    export_result = export_collection(root, collection["id"], output_root=tmp_path)
    export = tmp_path / "snapshot.json"
    preview = purge_preview(root, collection["id"])
    staged = stage_collection_removal(
        root,
        collection["id"],
        expected_preview=preview["records_deleted"],
    )
    restore_staged_removal(root, staged["transaction"]["id"])
    assert export.is_file()
    assert export_result["generated"][0]["path"] == str(export)
    assert validate_library(root)["valid"] is True
