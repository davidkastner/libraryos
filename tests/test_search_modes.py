import json

import pytest

from libraryos import create_work, import_source, initialize_library, register_derivative
from libraryos.catalog import prepared_query, search_prepared
from libraryos.cli import main
from libraryos.operations import invoke, operation_contract
from libraryos.storage import StorageError


@pytest.fixture
def prepared_library(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    works = []
    texts = [
        'Asp-102 protonated His-57 acetyl-CoA 2.7.1.1 ATP/GTP alpha,beta "quoted" NAD+ → NADH',
        "ATP unrelated observation",
        "GTP unrelated observation",
    ]
    for index, text in enumerate(texts):
        work = create_work(root, work_type="article", title=f"Search fixture {index}")
        supplied = tmp_path / f"source-{index}.txt"
        supplied.write_text(text)
        source = import_source(root, work["id"], supplied, role="full_text")["source"]
        register_derivative(
            root, work["id"], supplied, role="agent_markdown",
            input_sha256=[source["sha256"]], generator_name="fixture", generator_version="1",
            media_type="text/plain",
        )
        works.append(work)
    return root, works


@pytest.mark.parametrize(
    "query", ["Asp-102 protonated", "His-57", "acetyl-CoA", "2.7.1.1", "ATP/GTP", "alpha,beta", '"quoted"']
)
def test_literal_search_handles_punctuation_without_changing_fts(prepared_library, query):
    root, works = prepared_library
    hits = search_prepared(root, query, query_mode="literal")
    assert [hit["work_id"] for hit in hits] == [works[0]["id"]]
    assert all(hit["scientific_support"] == "not_assessed" for hit in hits)


def test_explicit_fts_retains_boolean_semantics_and_literal_treats_operators_as_text(prepared_library):
    root, works = prepared_library
    expected = {work["id"] for work in works}
    assert {hit["work_id"] for hit in search_prepared(root, "ATP OR GTP")} == expected
    assert {hit["work_id"] for hit in search_prepared(root, "ATP OR GTP", query_mode="fts")} == expected
    assert search_prepared(root, "ATP OR GTP", query_mode="literal") == []
    assert len(search_prepared(root, '"ATP" NOT "GTP"')) == 1


def test_literal_search_preserves_work_scope(prepared_library):
    root, works = prepared_library
    assert search_prepared(root, "Asp-102", query_mode="literal", work_ids=[works[1]["id"]]) == []
    hits = search_prepared(root, "Asp-102", query_mode="literal", work_ids=[works[0]["id"]])
    assert [hit["work_id"] for hit in hits] == [works[0]["id"]]
    assert hits[0]["work_scope"] == "explicit"


def test_invalid_fts_is_structured_and_does_not_silently_fall_back(prepared_library, capsys):
    root, _ = prepared_library
    arguments = {"library": str(root), "query": "Asp-102"}
    assert main(["call", "search.prepared", "--arguments", json.dumps(arguments), "--format", "text"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error"]["code"] == "search_query_invalid"
    assert payload["error"]["details"]["query_mode"] == "fts"
    assert payload["error"]["details"]["effective_query"] == "Asp-102"
    assert payload["error"]["next_actions"][0]["action"] == "use_literal_terms"
    assert "Traceback" not in captured.out + captured.err


def test_operation_accepts_literal_mode_and_contract_lists_modes(prepared_library):
    root, works = prepared_library
    arguments = {"library": str(root), "query": "His-57", "query_mode": "literal"}
    result = invoke("search.prepared", arguments)
    assert result["result"][0]["work_id"] == works[0]["id"]
    schema = operation_contract("search.prepared")["arguments_schema"]
    assert schema["properties"]["query_mode"] == {"enum": ["fts", "literal"], "default": "fts"}
    with pytest.raises(StorageError, match="Operation arguments are invalid"):
        invoke("search.prepared", {**arguments, "query_mode": "unknown"})


def test_search_cli_defaults_to_literal_and_reports_zero_hit_interpretation(prepared_library, capsys):
    root, works = prepared_library
    assert main(["search", "--library", str(root), "--full-text", "His-57", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["query_mode"] == "literal"
    assert payload["result"]["effective_query"] == '"His-57"'
    assert payload["result"]["results"][0]["work_id"] == works[0]["id"]

    assert main([
        "search", "--library", str(root), "--full-text", "His-57",
        "--work-id", works[1]["id"],
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["results"] == []
    assert payload["result"]["query_mode"] == "literal"
    assert payload["result"]["effective_query"] == '"His-57"'
    assert payload["result"]["work_ids"] == [works[1]["id"]]


def test_search_cli_can_select_explicit_fts(prepared_library, capsys):
    root, works = prepared_library
    assert main([
        "search", "--library", str(root), "--full-text", "--query-mode", "fts", "ATP OR GTP",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["effective_query"] == "ATP OR GTP"
    assert len(payload["result"]["results"]) == len(works)


@pytest.mark.parametrize("query", ["", " \t "])
def test_blank_search_is_a_structured_error(query):
    with pytest.raises(StorageError, match="non-empty") as error:
        prepared_query(query, "literal")
    assert error.value.code == "search_query_invalid"


def test_search_cli_rejects_ignored_full_text_options(prepared_library, capsys):
    root, _ = prepared_library
    assert main(["search", "--library", str(root), "--query-mode", "literal", "fixture"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "arguments_invalid"


@pytest.mark.parametrize("arguments, path", [(["--limit", "0"], "limit"), (["--work-id", ""], "work_ids")])
def test_search_cli_invalid_bounds_are_structured(prepared_library, capsys, arguments, path):
    root, _ = prepared_library
    assert main(["search", "--library", str(root), "--full-text", "ATP", *arguments]) == 2
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert error["code"] == "arguments_invalid"
    assert error["path"] == path
    assert "Traceback" not in captured.out + captured.err


@pytest.mark.parametrize("query, expression", [
    ("NAD+ → NADH", '"NAD+" AND "NADH"'),
    ("ATP / GTP", '"ATP" AND "GTP"'),
])
def test_literal_search_reports_standalone_punctuation_omission(prepared_library, capsys, query, expression):
    root, works = prepared_library
    assert main(["search", "--library", str(root), "--full-text", query]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["query"] == query
    assert result["effective_query"] == expression
    assert [hit["work_id"] for hit in result["results"]] == [works[0]["id"]]


@pytest.mark.parametrize("query", ["/", "→", "+ -", "\"\"", "( )"])
def test_literal_punctuation_only_query_is_rejected(query):
    with pytest.raises(StorageError, match="letter or number") as error:
        prepared_query(query, "literal")
    assert error.value.code == "search_query_invalid"
