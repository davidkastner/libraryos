import zipfile

import pytest

from libraryos import (
    StorageError,
    create_work,
    get_work,
    import_source,
    initialize_library,
    list_jobs,
    prepare_source,
    rebuild_catalog,
    retry_job,
    search_prepared,
    validate_library,
)


def _source(root, tmp_path, *, name, content, media_type, verified=True):
    work = create_work(root, work_type="article", title="Preparation fixture")
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    imported = import_source(
        root,
        work["id"],
        path,
        role="full_text",
        media_type=media_type,
        identity_status="verified" if verified else "unverified",
        identity_method="fixture" if verified else None,
    )
    return work, imported["source"]


def test_html_preparation_is_source_faithful_and_searchable(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="article.html",
        media_type="text/html",
        content=(
            "<html><body><article><h1>Preparation fixture</h1>"
            "<p>The superconducting result remains unchanged: Fe<sup>2+</sup>.</p>"
            "<figure><figcaption>Figure 1. Exact caption.</figcaption></figure>"
            "</article><script>discard_me()</script></body></html>"
        ),
    )
    result = prepare_source(root, work["id"], source["id"])
    derivative = result["derivatives"][0]
    assert result["scientific_inspection"] == "not_performed"
    assert result["next_actions"] == [
        {
            "operation": "search.prepared",
            "purpose": (
                "Search the prepared representation within this exact work; "
                "search results do not establish scientific support."
            ),
            "arguments": {
                "library": str(root.resolve()),
                "query": "<search terms>",
                "work_ids": [work["id"]],
            },
            "required_arguments": ["query"],
        },
        {
            "operation": "read.resolve",
            "purpose": (
                "Resolve the best available reading representation for source inspection."
            ),
            "arguments": {"library": str(root.resolve()), "work_id": work["id"]},
        },
    ]
    assert derivative["role"] == "source_faithful_markdown"
    assert derivative["input_sha256"] == [source["sha256"]]
    assert derivative["locators"][0]["artifact_id"] == source["id"]
    prepared = root / "works" / work["id"] / derivative["path"]
    text = prepared.read_text()
    assert "# Preparation fixture" in text
    assert "superconducting result remains unchanged" in text
    assert "Figure 1. Exact caption." in text
    assert "discard_me" not in text
    rebuild_catalog(root)
    assert search_prepared(root, "superconducting")[0]["work_id"] == work["id"]
    assert validate_library(root)["valid"] is True


def test_html_preparation_flags_link_only_full_text(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="abstract-record.html",
        media_type="text/html",
        content=(
            "<html><body><article><h1>Preparation fixture</h1>"
            "<h2>Abstract</h2><p>Only the abstract is present.</p>"
            "<h2>Full Text</h2><p>The Full Text of this article is available "
            "as a PDF.</p></article></body></html>"
        ),
    )

    result = prepare_source(root, work["id"], source["id"])

    assert result["derivatives"][0]["warnings"] == [
        "html_structure_conservatively_preserved",
        "full_text_not_present",
    ]
    assert result["next_actions"][0]["operation"] == "source.crossref.discover"
    assert "full-text source" in result["next_actions"][0]["purpose"]


def test_xml_preparation_avoids_nested_text_duplication(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="article.xml",
        media_type="application/xml",
        content=(
            "<?xml version=\"1.0\"?><article><front><article-meta>"
            "<title-group><article-title>Preparation fixture</article-title></title-group>"
            "<abstract><p>One abstract sentence.</p></abstract></article-meta></front>"
            "<body><sec><title>Results</title><pb n=\"S3\"/>"
            "<p>One result sentence.</p></sec></body>"
            "</article>"
        ),
    )
    result = prepare_source(root, work["id"], source["id"])
    path = root / "works" / work["id"] / result["derivatives"][0]["path"]
    text = path.read_text()
    assert text.count("One abstract sentence.") == 1
    assert text.count("One result sentence.") == 1
    assert "<!-- source-page: S3 -->" in text
    assert {"artifact_id": source["id"], "type": "page", "value": "S3"} in (
        result["derivatives"][0]["locators"]
    )
    assert len(result["derivatives"][0]["generator"]["configuration_sha256"]) == 64


def test_bioc_xml_preparation_preserves_passages_and_offsets(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="article.bioc.xml",
        media_type="application/xml",
        content=(
            '<?xml version="1.0"?><collection><document><id>42</id>'
            '<passage><infon key="section_type">TITLE</infon>'
            '<infon key="type">front</infon><offset>0</offset>'
            "<text>BioC preparation fixture</text></passage>"
            '<passage><infon key="section_type">RESULTS</infon>'
            '<infon key="type">paragraph</infon><offset>25</offset>'
            "<text>Asp233 shuttles the proton to solvent.</text></passage>"
            '<passage><infon key="section_type">FIG</infon>'
            '<infon key="type">section_caption</infon><offset>70</offset>'
            "<text>Figure 6. Proposed catalytic mechanism.</text></passage>"
            "</document></collection>"
        ),
    )
    result = prepare_source(root, work["id"], source["id"])
    derivative = result["derivatives"][0]
    path = root / "works" / work["id"] / derivative["path"]
    text = path.read_text()
    assert "# BioC preparation fixture" in text
    assert "## RESULTS" in text
    assert "## FIG" in text
    assert "Figure 6. Proposed catalytic mechanism." in text
    assert text.count("Asp233 shuttles the proton to solvent.") == 1
    assert {
        "artifact_id": source["id"],
        "type": "passage",
        "value": "offset:25",
    } in derivative["locators"]


def test_plain_text_preparation_marks_unverified_identity(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="report.txt",
        media_type="text/plain",
        content="Exact source text.\n",
        verified=False,
    )
    result = prepare_source(root, work["id"], source["id"])
    derivative = result["derivatives"][0]
    assert derivative["warnings"] == ["source_identity_unverified"]
    output = root / "works" / work["id"] / derivative["path"]
    assert output.read_text() == "Exact source text.\n"


def test_preparation_is_idempotent(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="report.txt",
        media_type="text/plain",
        content="Exact source text.\n",
    )
    first = prepare_source(root, work["id"], source["id"])
    second = prepare_source(root, work["id"], source["id"])
    assert first["derivatives"][0]["id"] == second["derivatives"][0]["id"]
    assert len(get_work(root, work["id"])["derivatives"]) == 1


def test_unsupported_preparation_is_categorized(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work, source = _source(
        root,
        tmp_path,
        name="archive.zip",
        media_type="application/zip",
        content=b"PK\x03\x04fixture",
    )
    with pytest.raises(StorageError) as caught:
        prepare_source(root, work["id"], source["id"])
    assert caught.value.code == "conversion_failure"
    job = list_jobs(root)[0]
    assert job["status"] == "failed"
    assert job["exceptions"][0]["category"] == "conversion_failure"


def test_pdf_preparation_extracts_text_pages_figures_and_checkpoints(tmp_path):
    import pymupdf

    root = tmp_path / "library"
    initialize_library(root)
    pdf_path = tmp_path / "article.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Figure 1. Synthetic fixture")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 2, 2), False)
    pixmap.clear_with(0xFF0000)
    page.insert_image(pymupdf.Rect(72, 100, 120, 148), pixmap=pixmap)
    document.save(pdf_path)
    document.close()
    work, source = _source(
        root,
        tmp_path,
        name="article.pdf",
        media_type="application/pdf",
        content=pdf_path.read_bytes(),
    )
    result = prepare_source(root, work["id"], source["id"], render_dpi=120)
    roles = [item["role"] for item in result["derivatives"]]
    assert {"page_text", "page_render", "figure", "source_faithful_markdown"} <= set(roles)
    assert result["preparation"]["pages"] == 1
    assert result["preparation"]["figures_extracted"] == 1
    assert result["preparation"]["ocr_pages"] == []
    assert result["preparation"]["generator"]["name"] == "PyMuPDF"
    text = next(item for item in result["derivatives"] if item["role"] == "page_text")
    assert "Figure 1. Synthetic fixture" in (
        root / "works" / work["id"] / text["path"]
    ).read_text()
    figure = next(item for item in result["derivatives"] if item["role"] == "figure")
    assert figure["quality"] == "partial"
    assert "embedded_image_extraction_is_not_figure_segmentation" in figure["warnings"]
    job = list_jobs(root)[0]
    assert job["status"] == "succeeded"
    assert job["checkpoint"]["completed_pages"] == 1
    assert validate_library(root)["valid"] is True


def test_pdf_preparation_resumes_after_completed_page(tmp_path, monkeypatch):
    import pymupdf

    import libraryos.preparation as preparation

    root = tmp_path / "library"
    initialize_library(root)
    pdf_path = tmp_path / "two-pages.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "First completed page")
    document.new_page().insert_text((72, 72), "Second resumed page")
    document.save(pdf_path)
    document.close()
    work, source = _source(
        root,
        tmp_path,
        name="two-pages.pdf",
        media_type="application/pdf",
        content=pdf_path.read_bytes(),
    )
    original = preparation.register_derivative
    failed_once = False

    def fail_on_second_page(*args, **kwargs):
        nonlocal failed_once
        locators = kwargs.get("locators", [])
        if not failed_once and any(
            item.get("type") == "page" and item.get("value") == 2
            for item in locators
        ):
            failed_once = True
            raise StorageError("injected interruption", code="injected_interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(preparation, "register_derivative", fail_on_second_page)
    with pytest.raises(StorageError, match="injected interruption"):
        prepare_source(root, work["id"], source["id"], render_dpi=120)
    failed = list_jobs(root)[0]
    assert failed["checkpoint"]["completed_pages"] == 1
    first_page_ids = list(failed["checkpoint"]["derivative_ids"])

    monkeypatch.setattr(preparation, "register_derivative", original)
    retry_job(root, failed["id"], reason="resume test")
    result = prepare_source(
        root,
        work["id"],
        source["id"],
        render_dpi=120,
        resume_job_id=failed["id"],
    )

    assert result["job_id"] == failed["id"]
    assert first_page_ids == [
        item["id"]
        for item in result["derivatives"]
        if any(
            locator.get("type") == "page" and locator.get("value") == 1
            for locator in item.get("locators", [])
        )
    ]
    assert list_jobs(root)[0]["checkpoint"]["completed_pages"] == 2
    assert validate_library(root)["valid"] is True


def test_supplement_archive_preparation_is_bounded_and_source_linked(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    archive = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("tables/measurements.csv", "time,value\n0,1\n")
        output.writestr("README.txt", "Synthetic dataset")
    work, source = _source(
        root,
        tmp_path,
        name="dataset.zip",
        media_type="application/zip",
        content=archive.read_bytes(),
    )
    result = prepare_source(root, work["id"], source["id"])
    assert result["preparation"]["members_extracted"] == 2
    assert {item["role"] for item in result["derivatives"]} == {
        "supplement_extracted"
    }
    assert all(
        item["input_sha256"] == [source["sha256"]]
        for item in result["derivatives"]
    )
    assert {
        locator["value"]
        for item in result["derivatives"]
        for locator in item["locators"]
    } == {"README.txt", "tables/measurements.csv"}


def test_supplement_archive_rejects_parent_traversal(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt", "must not escape")
    work, source = _source(
        root,
        tmp_path,
        name="unsafe.zip",
        media_type="application/zip",
        content=archive.read_bytes(),
    )
    with pytest.raises(StorageError) as caught:
        prepare_source(root, work["id"], source["id"])
    assert caught.value.code == "conversion_archive_unsafe"
    assert not (tmp_path / "escape.txt").exists()
