import json

from libraryos import initialize_library
from libraryos.cli import main
from libraryos.operations import OPERATIONS


def _output(capsys):
    return json.loads(capsys.readouterr().out)


def test_cli_work_source_rebuild_search(tmp_path, capsys):
    root = tmp_path / "library"
    assert main(["init", str(root)]) == 0
    _output(capsys)
    assert main([
        "work-create", "--library", str(root), "--type", "standard",
        "--title", "Interoperable Standard", "--identifier", "local:STD-1",
    ]) == 0
    work = _output(capsys)["result"]

    source = tmp_path / "standard.txt"
    source.write_text("standard source\n")
    assert main([
        "source-import", "--library", str(root), "--work-id", work["id"],
        "--role", "full_text", str(source),
    ]) == 0
    assert _output(capsys)["effects"]["mutation"] is True

    assert main(["rebuild", "--library", str(root)]) == 0
    assert _output(capsys)["result"]["works"] == 1
    assert main(["search", "--library", str(root), "STD-1"]) == 0
    result = _output(capsys)
    assert result["result"]["results"][0]["id"] == work["id"]
    assert result["result"]["results"][0]["scientific_support"] == "not_assessed"

    assert main([
        "call", "work.list", "--arguments", json.dumps({"library": str(root)})
    ]) == 0
    assert _output(capsys)["result"][0]["id"] == work["id"]


def test_cli_exposes_every_operation_contract(capsys):
    assert main(["operations"]) == 0
    assert set(_output(capsys)["operations"]) == set(OPERATIONS)
    assert main(["operations", "metadata.crossref.resolve"]) == 0
    contract = _output(capsys)
    assert contract["operation"] == "libraryos.metadata.crossref.resolve"
    assert contract["description"]
    assert contract["effects"] == {"mutation": True, "network": True}


def test_generic_cli_call_reaches_public_operation(tmp_path, capsys):
    root = tmp_path / "library"
    initialize_library(root)
    assert main(
        [
            "call",
            "library.status",
            "--arguments",
            json.dumps({"library": str(root)}),
        ]
    ) == 0
    payload = _output(capsys)
    assert payload["operation"] == "libraryos.library.status"
    assert payload["result"]["descriptor"]["id"]
