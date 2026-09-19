import json

import pytest

from libraryos import StorageError, initialize_library, open_library, validate_library
from libraryos.storage import resolve_library_path


def test_initialize_and_open_library(tmp_path):
    root = tmp_path / "research"
    result = initialize_library(root, title="Research Library")

    opened_root, descriptor = open_library(root)
    assert opened_root == root
    assert descriptor["id"] == result["id"]
    assert descriptor["title"] == "Research Library"
    assert (root / "works").is_dir()
    assert (root / "records" / "reviews").is_dir()
    assert (root / ".libraryos" / "locks").is_dir()
    assert root.stat().st_mode & 0o777 == 0o700


def test_initialize_rejects_nonempty_directory(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    (root / "existing.txt").write_text("keep")
    with pytest.raises(StorageError) as caught:
        initialize_library(root)
    assert caught.value.code == "library_root_not_empty"
    assert (root / "existing.txt").read_text() == "keep"


def test_initialize_rejects_git_worktree(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    with pytest.raises(StorageError) as caught:
        initialize_library(repository / "private-library")
    assert caught.value.code == "library_inside_git"


@pytest.mark.parametrize("unsafe", ["../secret", "/etc/passwd", "source\\file.pdf"])
def test_resolve_library_path_rejects_escapes(tmp_path, unsafe):
    with pytest.raises(StorageError):
        resolve_library_path(tmp_path, unsafe)


def test_resolve_library_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "library"
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError) as caught:
        resolve_library_path(root, "linked/source.pdf")
    assert caught.value.code == "library_path_escape"


def test_validate_empty_library(tmp_path):
    root = tmp_path / "research"
    initialize_library(root)
    result = validate_library(root)
    assert result["valid"] is True
    assert result["counts"] == {
        "records": 1,
        "works": 0,
        "sources": 0,
        "derivatives": 0,
        "quarantine": 0,
            "collections": 0,
            "occurrences": 0,
            "external_documents": 0,
            "findings": 0,
    }


def test_validate_reports_tampered_descriptor(tmp_path):
    root = tmp_path / "research"
    initialize_library(root)
    descriptor_path = root / "library.json"
    descriptor = json.loads(descriptor_path.read_text())
    descriptor["unexpected"] = True
    descriptor_path.write_text(json.dumps(descriptor))
    with pytest.raises(StorageError) as caught:
        validate_library(root)
    assert caught.value.code == "schema_validation_failed"


def test_validate_reports_missing_derivative_lineage(tmp_path):
    root = tmp_path / "research"
    initialize_library(root)
    work_id = "497f6eca-6276-4993-bfeb-53cbbbba6f08"
    bundle = root / "works" / work_id
    bundle.mkdir()
    derivative = bundle / "derived.txt"
    derivative.write_text("derived")
    import hashlib

    digest = hashlib.sha256(derivative.read_bytes()).hexdigest()
    manifest = {
        "schema": "https://libraryos.dev/schemas/work/v1",
        "schema_version": 1,
        "id": work_id,
        "work": {"type": "report", "identifiers": [], "title": "Lineage"},
        "sources": [],
        "derivatives": [{
            "id": "text", "path": "derived.txt", "sha256": digest,
            "bytes": derivative.stat().st_size, "media_type": "text/plain",
            "role": "text", "input_sha256": ["0" * 64],
            "generator": {"name": "test", "version": "1"},
            "generated_at": "2026-09-19T12:00:00Z", "quality": "ready",
        }],
        "created_at": "2026-09-19T12:00:00Z",
        "updated_at": "2026-09-19T12:00:00Z",
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = validate_library(root)
    assert result["valid"] is False
    assert result["findings"][0]["code"] == "derivative_input_missing"
