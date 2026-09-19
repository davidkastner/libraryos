from libraryos import scan_privacy


def test_privacy_scan_reports_without_echoing_sensitive_values(tmp_path):
    target = tmp_path / "export"
    target.mkdir()
    secret = "ghp_" + "a" * 30
    private_path = "/" + "Users" + "/researcher/private/file"
    sensitive_url = "https://" + "user@example.org/paper?" + "token=value"
    (target / "metadata.json").write_text(
        f'{{"url":"{sensitive_url}",'
        f'"credential":"{secret}","path":"{private_path}"}}'
    )
    (target / "paper.pdf").write_bytes(b"%PDF example")

    report = scan_privacy(target)
    assert report["valid"] is False
    assert {finding["code"] for finding in report["findings"]} == {
        "github_token",
        "publication_bytes_present",
        "sensitive_url_present",
        "user_path_present",
    }
    assert report["contents_emitted"] is False
    assert secret not in str(report)
    assert "token=value" not in str(report)


def test_privacy_scan_is_deterministic_and_can_allow_pdf_bytes(tmp_path):
    target = tmp_path / "private-library"
    target.mkdir()
    (target / "paper.pdf").write_bytes(b"%PDF private")
    (target / "record.json").write_text('{"url":"https://example.org/article"}')

    first = scan_privacy(target, allow_publication_bytes=True)
    second = scan_privacy(target, allow_publication_bytes=True)
    assert first == second
    assert first["valid"] is True
    assert first["counts"]["files_scanned"] == 2
