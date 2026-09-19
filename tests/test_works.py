import json

from libraryos import (
    create_work,
    import_source,
    initialize_library,
    rebuild_catalog,
    register_derivative,
    search_catalog,
    search_prepared,
    validate_library,
)


def test_work_source_and_rebuildable_catalog(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="report",
        title="A General Technical Report",
        identifiers=[{"scheme": "local", "value": "REPORT-7", "normalized": "report-7"}],
        metadata={"issued": 2026},
    )
    supplied = tmp_path / "report.txt"
    supplied.write_text("authoritative source\n")

    first = import_source(root, work["id"], supplied, role="full_text")
    second = import_source(root, work["id"], supplied, role="full_text")

    assert first["created"] is True
    assert second["created"] is False
    assert first["source"]["sha256"] == second["source"]["sha256"]
    assert supplied.read_text() == "authoritative source\n"
    assert validate_library(root)["valid"] is True

    counts = rebuild_catalog(root)
    assert counts == {"works": 1, "identifiers": 1, "sources": 1, "derivatives": 0}
    assert search_catalog(root, "REPORT-7")[0]["id"] == work["id"]
    assert search_catalog(root, "technical")[0]["scientific_support"] == "not_assessed"

    prepared = tmp_path / "prepared.md"
    prepared.write_text("# Result\nA superconducting magnet was characterized.\n")
    derivative = register_derivative(
        root,
        work["id"],
        prepared,
        role="agent_markdown",
        input_sha256=[first["source"]["sha256"]],
        generator_name="fixture",
        generator_version="1",
        media_type="text/markdown",
    )
    assert derivative["created"] is True
    rebuild_catalog(root)
    hit = search_prepared(root, "superconducting")[0]
    assert hit["artifact_id"] == derivative["derivative"]["id"]
    assert hit["locator"]["type"] == "passage"
    assert hit["scientific_support"] == "not_assessed"

    (root / ".libraryos" / "index.sqlite").unlink()
    assert search_catalog(root, "report-7")[0]["id"] == work["id"]


def test_extensions_round_trip_through_work_write(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="dataset",
        title="Unrelated-domain fixture",
        metadata={"extensions": {"example.physics": {"beam_energy": "7 GeV"}}},
    )
    manifest = json.loads((root / "works" / work["id"] / "manifest.json").read_text())
    assert manifest["work"]["extensions"]["example.physics"]["beam_energy"] == "7 GeV"
