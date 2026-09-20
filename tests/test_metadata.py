import hashlib
import json

import pytest

from libraryos import (
    CrossrefProvider,
    OpenAlexProvider,
    StorageError,
    accept_metadata_assertion,
    create_work,
    discover_relations,
    discover_sources,
    initialize_library,
    resolve_metadata,
    validate_library,
)


def _crossref_response(*, doi="10.1234/example", title="Resolved title"):
    value = {
        "message": {
            "DOI": doi,
            "title": [title],
            "container-title": ["Journal of Examples"],
            "publisher": "Example Society",
            "type": "journal-article",
            "author": [
                {
                    "given": "Ada",
                    "family": "Example",
                    "ORCID": "https://orcid.org/0000-0000-0000-0001",
                }
            ],
            "published-online": {"date-parts": [[2026, 9, 19]]},
            "link": [
                {
                    "URL": "https://publisher.example/article.pdf?" + "token=transient",
                    "content-type": "application/pdf",
                }
            ],
            "resource": {
                "primary": {"URL": "https://publisher.example/article#abstract"}
            },
        }
    }
    payload = json.dumps(value, sort_keys=True).encode()
    return value, payload


def _provider(**changes):
    response, payload = _crossref_response(**changes)

    def transport(url, headers, timeout):
        assert url.endswith("10.1234%2Fexample")
        assert headers["Accept"] == "application/json"
        assert timeout == 5
        return response, payload

    return CrossrefProvider(
        timeout=5,
        transport=transport,
        publisher_access="institutional",
    ), payload


def test_crossref_assertion_is_provenanced_and_requires_acceptance(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title=None,
        identifiers=[{"scheme": "doi", "value": "https://doi.org/10.1234/EXAMPLE"}],
    )
    provider, payload = _provider()

    resolved = resolve_metadata(root, work["id"], provider=provider)
    assertion = resolved["assertion"]
    assert resolved["accepted"] is False
    assert resolved["next_actions"] == [
        {
            "operation": "metadata.assertion.accept",
            "purpose": "Review and explicitly accept the resolved bibliographic metadata.",
            "arguments": {
                "library": str(root.resolve()),
                "work_id": work["id"],
                "assertion_id": assertion["id"],
            },
        }
    ]
    assert assertion["status"] == "candidate"
    assert assertion["provider_version"] == "rest-api-v1"
    assert assertion["raw_response_sha256"] == hashlib.sha256(payload).hexdigest()
    assert assertion["data"]["title"] == "Resolved title"
    assert work["work"]["title"] is None

    accepted = accept_metadata_assertion(root, work["id"], assertion["id"])
    assert accepted["accepted"] is True
    assert accepted["next_actions"] == [
        {
            "operation": "source.crossref.discover",
            "purpose": "Discover source candidates without retrieving bytes.",
            "arguments": {"library": str(root.resolve()), "work_id": work["id"]},
        }
    ]
    from libraryos import get_work

    current = get_work(root, work["id"])
    assert current["work"]["title"] == "Resolved title"
    assert current["work"]["metadata_status"] == "verified"
    assert current["work"]["metadata_assertions"][0]["status"] == "accepted"
    assert validate_library(root)["valid"] is True


def test_consequential_metadata_conflict_requires_explicit_override(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title="Curator title",
        identifiers=[{"scheme": "doi", "value": "10.1234/example"}],
    )
    provider, _ = _provider(title="Provider title")
    assertion = resolve_metadata(root, work["id"], provider=provider)["assertion"]
    assert assertion["status"] == "conflicting"
    assert assertion["conflicts"] == ["title"]

    with pytest.raises(StorageError) as caught:
        accept_metadata_assertion(root, work["id"], assertion["id"])
    assert caught.value.code == "metadata_conflict_requires_acceptance"

    accept_metadata_assertion(
        root,
        work["id"],
        assertion["id"],
        accept_conflicts=True,
    )
    from libraryos import get_work

    assert get_work(root, work["id"])["work"]["title"] == "Provider title"


def test_crossref_identity_mismatch_is_rejected_without_mutation(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title=None,
        identifiers=[{"scheme": "doi", "value": "10.1234/example"}],
    )
    provider, _ = _provider(doi="10.1234/different")
    with pytest.raises(StorageError) as caught:
        resolve_metadata(root, work["id"], provider=provider)
    assert caught.value.code == "provider_identity_conflict"
    from libraryos import get_work

    assert "metadata_assertions" not in get_work(root, work["id"])["work"]


def test_discovery_records_candidates_but_does_not_acquire(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title=None,
        identifiers=[{"scheme": "doi", "value": "10.1234/example"}],
    )
    provider, _ = _provider()
    result = discover_sources(root, work["id"], provider=provider)
    assert result["acquired"] is False
    assert result["scientific_inspection"] == "not_assessed"
    assert len(result["candidates"]) == 2
    assert len(result["next_actions"]) == 2
    for candidate, action in zip(result["candidates"], result["next_actions"], strict=True):
        assert action["operation"] == "source.acquire"
        assert action["arguments"]["url"] == candidate["candidate"]["url"]
        assert action["arguments"]["access"] == candidate["candidate"]["access"]
        assert action["arguments"]["identity_evidence"] == {
            "candidate_id": candidate["candidate"]["id"]
        }
        assert action["required_arguments"] == ["role"]

    from libraryos import get_work

    current = get_work(root, work["id"])
    assert current["sources"] == []
    assert {item["identity_method"] for item in current["source_candidates"]} == {
        "crossref_exact_doi_link",
        "crossref_exact_doi_resource",
    }
    # Persisted candidate URLs use the existing secret-free URL policy.
    assert {item["url"] for item in current["source_candidates"]} == {
        "https://publisher.example/article.pdf",
        "https://publisher.example/article",
    }


def test_openalex_relation_discovery_is_non_mutating_and_provenanced():
    responses = {
        "https://api.openalex.org/works/https%3A%2F%2Fdoi.org%2F10.1234%2Fexample": {
            "id": "https://openalex.org/W100",
            "referenced_works": [
                "https://openalex.org/W200",
                "https://openalex.org/W300",
            ],
        },
        "https://api.openalex.org/works?filter=openalex_id:W200|W300&per-page=2": {
            "results": [
                {
                    "id": "https://openalex.org/W200",
                    "doi": "https://doi.org/10.1000/REFERENCE",
                    "display_name": "Earlier work",
                    "publication_year": 2001,
                }
            ]
        },
        "https://api.openalex.org/works?filter=cites:W100&per-page=2": {
            "results": [
                {
                    "id": "https://openalex.org/W400",
                    "doi": "https://doi.org/10.1000/CITING",
                    "display_name": "Later work",
                    "publication_year": 2025,
                }
            ]
        },
    }

    def transport(url, headers, timeout):
        assert headers["Accept"] == "application/json"
        assert timeout == 5
        value = responses[url]
        return value, json.dumps(value).encode()

    result = discover_relations(
        {"scheme": "doi", "value": "10.1234/example"},
        provider=OpenAlexProvider(timeout=5, transport=transport),
        directions=["references", "citations"],
        limit=2,
    )

    assert result["provider"] == {"name": "openalex", "version": "api-v1"}
    assert result["persisted"] is False
    assert [item["relation"] for item in result["candidates"]] == [
        "references",
        "citations",
    ]
    assert result["candidates"][0]["identifiers"][0]["normalized"] == "10.1000/reference"
    assert result["candidates"][1]["scientific_support"] == "not_assessed"


def test_openalex_relation_discovery_rejects_unknown_direction():
    provider = OpenAlexProvider(transport=lambda *_args: ({}, b"{}"))
    with pytest.raises(StorageError) as caught:
        discover_relations(
            {"scheme": "doi", "value": "10.1234/example"},
            provider=provider,
            directions=["similar"],
        )
    assert caught.value.code == "relation_direction_invalid"
