import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from libraryos import (
    StorageError,
    acquire_url,
    create_work,
    get_work,
    initialize_library,
    list_jobs,
    register_source_candidate,
    validate_library,
)


class _SourceHandler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        status, media_type, body = self.routes.get(
            path,
            (404, "text/plain", b"not found"),
        )
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


@pytest.fixture
def source_server():
    _SourceHandler.routes = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SourceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", _SourceHandler.routes
    finally:
        server.shutdown()
        server.server_close()


def _verified_candidate(root, work_id, url, access="open_access"):
    candidate = register_source_candidate(
        root,
        work_id,
        url=url,
        provider="fixture-provider",
        access=access,
        identity_method="fixture-provider-identity",
        verified_by="fixture-provider",
        verified_at="2026-09-19T12:00:00Z",
    )
    return {"method": "registered_candidate", "candidate_id": candidate["candidate"]["id"]}


def test_acquisition_requires_access_authorization(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    with pytest.raises(StorageError) as caught:
        acquire_url(
            root,
            work["id"],
            "https://example.invalid/report.txt",
            role="full_text",
            access="institutional",
            allowed_access=["open_access"],
        )
    assert caught.value.code == "access_not_authorized"
    assert list_jobs(root) == []


def test_acquisition_requires_identity_before_network(tmp_path, source_server):
    base_url, routes = source_server
    routes["/report.txt"] = (200, "text/plain", b"Remote report\n")
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    with pytest.raises(StorageError) as caught:
        acquire_url(
            root,
            work["id"],
            f"{base_url}/report.txt",
            role="full_text",
            access="open_access",
            allowed_access=["open_access"],
        )
    assert caught.value.code == "identity_evidence_required"
    assert list_jobs(root) == []


def test_bounded_acquisition_records_verified_source_without_inspection(
    tmp_path,
    source_server,
):
    base_url, routes = source_server
    body = b"Remote report\nThis is the complete report body.\n"
    routes["/report.txt"] = (200, "text/plain", body)
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    result = acquire_url(
        root,
        work["id"],
        f"{base_url}/report.txt?token=secret#fragment",
        role="full_text",
        access="open_access",
        allowed_access=["open_access"],
        expected_media_type="text/plain",
        identity_evidence={
            "method": "expected_sha256",
            "expected_sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    assert result["created"] is True
    source = get_work(root, work["id"])["sources"][0]
    assert source["identity_status"] == "verified"
    assert source["identity_method"] == "expected_sha256"
    assert source["canonical_url"] == f"{base_url}/report.txt"
    assert (root / "works" / work["id"] / source["path"]).read_bytes() == body
    job = list_jobs(root)[0]
    assert job["status"] == "succeeded"
    assert job["arguments"]["canonical_url"] == f"{base_url}/report.txt"
    assert "secret" not in json.dumps(job)
    assert "review" not in job


@pytest.mark.parametrize(
    ("path", "media_type", "body", "expected_code"),
    [
        (
            "/landing",
            "text/html",
            b"<html><head><title>Publisher</title></head><body>Article page</body></html>",
            "landing_page",
        ),
        (
            "/abstract",
            "text/html",
            b"<html><body><div id=\"abstract\">Remote report abstract</div></body></html>",
            "abstract_only",
        ),
        (
            "/challenge",
            "text/html",
            b"<html><body>Verify you are human with CAPTCHA</body></html>",
            "bot_challenge",
        ),
        (
            "/login",
            "text/html",
            b"<html><body>Institutional login required to access this article</body></html>",
            "access_restricted",
        ),
        (
            "/fake.pdf",
            "application/pdf",
            b"<html><body>This is actually HTML with enough content to inspect.</body></html>",
            "type_mismatch",
        ),
        (
            "/broken.pdf",
            "application/pdf",
            b"%PDF-1.7\nincomplete",
            "corrupt_source",
        ),
    ],
)
def test_rejected_bytes_are_quarantined_not_attached(
    tmp_path,
    source_server,
    path,
    media_type,
    body,
    expected_code,
):
    base_url, routes = source_server
    routes[path] = (200, media_type, body)
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    with pytest.raises(StorageError) as caught:
        acquire_url(
            root,
            work["id"],
            f"{base_url}{path}",
            role="full_text",
            access="open_access",
            allowed_access=["open_access"],
            identity_evidence=_verified_candidate(root, work["id"], f"{base_url}{path}"),
        )
    assert caught.value.code == expected_code
    assert get_work(root, work["id"])["sources"] == []
    manifest_path = next((root / "quarantine").glob("*/manifest.json"))
    record = json.loads(manifest_path.read_text())
    assert record["reason"] == expected_code
    assert (manifest_path.parent / record["path"]).read_bytes() == body
    assert validate_library(root)["valid"] is True
    job = list_jobs(root)[0]
    assert job["status"] == "failed"
    assert job["result"]["quarantine_id"] == record["id"]
    assert job["exceptions"][0]["category"] == expected_code


def test_unmatched_identity_is_quarantined(tmp_path, source_server):
    base_url, routes = source_server
    routes["/other.txt"] = (200, "text/plain", b"A different document entirely.\n")
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(
        root,
        work_type="article",
        title="Remote report",
        identifiers=[{"scheme": "doi", "value": "10.1234/example"}],
    )
    with pytest.raises(StorageError) as caught:
        acquire_url(
            root,
            work["id"],
            f"{base_url}/other.txt",
            role="full_text",
            access="open_access",
            allowed_access=["open_access"],
            identity_evidence={
                "method": "content_identifier",
                "identifiers": [{"scheme": "doi", "value": "10.1234/example"}],
            },
        )
    assert caught.value.code == "ambiguous_identity"
    assert get_work(root, work["id"])["sources"] == []


def test_http_failures_are_categorized_without_quarantine(tmp_path, source_server):
    base_url, _ = source_server
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    with pytest.raises(StorageError) as caught:
        acquire_url(
            root,
            work["id"],
            f"{base_url}/missing?signature=secret",
            role="full_text",
            access="open_access",
            allowed_access=["open_access"],
            identity_evidence=_verified_candidate(
                root,
                work["id"],
                f"{base_url}/missing?signature=secret",
            ),
        )
    assert caught.value.code == "not_found"
    assert not list((root / "quarantine").iterdir())
    job = list_jobs(root)[0]
    assert job["exceptions"][0]["category"] == "not_found"
    assert "secret" not in json.dumps(job)


def test_registered_candidate_cannot_be_replayed_for_another_url(
    tmp_path,
    source_server,
):
    base_url, routes = source_server
    routes["/expected.txt"] = (200, "text/plain", b"Expected source.\n")
    routes["/substitute.txt"] = (200, "text/plain", b"Substitute source.\n")
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    evidence = _verified_candidate(
        root,
        work["id"],
        f"{base_url}/expected.txt?temporary=one",
    )
    with pytest.raises(StorageError) as caught:
        acquire_url(
            root,
            work["id"],
            f"{base_url}/substitute.txt?temporary=two",
            role="full_text",
            access="open_access",
            allowed_access=["open_access"],
            identity_evidence=evidence,
        )
    assert caught.value.code == "source_candidate_mismatch"
    assert list_jobs(root) == []


def test_registered_candidate_provenance_reaches_source(tmp_path, source_server):
    base_url, routes = source_server
    body = b"Provider-verified report.\n"
    routes["/report.txt"] = (200, "text/plain", body)
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")
    evidence = _verified_candidate(root, work["id"], f"{base_url}/report.txt")
    result = acquire_url(
        root,
        work["id"],
        f"{base_url}/report.txt",
        role="full_text",
        access="open_access",
        allowed_access=["open_access"],
        identity_evidence=evidence,
    )
    assert (
        result["source"]["identity_method"]
        == "registered_candidate:fixture-provider-identity"
    )
