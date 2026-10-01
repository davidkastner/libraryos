import hashlib
import uuid
from unittest.mock import patch

import pytest

from libraryos import (
    StorageError,
    create_work,
    get_work,
    import_source,
    initialize_library,
    list_records,
    register_derivative,
    register_source_candidate,
    resolve_read,
    write_occurrence,
)
from libraryos.reading import open_read


def test_occurrence_import_is_structural_and_immutable(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="standard", title="A standard")
    occurrence = {
        "schema": "https://libraryos.dev/schemas/occurrence/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "adapter": "example.standards",
        "external_source": {
            "location": "standards/index.json",
            "sha256": hashlib.sha256(b"external inventory").hexdigest(),
            "record_id": "STD-1",
        },
        "work": work["id"],
        "attachment_class": "bibliography",
        "external_object_paths": ["records/STD-1/references/4"],
        "inventoried_at": "2026-09-19T12:00:00Z",
    }
    first = write_occurrence(root, occurrence)
    second = write_occurrence(root, occurrence)
    assert first == second
    assert first["scientific_support"] == "not_assessed"
    assert list_records(root, "occurrences") == [first]
    assert list_records(root, "reviews") == []
    assert list_records(root, "assessments") == []


def test_read_resolver_prefers_pdf_without_opening_or_review(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Representations")
    html_path = tmp_path / "article.html"
    html_path.write_text("<article>Representations</article>")
    html_source = import_source(
        root,
        work["id"],
        html_path,
        role="full_text",
        media_type="text/html",
        identity_status="verified",
        identity_method="fixture",
    )["source"]
    markdown_path = tmp_path / "article.md"
    markdown_path.write_text("# Representations\n")
    register_derivative(
        root,
        work["id"],
        markdown_path,
        role="source_faithful_markdown",
        input_sha256=[html_source["sha256"]],
        generator_name="fixture",
        generator_version="1",
        media_type="text/markdown",
    )
    pdf_path = tmp_path / "article.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    pdf_source = import_source(
        root,
        work["id"],
        pdf_path,
        role="full_text",
        media_type="application/pdf",
        identity_status="verified",
        identity_method="fixture",
    )["source"]
    result = resolve_read(root, work["id"])
    assert result["representation"] == "local_pdf"
    assert result["artifact_id"] == pdf_source["id"]
    assert result["opened"] is False
    assert result["review_created"] is False
    assert list_records(root, "reviews") == []

    preferred = resolve_read(
        root,
        work["id"],
        preference=[
            "source_faithful_text",
            "local_pdf",
            "local_structured_source",
            "stable_source_url",
        ],
    )
    assert preferred["representation"] == "source_faithful_text"
    assert preferred["preference"][0] == "source_faithful_text"


def test_read_resolver_rejects_ambiguous_preferences(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Preferences")

    with pytest.raises(StorageError, match="duplicate"):
        resolve_read(root, work["id"], preference=["local_pdf", "local_pdf"])
    with pytest.raises(StorageError, match="Unknown"):
        resolve_read(root, work["id"], preference=["telepathy"])


def test_read_resolver_returns_candidate_or_recovery(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Metadata only")
    unavailable = resolve_read(root, work["id"])
    assert unavailable["representation"] == "unavailable"
    assert unavailable["manuscript_status"] == "unavailable"
    assert [item["operation"] for item in unavailable["next_actions"]] == [
        "source.crossref.discover",
        "source.import",
    ]
    candidate = register_source_candidate(
        root,
        work["id"],
        url="https://example.org/report?temporary=secret",
        provider="example",
        access="open_access",
        identity_method="exact_local_identifier",
        verified_by="example",
        verified_at="2026-09-19T12:00:00Z",
    )
    resolved = resolve_read(root, work["id"])
    assert resolved["representation"] == "unavailable"
    assert resolved["manuscript_status"] == "browser_retrieval_required"
    assert resolved["readable"] is False
    assert resolved["retrieval_url"] == "https://example.org/report"
    assert [item["operation"] for item in resolved["next_actions"]] == [
        "source.import",
        "source.prepare",
        "read.resolve",
    ]
    assert resolved["review_created"] is False
    assert candidate["candidate"]["url"] == resolved["retrieval_url"]
    assert get_work(root, work["id"])["sources"] == []


def test_metadata_only_xml_is_not_a_readable_manuscript(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title="Metadata masquerading as full text",
        identifiers=[{"scheme": "doi", "value": "10.1000/metadata"}],
    )
    source_path = tmp_path / "metadata.xml"
    source_path.write_text(
        "<article><front><article-meta><title-group><article-title>Example</article-title>"
        "</title-group><abstract><p>Abstract only.</p></abstract></article-meta></front></article>"
    )
    import_source(
        root,
        work["id"],
        source_path,
        role="full_text",
        media_type="application/xml",
    )

    resolved = resolve_read(root, work["id"])

    assert resolved["representation"] == "unavailable"
    assert resolved["manuscript_status"] == "browser_retrieval_required"
    assert resolved["readable"] is False


def test_prepared_metadata_only_xml_is_not_a_readable_manuscript(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title="Prepared metadata masquerading as full text",
        identifiers=[{"scheme": "doi", "value": "10.1000/prepared-metadata"}],
    )
    source_path = tmp_path / "metadata.xml"
    source_path.write_text(
        "<article><front><article-meta><abstract><p>Abstract only.</p></abstract>"
        "</article-meta></front></article>"
    )
    source = import_source(
        root,
        work["id"],
        source_path,
        role="full_text",
        media_type="application/xml",
        identity_status="verified",
        identity_method="fixture",
    )["source"]
    markdown_path = tmp_path / "metadata.md"
    markdown_path.write_text("# Abstract only\n")
    register_derivative(
        root,
        work["id"],
        markdown_path,
        role="source_faithful_markdown",
        media_type="text/markdown",
        input_sha256=[source["sha256"]],
        generator_name="fixture",
        generator_version="1",
        quality="ready",
    )

    resolved = resolve_read(root, work["id"])

    assert resolved["representation"] == "unavailable"
    assert resolved["manuscript_status"] == "browser_retrieval_required"
    assert resolved["readable"] is False


def test_supplement_pdf_is_not_an_article_manuscript(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Supplement only")
    source_path = tmp_path / "supporting-information.pdf"
    source_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    import_source(
        root,
        work["id"],
        source_path,
        role="supplement",
        media_type="application/pdf",
    )

    resolved = resolve_read(root, work["id"])

    assert resolved["representation"] == "unavailable"
    assert resolved["manuscript_status"] == "unavailable"
    assert resolved["readable"] is False


@pytest.mark.parametrize("role", ["accepted_manuscript", "publisher_pdf"])
def test_verified_legacy_article_pdf_roles_are_readable(tmp_path, role):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title=f"Legacy role: {role}")
    source_path = tmp_path / f"{role}.pdf"
    source_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    import_source(
        root,
        work["id"],
        source_path,
        role=role,
        media_type="application/pdf",
        identity_status="verified",
        identity_method="fixture",
    )

    resolved = resolve_read(root, work["id"])

    assert resolved["representation"] == "local_pdf"
    assert resolved["manuscript_status"] == "manuscript_ready"
    assert resolved["readable"] is True


@pytest.mark.parametrize(
    ("xml", "expected_status"),
    [
        (
            "<article><front><article-meta><abstract><p>Abstract only.</p></abstract>"
            "</article-meta></front></article>",
            "unavailable",
        ),
        (
            "<article><front/><body><sec><p>Article body.</p></sec></body></article>",
            "manuscript_ready",
        ),
    ],
)
def test_legacy_structured_article_role_still_requires_a_body(
    tmp_path, xml, expected_status
):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Legacy structured role")
    source_path = tmp_path / "publisher.xml"
    source_path.write_text(xml)
    import_source(
        root,
        work["id"],
        source_path,
        role="publisher_fulltext",
        media_type="application/xml",
        identity_status="verified",
        identity_method="fixture",
    )

    resolved = resolve_read(root, work["id"])

    assert resolved["manuscript_status"] == expected_status
    assert resolved["readable"] is (expected_status == "manuscript_ready")


def test_source_data_pdf_is_not_an_article_manuscript(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Source data only")
    source_path = tmp_path / "source-data.pdf"
    source_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    import_source(
        root,
        work["id"],
        source_path,
        role="source_data",
        media_type="application/pdf",
        identity_status="verified",
        identity_method="fixture",
    )

    resolved = resolve_read(root, work["id"])

    assert resolved["manuscript_status"] == "unavailable"
    assert resolved["readable"] is False


def test_acquired_article_source_that_is_not_readable_needs_preparation(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Needs preparation")
    source_path = tmp_path / "article.txt"
    source_path.write_text("Authoritative article source awaiting conversion.")
    import_source(root, work["id"], source_path, role="full_text", media_type="text/plain")

    resolved = resolve_read(root, work["id"])

    assert resolved["representation"] == "unavailable"
    assert resolved["manuscript_status"] == "source_acquired_not_prepared"
    assert [item["operation"] for item in resolved["next_actions"]] == ["source.prepare"]


def test_open_read_uses_preview_without_recording_review(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Open me")
    pdf_path = tmp_path / "article.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    import_source(
        root,
        work["id"],
        pdf_path,
        role="full_text",
        media_type="application/pdf",
        identity_status="verified",
        identity_method="fixture",
    )

    with patch("libraryos.reading.sys.platform", "darwin"), patch(
        "libraryos.reading.subprocess.Popen"
    ) as popen:
        result = open_read(root, work["id"], application="preview")

    command = popen.call_args.args[0]
    assert command[:3] == ["open", "-a", "Preview"]
    assert command[-2] == "--"
    assert result["opened"] is True
    assert result["opened_with"] == "Preview"
    assert result["review_created"] is False
    assert result["scientific_inspection"] == "not_performed"
    assert list_records(root, "reviews") == []


def test_open_read_requires_local_pdf(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="No local PDF")

    with pytest.raises(StorageError) as caught:
        open_read(root, work["id"], application="preview")
    assert caught.value.code == "local_pdf_unavailable"
