import json
from pathlib import Path

import pytest

from libraryos.storage import atomic_bytes, atomic_json


def test_atomic_bytes_preserves_previous_file_when_replace_is_interrupted(
    monkeypatch, tmp_path
):
    target = tmp_path / "record.bin"
    target.write_bytes(b"previous complete value")

    def interrupted_replace(source: Path, destination: Path) -> None:
        assert destination == target
        assert source.read_bytes() == b"replacement complete value"
        raise OSError("injected interruption before atomic replacement")

    monkeypatch.setattr("libraryos.storage.os.replace", interrupted_replace)

    with pytest.raises(OSError, match="injected interruption"):
        atomic_bytes(target, b"replacement complete value")

    assert target.read_bytes() == b"previous complete value"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_json_never_exposes_partial_json_when_replace_is_interrupted(
    monkeypatch, tmp_path
):
    target = tmp_path / "record.json"
    original = {"schema": "example/v1", "value": "before"}
    atomic_json(target, original)

    def interrupted_replace(source: Path, destination: Path) -> None:
        assert destination == target
        assert json.loads(source.read_text())["value"] == "after"
        raise OSError("injected interruption before atomic replacement")

    monkeypatch.setattr("libraryos.storage.os.replace", interrupted_replace)

    with pytest.raises(OSError, match="injected interruption"):
        atomic_json(target, {"schema": "example/v1", "value": "after"})

    assert json.loads(target.read_text()) == original
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []
