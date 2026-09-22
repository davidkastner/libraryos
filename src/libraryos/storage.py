"""Safe filesystem primitives for authoritative LibraryOS records."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Any

from .schemas import SchemaError, validate_record


class StorageError(ValueError):
    """A stable storage or library-integrity error."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        path: str | None = None,
        details: dict[str, Any] | None = None,
        next_actions: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.details = details
        self.next_actions = next_actions


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Serialize a mutation using a private advisory lock file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flock(descriptor, LOCK_EX)
        yield
    finally:
        flock(descriptor, LOCK_UN)
        os.close(descriptor)


def atomic_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    """Atomically replace a JSON record and durably flush its parent directory."""

    payload = (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    atomic_bytes(path, payload, mode=mode)


def atomic_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Atomically replace a file and durably flush its parent directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def git_ancestor(path: Path) -> Path | None:
    """Return the containing Git worktree, if any."""

    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def safe_library_root(path: str | Path, *, must_exist: bool = True) -> Path:
    """Resolve a library root without following a root symlink."""

    supplied = Path(path).expanduser().absolute()
    if supplied.is_symlink():
        raise StorageError(
            "Library root must not be a symlink",
            code="library_root_symlink",
            path=str(supplied),
        )
    if must_exist and not supplied.is_dir():
        raise StorageError(
            "Library root does not exist or is not a directory",
            code="library_not_found",
            path=str(supplied),
        )
    return supplied.resolve()


def resolve_library_path(root: Path, relative_path: str, *, must_exist: bool = False) -> Path:
    """Resolve a manifest path beneath a library without allowing escapes."""

    candidate = Path(relative_path)
    if candidate.is_absolute() or "\\" in relative_path or ".." in candidate.parts:
        raise StorageError(
            "Library paths must be relative and cannot traverse parent directories",
            code="unsafe_library_path",
            path=relative_path,
        )
    resolved = (root / candidate).resolve(strict=must_exist)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise StorageError(
            "Library path resolves outside the configured root",
            code="library_path_escape",
            path=relative_path,
        ) from error
    return resolved


def read_json_record(path: Path) -> dict[str, Any]:
    """Read and validate a self-declared authoritative record."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise StorageError(
            f"Could not read record: {error}",
            code="record_read_failed",
            path=str(path),
        ) from error
    except json.JSONDecodeError as error:
        raise StorageError(
            f"Record is not valid JSON: {error}",
            code="record_json_invalid",
            path=str(path),
        ) from error
    if not isinstance(value, dict):
        raise StorageError(
            "Authoritative record must be a JSON object",
            code="record_type_invalid",
            path=str(path),
        )
    try:
        validate_record(value)
    except SchemaError as error:
        raise StorageError(
            str(error),
            code=error.code,
            path=f"{path}{error.path}",
        ) from error
    return value
