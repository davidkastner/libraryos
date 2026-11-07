import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
from libraryos.operations import invoke


class _SourceHandler(BaseHTTPRequestHandler):
    routes = {}
    requests = []

    def do_GET(self):  # noqa: N802
        self.requests.append(self.path)
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
    _SourceHandler.requests = []
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
    return {"candidate_id": candidate["candidate"]["id"]}


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


def test_non_candidate_identity_requires_method_before_network(tmp_path, source_server):
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
            identity_evidence={"title": "Remote report"},
        )
    assert caught.value.code == "identity_evidence_invalid"
    assert list_jobs(root) == []


def test_identity_evidence_error_exposes_documented_recovery(tmp_path, source_server):
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
            identity_evidence={"doi": "10.1000/example"},
        )

    assert caught.value.code == "identity_evidence_invalid"
    assert caught.value.details == {
        "allowed_fields": [
            "candidate_id",
            "expected_sha256",
            "identifiers",
            "method",
            "title",
        ]
    }
    assert caught.value.next_actions[0]["action"] == "use_registered_candidate"
    assert list_jobs(root) == []


def test_candidate_only_identity_rejects_unknown_candidate_before_network(
    tmp_path,
    source_server,
):
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
            identity_evidence={"candidate_id": "missing-candidate"},
        )
    assert caught.value.code == "source_candidate_not_found"
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
    assert result["next_actions"] == [
        {
            "operation": "source.prepare",
            "purpose": (
                "Prepare searchable and visual navigation derivatives from the "
                "acquired immutable source."
            ),
            "arguments": {
                "library": str(root.resolve()),
                "work_id": work["id"],
                "source_id": source["id"],
            },
        }
    ]
    assert source["identity_status"] == "verified"
    assert source["identity_method"] == "expected_sha256"
    assert source["canonical_url"] == f"{base_url}/report.txt"
    assert (root / "works" / work["id"] / source["path"]).read_bytes() == body
    job = list_jobs(root)[0]
    assert job["status"] == "succeeded"
    assert job["arguments"]["canonical_url"] == f"{base_url}/report.txt"
    assert "secret" not in json.dumps(job)
    assert "review" not in job


def test_xml_suffix_media_type_is_accepted_as_xml(tmp_path, source_server):
    base_url, routes = source_server
    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<collection><document><id>123</id>"
        b"<passage><text>Remote report</text></passage>"
        b"</document></collection>"
    )
    routes["/report.xml"] = (200, "application/xslt+xml", body)
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Remote report")

    result = acquire_url(
        root,
        work["id"],
        f"{base_url}/report.xml",
        role="full_text",
        access="open_access",
        allowed_access=["open_access"],
        expected_media_type="application/xml",
        identity_evidence={
            "method": "expected_sha256",
            "expected_sha256": hashlib.sha256(body).hexdigest(),
        },
    )

    assert result["created"] is True
    source = get_work(root, work["id"])["sources"][0]
    assert source["media_type"] == "application/xml"
    assert source["path"].endswith(".xml")
    assert (root / "works" / work["id"] / source["path"]).read_bytes() == body
    assert not list((root / "quarantine").glob("*/manifest.json"))


@pytest.mark.parametrize("declared_media_type", ["image/jpeg", "application/octet-stream"])
def test_jpeg_figure_is_acquired_unchanged(tmp_path, source_server, declared_media_type):
    base_url, routes = source_server
    body = (Path(__file__).parent / "fixtures" / "one-pixel.jpg").read_bytes()
    routes["/figure"] = (200, declared_media_type, body)
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="Illustrated article")
    url = f"{base_url}/figure"

    result = acquire_url(
        root,
        work["id"],
        url,
        role="figure",
        access="open_access",
        allowed_access=["open_access"],
        expected_media_type="image/jpeg",
        identity_evidence=_verified_candidate(root, work["id"], url),
    )

    assert result["created"] is True
    source = get_work(root, work["id"])["sources"][0]
    assert source["media_type"] == "image/jpeg"
    assert source["path"].endswith(".jpg")
    assert source["sha256"] == hashlib.sha256(body).hexdigest()
    assert (root / "works" / work["id"] / source["path"]).read_bytes() == body
    assert not list((root / "quarantine").glob("*/manifest.json"))
    assert validate_library(root)["valid"] is True


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
            "/interstitial.pdf",
            "application/pdf",
            b"<html><body>Preparing to download. Proof of work is required.</body></html>",
            "download_interstitial",
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
        (
            "/fake.jpg",
            "image/jpeg",
            b"<html><body>This is an HTML response, not the requested figure.</body></html>",
            "type_mismatch",
        ),
        pytest.param(
            "/broken.jpg",
            "image/jpeg",
            (Path(__file__).parent / "fixtures" / "one-pixel.jpg").read_bytes()[:-2],
            "corrupt_source",
            id="truncated-jpeg",
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
    assert caught.value.details["detected_media_type"] == record["media_type"]
    assert caught.value.next_actions[0]["action"] == "try_registered_alternate_source"


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


def test_public_candidate_binds_query_without_persisting_transient_url(tmp_path, source_server):
    base_url, routes = source_server
    body = b"The selected source asset.\n"
    routes['/asset.txt'] = (200, 'text/plain', body)
    root = str(tmp_path / 'library')
    invoke('library.initialize', dict(path=root))
    work = invoke('work.create', dict(library=root, work_type='report', title='Query asset fixture'))['result']
    first_url = f'{base_url}/asset.txt?id=first&token=private-fixture-value'
    second_url = f'{base_url}/asset.txt?id=second&token=private-fixture-value'
    arguments = dict(library=root, work_id=work['id'], provider='fixture', access='open_access',
        identity_method='provider_exact_asset', verified_by='fixture', verified_at='2026-09-19T12:00:00Z')
    first = invoke('source.candidate.register', dict(arguments, url=first_url))['result']
    repeated = invoke('source.candidate.register', dict(arguments, url=first_url+'#page=2'))['result']
    second = invoke('source.candidate.register', dict(arguments, url=second_url))['result']
    assert first['candidate']['id'] == repeated['candidate']['id']
    assert first['candidate']['id'] != second['candidate']['id']
    assert first['candidate']['url'] == second['candidate']['url'] == f'{base_url}/asset.txt'
    assert len(first['candidate']['request_url_sha256']) == 64
    acquire = dict(library=root, work_id=work['id'], role='full_text', access='open_access',
        allowed_access=['open_access'], identity_evidence=dict(candidate_id=first['candidate']['id']))
    for wrong_url in [second_url, first_url.replace('private-fixture-value', 'changed-value'),
            first_url+'&part=second', f'{base_url}/asset.txt?token=private-fixture-value&id=first',
            first_url.replace('://', '://fixture:credential@')]:
        with pytest.raises(StorageError) as caught:
            invoke('source.acquire', dict(acquire, url=wrong_url))
        assert caught.value.code == 'source_candidate_mismatch'
    assert _SourceHandler.requests == []
    assert list_jobs(root) == []
    assert get_work(root, work['id'])['sources'] == []
    result = invoke('source.acquire', dict(acquire, url=first_url))['result']
    assert _SourceHandler.requests == ['/asset.txt?id=first&token=private-fixture-value']
    assert result['source']['sha256'] == hashlib.sha256(body).hexdigest()
    assert result['source']['canonical_url'] == f'{base_url}/asset.txt'
    job = list_jobs(root)[0]
    assert job['arguments']['source_candidate_id'] == first['candidate']['id']
    assert job['arguments']['request_url_sha256'] == first['candidate']['request_url_sha256']
    for path in (tmp_path / 'library').rglob('*.json'):
        text = path.read_text()
        assert 'private-fixture-value' not in text
        assert 'id=first' not in text and 'id=second' not in text
    assert validate_library(root)['valid']


def test_candidate_without_request_binding_is_not_acquisition_authority(tmp_path, source_server):
    base_url, routes = source_server
    routes['/asset.txt'] = (200, 'text/plain', b'No unbound retrieval.\n')
    root = tmp_path / 'library'
    initialize_library(root)
    work = create_work(root, work_type='report', title='Unbound candidate fixture')
    evidence = _verified_candidate(root, work['id'], f'{base_url}/asset.txt')
    manifest = get_work(root, work['id'])
    manifest['source_candidates'][0].pop('request_url_sha256', None)
    (root / 'works' / work['id'] / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(StorageError) as caught:
        acquire_url(root, work['id'], f'{base_url}/asset.txt', role='full_text', access='open_access',
            allowed_access=['open_access'], identity_evidence=evidence)
    assert caught.value.code == 'source_candidate_unbound'
    assert _SourceHandler.requests == [] and list_jobs(root) == []
    fresh = _verified_candidate(root, work['id'], f'{base_url}/asset.txt')
    assert fresh['candidate_id'] != evidence['candidate_id']
    result = acquire_url(root, work['id'], f'{base_url}/asset.txt', role='full_text', access='open_access',
        allowed_access=['open_access'], identity_evidence=fresh)
    assert result['source']['identity_status'] == 'verified'
