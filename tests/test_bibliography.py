import json

from libraryos import (
    import_bibliography,
    initialize_library,
    list_works,
    parse_bibliography,
    validate_library,
)


def test_parse_bibtex_with_nested_braces_and_authors():
    records = parse_bibliography(
        """
        @article{Example2026,
          title = {A {Nested} Scientific Title},
          author = {Example, Ada and Grace Hopper},
          journal = {Journal of Examples},
          year = {2026},
          doi = {https://doi.org/10.1234/EXAMPLE}
        }
        """,
        format="bibtex",
    )
    assert records == [
        {
            "work_type": "article",
            "title": "A {Nested} Scientific Title",
            "identifiers": [
                {
                    "scheme": "doi",
                    "value": "https://doi.org/10.1234/EXAMPLE",
                    "normalized": "10.1234/example",
                }
            ],
            "metadata": {
                "authors": [
                    {"family": "Example", "given": "Ada"},
                    "Grace Hopper",
                ],
                "container_title": "Journal of Examples",
                "issued": 2026,
            },
        }
    ]


def test_import_csl_json_is_provenanced_and_idempotent(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    source = tmp_path / "library.json"
    source.write_text(
        json.dumps(
            [
                {
                    "type": "article-journal",
                    "title": "A CSL article",
                    "author": [{"family": "Example", "given": "Ada"}],
                    "container-title": "General Science",
                    "issued": {"date-parts": [[2024, 1, 2]]},
                    "DOI": "10.1234/CSL",
                }
            ]
        )
    )

    first = import_bibliography(root, source)
    second = import_bibliography(root, source)
    assert first["created"] == 1
    assert second["existing"] == 1
    work = list_works(root)[0]
    assert work["work"]["metadata_status"] == "verified"
    assert work["work"]["metadata_imports"][0]["format"] == "csl-json"
    assert work["work"]["metadata_imports"][0]["source_sha256"] == first["source_sha256"]
    assert validate_library(root)["valid"] is True


def test_import_ris_and_bibtex_without_persistent_identifier(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    ris = tmp_path / "works.ris"
    ris.write_text(
        "\n".join(
            [
                "TY  - RPRT",
                "ID  - REPORT-7",
                "TI  - A technical report",
                "AU  - Example, Ada",
                "PY  - 2022/03/01",
                "UR  - https://example.org/report?edition=2",
                "ER  -",
            ]
        )
    )
    bib = tmp_path / "works.bib"
    bib.write_text("@misc{opaque-key, title={An uncatalogued object}}")

    assert import_bibliography(root, ris)["created"] == 1
    assert import_bibliography(root, bib)["created"] == 1
    works = list_works(root)
    assert len(works) == 2
    identifiers = {
        (item["scheme"], item["normalized"])
        for work in works
        for item in work["work"]["identifiers"]
    }
    assert ("url", "https://example.org/report?edition=2") in identifiers
    assert ("bibtex-key", "opaque-key") in identifiers
