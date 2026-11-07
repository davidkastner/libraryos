import pytest

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


@pytest.mark.parametrize(("suffix", "sensitive"), [
    ("HOST[:PORT]", False),
    ("[invalid]/article", False),
    ("[::1/article", False),
    ("[::1]:8080/article?id=paper", False),
    ("example.org/path@name?email=a@b#section", False),
    ("example.org/article#section?token=fragment", False),
    ("name:private-value@[invalid]/article", True),
    ("[invalid]/article?token=private-value", True),
    ("HOST[:PORT]/article?X-Amz-Signature=private-value", True),
    ("[::1]:8080/article?key=private-value#section", True),
])
def test_symbolic_and_malformed_urls_preserve_privacy_checks(tmp_path, suffix, sensitive):
    target = tmp_path / "help.txt"
    target.write_text("https://" + suffix)

    report = scan_privacy(target)

    assert report["valid"] is not sensitive
    assert {row["code"] for row in report["findings"]} == (
        {"sensitive_url_present"} if sensitive else set()
    )
    assert "private-value" not in str(report)
    assert report == scan_privacy(target)


def test_symbolic_url_does_not_stop_scanning_later_secrets(tmp_path):
    target = tmp_path / "export"
    target.mkdir()
    (target / "first.txt").write_text("https://" + "HOST[:PORT]")
    secret = "ghp_" + "b" * 30
    (target / "second.txt").write_text(secret)

    report = scan_privacy(target)

    assert report["valid"] is False
    assert report["counts"]["files_scanned"] == 2
    assert [(row["path"], row["code"]) for row in report["findings"]] == [
        ("second.txt", "github_token")
    ]
    assert secret not in str(report)
