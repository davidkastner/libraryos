import json
from pathlib import Path

import pytest

from libraryos.schemas import SchemaError, available_schemas, load_schema, validate_record

FIXTURES = Path(__file__).parent / "fixtures"


def test_expected_schemas_are_packaged():
    assert {
        "assessment-v1",
        "collection-v1",
        "collection-registration-v1",
        "common-v1",
        "library-v1",
        "job-v1",
        "operation-error-v1",
        "operation-result-v1",
        "review-v1",
        "trash-transaction-v1",
        "work-v1",
    }.issubset(available_schemas())


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")))
def test_canonical_fixtures_validate(path):
    record = json.loads(path.read_text())
    validate_record(record)


def test_domain_extension_is_preserved_by_contract():
    record = json.loads((FIXTURES / "collection-general.json").read_text())
    record["extensions"] = {"org.example.physics": {"instrument": "SQUID"}}
    validate_record(record)


def test_unknown_top_level_field_is_rejected():
    record = json.loads((FIXTURES / "collection-general.json").read_text())
    record["mechanism"] = {}
    with pytest.raises(SchemaError) as caught:
        validate_record(record)
    assert caught.value.code == "schema_validation_failed"
    assert caught.value.path == ""


def test_review_requires_exact_source_hash():
    record = json.loads((FIXTURES / "review-source-bound.json").read_text())
    record["source_sha256"] = []
    with pytest.raises(SchemaError) as caught:
        validate_record(record)
    assert caught.value.path == "/source_sha256"


def test_schema_can_be_loaded_by_uri():
    schema = load_schema("https://libraryos.dev/schemas/collection/v1")
    assert schema["$id"] == "https://libraryos.dev/schemas/collection/v1"


def test_formats_are_enforced():
    record = json.loads((FIXTURES / "review-source-bound.json").read_text())
    record["id"] = "not-a-uuid"
    with pytest.raises(SchemaError) as caught:
        validate_record(record)
    assert caught.value.path == "/id"
