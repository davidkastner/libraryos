import hashlib
import json

import pytest

from libraryos import (
    CrossrefProvider,
    StorageError,
    accept_metadata_assertion,
    create_work,
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
    assert assertion["status"] == "candidate"
    assert assertion["provider_version"] == "rest-api-v1"
    assert assertion["raw_response_sha256"] == hashlib.sha256(payload).hexdigest()
    assert assertion["data"]["title"] == "Resolved title"
    assert work["work"]["title"] is None

    accepted = accept_metadata_assertion(root, work["id"], assertion["id"])
    assert accepted["accepted"] is True
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
