import json
import threading
import urllib.request

from libraryos import create_work, import_source, initialize_library
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
        assert "/assets/libraryos-icon-64.png" in html
        assert 'id="show-more"' in html
        assert "Show 100 More" in html
        assert 'class="copy-button"' in html
        assert 'aria-label="Copy PDF path"' in html
        icon = urllib.request.urlopen(f"{base}/assets/libraryos-icon-64.png")
        assert icon.headers["Content-Type"] == "image/png"
        assert len(icon.read()) > 1_000
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
