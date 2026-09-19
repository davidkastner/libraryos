import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from libraryos import (
    StorageError,
    create_work,
    initialize_library,
    normalize_identifier,
    resolve_identifier,
    validate_library,
)


@pytest.mark.parametrize(
    ("identifier", "scheme", "normalized"),
    [
        (
            {"scheme": "DOI", "value": " https://doi.org/10.1234%2FExample "},
            "doi",
            "10.1234/example",
        ),
        ({"scheme": "pmid", "value": "PMID:000123"}, "pmid", "123"),
        (
            {
                "scheme": "pmcid",
                "value": "https://pmc.ncbi.nlm.nih.gov/articles/PMC00123/",
            },
            "pmcid",
            "PMC123",
        ),
        ({"scheme": "arXiv", "value": "arXiv:hep-th/9901001V2"}, "arxiv", "hep-th/9901001v2"),
        (
            {"scheme": "url", "value": "HTTPS://Example.COM:443/a?x=1#section"},
            "url",
            "https://example.com/a?x=1",
        ),
        ({"scheme": "isbn", "value": "0-306-40615-2"}, "isbn", "0306406152"),
        ({"scheme": "local", "value": "Case Sensitive"}, "local", "Case Sensitive"),
    ],
)
def test_identifier_normalization(identifier, scheme, normalized):
    result = normalize_identifier(identifier)
    assert result["scheme"] == scheme
    assert result["normalized"] == normalized
    assert result["value"] == identifier["value"].strip()


def test_unknown_scheme_accepts_explicit_normalized_alias():
    result = normalize_identifier(
        {"scheme": "accession", "value": " ABC 7 ", "normalized": "abc:7"}
    )
    assert result == {
        "scheme": "accession",
        "value": "ABC 7",
        "normalized": "abc:7",
    }


@pytest.mark.parametrize(
    "identifier",
    [
        {"scheme": "doi", "value": "not-a-doi"},
        {"scheme": "pmid", "value": "12x"},
        {"scheme": "pmcid", "value": "PMC0"},
        {"scheme": "arxiv", "value": "something"},
        {"scheme": "url", "value": "ftp://example.org/file"},
        {"scheme": "isbn", "value": "0-306-40615-3"},
    ],
)
def test_invalid_recognized_identifier_is_rejected(identifier):
    with pytest.raises(StorageError) as caught:
        normalize_identifier(identifier)
    assert caught.value.code == "identifier_invalid"


def test_creation_normalizes_resolves_and_rejects_duplicate_alias(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    first = create_work(
        root,
        work_type="article",
        title="First title",
        identifiers=[{"scheme": "doi", "value": "https://doi.org/10.1234/EXAMPLE"}],
    )
    assert first["work"]["identifiers"][0]["normalized"] == "10.1234/example"
    assert resolve_identifier(root, "DOI", "doi:10.1234/example")["id"] == first["id"]

    with pytest.raises(StorageError) as caught:
        create_work(
            root,
            work_type="article",
            title="Different title",
            identifiers=[{"scheme": "doi", "value": "10.1234/example"}],
        )
    assert caught.value.code == "identifier_conflict"
    assert len(list((root / "works").glob("*/manifest.json"))) == 1


def test_similar_titles_do_not_merge_and_versions_are_explicit(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    article = create_work(
        root,
        work_type="article",
        title="The same study",
        identifiers=[{"scheme": "doi", "value": "10.1234/article"}],
    )
    preprint = create_work(
        root,
        work_type="preprint",
        title="The same study",
        identifiers=[{"scheme": "arxiv", "value": "2401.01234v1"}],
        relations=[{"type": "is-preprint-of", "target": article["id"]}],
    )
    assert article["id"] != preprint["id"]
    assert preprint["relations"] == [
        {"type": "is-preprint-of", "target": article["id"]}
    ]
    assert validate_library(root)["valid"] is True


def test_concurrent_creation_serializes_identity_check(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)

    def create(index):
        try:
            return create_work(
                root,
                work_type="report",
                title=f"Report {index}",
                identifiers=[{"scheme": "doi", "value": "10.1234/RACE"}],
            )["id"]
        except StorageError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(create, range(16)))
    assert sum(outcome == "identifier_conflict" for outcome in outcomes) == 15
    assert len(list((root / "works").glob("*/manifest.json"))) == 1


def test_validation_reports_manually_introduced_duplicate(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    first = create_work(
        root,
        work_type="article",
        title="One",
        identifiers=[{"scheme": "doi", "value": "10.1234/one"}],
    )
    second = create_work(
        root,
        work_type="article",
        title="Two",
        identifiers=[{"scheme": "doi", "value": "10.1234/two"}],
    )
    path = root / "works" / second["id"] / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["work"]["identifiers"] = [
        {
            "scheme": "doi",
            "value": "https://doi.org/10.1234/ONE",
            "normalized": "10.1234/one",
        }
    ]
    path.write_text(json.dumps(manifest))

    result = validate_library(root)
    assert result["valid"] is False
    finding = next(item for item in result["findings"] if item["code"] == "identifier_conflict")
    assert any(work_id in finding["message"] for work_id in (first["id"], second["id"]))
    assert "doi:10.1234/one" in finding["message"]
