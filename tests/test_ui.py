import json
import sqlite3
import threading
import urllib.request

from libraryos import (
    create_collection,
    create_work,
    import_source,
    initialize_library,
    put_collection,
    set_collection_membership,
)
from libraryos.server import create_server
from libraryos.works import query_works


def test_work_query_returns_bounded_searchable_summaries(tmp_path):
    root = tmp_path / "library"
    initialize_library(root, title="Research library")
    first = create_work(
        root,
        work_type="article",
        title="Catalytic mechanism",
        identifiers=[{"scheme": "doi", "value": "10.1000/mech"}],
        metadata={
            "authors": [{"given": "Ada", "family": "Lovelace"}],
            "container_title": "Journal of Catalysis",
            "issued": 2026,
        },
    )
    create_work(root, work_type="book", title="Unrelated")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
    import_source(
        root,
        first["id"],
        pdf,
        role="full_text",
        media_type="application/pdf",
    )

    result = query_works(root, query="lovelace", availability="local_pdf")

    assert result["total"] == 1
    assert result["items"][0]["title"] == "Catalytic mechanism"
    assert result["items"][0]["authors"] == ["Ada Lovelace"]
    assert result["items"][0]["pdf_count"] == 1
    assert result["items"][0]["scientific_support"] == "not_assessed"


def test_work_query_filters_by_collection_and_membership_can_change(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    included = create_work(root, work_type="article", title="Included paper")
    excluded = create_work(root, work_type="article", title="Excluded paper")
    collection = create_collection(
        root,
        collection_id="writing",
        title="Writing",
    )
    collection["members"] = [{"work": included["id"]}]
    put_collection(root, collection)

    result = query_works(root, collection_id="writing")

    assert result["total"] == 1
    assert result["items"][0]["id"] == included["id"]

    added = set_collection_membership(
        root,
        "writing",
        excluded["id"],
        included=True,
    )
    assert added["changed"] is True
    assert added["included"] is True
    assert query_works(root, collection_id="writing")["total"] == 2

    removed = set_collection_membership(
        root,
        "writing",
        included["id"],
        included=False,
    )
    assert removed["changed"] is True
    assert removed["included"] is False
    items = query_works(root, collection_id="writing")["items"]
    assert [item["id"] for item in items] == [excluded["id"]]


def test_work_query_uses_catalog_instead_of_scanning_manifests(tmp_path, monkeypatch):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Indexed paper")

    assert query_works(root)["items"][0]["id"] == work["id"]

    def fail_if_scanned(*_args, **_kwargs):
        raise AssertionError("interactive queries must not scan work manifests")

    monkeypatch.setattr("libraryos.works.list_works", fail_if_scanned)
    assert query_works(root, query="indexed")["items"][0]["id"] == work["id"]
    assert query_works(root, query="%")["total"] == 0


def test_catalog_stays_current_after_work_and_source_mutations(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    assert query_works(root)["total"] == 0

    work = create_work(root, work_type="article", title="New indexed paper")
    assert query_works(root, query="new indexed")["total"] == 1

    pdf = tmp_path / "new.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
    import_source(
        root,
        work["id"],
        pdf,
        role="full_text",
        media_type="application/pdf",
    )
    item = query_works(root, availability="local_pdf")["items"][0]
    assert item["id"] == work["id"]
    assert item["pdf_count"] == 1

    with sqlite3.connect(root / ".libraryos" / "index.sqlite") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_bound_ui_server_serves_app_and_injects_library_scope(tmp_path):
    root = tmp_path / "library"
    initialize_library(root, title="My papers")
    create_work(root, work_type="article", title="Visible paper")
    server, token = create_server(library=root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert "<title>Library</title>" in html
        assert "/assets/libraryos-icon.svg" in html
        assert "/assets/libraryos-icon.png" not in html
        assert "libraryos-icon-64.png" not in html
        assert "libraryos-icon-512.png" not in html
        assert 'id="show-more"' in html
        assert "Show 100 More" in html
        assert 'class="copy-button"' in html
        assert 'aria-label="Copy PDF path"' in html
        assert 'id="collection-nav"' in html
        assert 'id="new-collection"' in html
        assert 'class="collection-button"' in html
        icon = urllib.request.urlopen(f"{base}/assets/libraryos-icon.svg")
        assert icon.headers["Content-Type"] == "image/svg+xml"
        assert b"<svg" in icon.read()
        response = urllib.request.urlopen(f"{base}/")
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        assert token not in html

        request = urllib.request.Request(
            f"{base}/v1/operations/work.query",
            data=json.dumps({"query": "visible"}).encode(),
            method="POST",
            headers={
                "Cookie": cookie,
                "Content-Type": "application/json",
                "Origin": base,
            },
        )
        result = json.loads(urllib.request.urlopen(request).read())
        assert result["result"]["items"][0]["title"] == "Visible paper"
    finally:
        server.shutdown()
        server.server_close()
