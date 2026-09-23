from libraryos import (
    create_work,
    import_source,
    initialize_library,
    list_figures,
    read_artifact,
    read_pages,
    search_passages,
    source_receipt,
)
from libraryos.works import register_derivative


def _library(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Navigation fixture")
    source_path = tmp_path / "paper.pdf"
    source_path.write_bytes(b"%PDF fixture")
    source = import_source(
        root, work["id"], source_path, role="full_text", media_type="application/pdf",
        identity_status="verified", identity_method="fixture",
    )["source"]
    text_path = tmp_path / "page.txt"
    text_path.write_text("Catalytic Asp-42 transfers a proton.\n", encoding="utf-8")
    page = register_derivative(
        root, work["id"], text_path, role="page_text",
        input_sha256=[source["sha256"]], generator_name="fixture",
        generator_version="1", media_type="text/plain",
        locators=[{"artifact_id": source["id"], "type": "page", "value": 3}],
    )["derivative"]
    figure_path = tmp_path / "figure.png"
    figure_path.write_bytes(b"PNG")
    figure = register_derivative(
        root, work["id"], figure_path, role="figure",
        input_sha256=[source["sha256"]], generator_name="fixture",
        generator_version="1", media_type="image/png", quality="partial",
        warnings=["caption_candidate:Figure 1. Catalytic geometry."],
        locators=[{"artifact_id": source["id"], "type": "page", "value": 3}],
    )["derivative"]
    return root, work, page, figure


def test_page_and_passage_navigation_preserve_exact_artifact_identity(tmp_path):
    root, work, page, _ = _library(tmp_path)
    pages = read_pages(root, work["id"], start=3, end=3)
    assert pages["items"][0]["page_label"] == "3"
    assert pages["items"][0]["artifact_id"] == page["id"]
    hits = search_passages(root, work["id"], query="asp-42")
    assert hits["items"][0]["page_label"] == "3"
    assert "Asp-42 transfers" in hits["items"][0]["passage"]
    assert hits["support_asserted"] is False


def test_figures_artifact_resolution_and_receipts_do_not_claim_review(tmp_path):
    root, work, _, figure = _library(tmp_path)
    item = list_figures(root, work["id"])["items"][0]
    assert item["caption_candidates"] == ["Figure 1. Catalytic geometry."]
    assert read_artifact(root, work["id"], artifact_id=figure["id"])["items"][0]["sha256"]
    receipt = source_receipt(
        root, work["id"], artifact_id=figure["id"],
        locator_type="figure", locator_value="Figure 1",
    )
    assert receipt["representation"] == "figure"
    assert receipt["locator"] == {"type": "figure", "value": "Figure 1"}
    assert receipt["scientific_inspection"] == "not_performed"
    assert receipt["support_asserted"] is False
