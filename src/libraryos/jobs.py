"""Durable, resumable job records for long-running operations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .library import open_library, utc_now
from .schemas import validate_record
from .storage import StorageError, atomic_json, exclusive_lock, read_json_record

JOB_SCHEMA = "https://libraryos.dev/schemas/job/v1"
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
RETRYABLE_STATUSES = frozenset({"failed", "cancelled", "interrupted"})
_ALLOWED_TRANSITIONS = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "interrupted"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "interrupted": frozenset(),
}


def _job_paths(root: Path, job_id: str) -> tuple[Path, Path]:
    try:
        canonical_id = str(uuid.UUID(job_id))
    except (ValueError, AttributeError) as error:
        raise StorageError(
            "Job ID must be a UUID",
            code="job_id_invalid",
            path=str(job_id),
        ) from error
    if canonical_id != job_id:
        raise StorageError(
            "Job ID must use canonical UUID spelling",
            code="job_id_invalid",
            path=str(job_id),
        )
    return (
        root / ".libraryos" / "jobs" / f"{canonical_id}.json",
        root / ".libraryos" / "locks" / f"job-{canonical_id}.lock",
    )


def _transition_event(
    *,
    previous: str | None,
    status: str,
    action: str,
    occurred_at: str,
    reason: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "action": action,
        "from_status": previous,
        "to_status": status,
        "occurred_at": occurred_at,
    }
    if reason:
        event["reason"] = reason[:2000]
    return event


def _read_job(root: Path, job_id: str) -> dict[str, Any]:
    path, _ = _job_paths(root, job_id)
    if not path.is_file():
        raise StorageError("Job does not exist", code="job_not_found", path=job_id)
    return read_json_record(path)


def create_job(
    library: str | Path,
    *,
    operation: str,
    arguments: dict[str, Any],
    requested_by: dict[str, str],
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    root, _ = open_library(library)
    now = utc_now()
    job = {
        "schema": JOB_SCHEMA,
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "operation": operation,
        "status": "queued",
        "requested_by": requested_by,
        "capabilities": capabilities or [],
        "arguments": arguments,
        "attempts": [],
        "exceptions": [],
        "history": [
            _transition_event(
                previous=None,
                status="queued",
                action="created",
                occurred_at=now,
            )
        ],
        "created_at": now,
        "updated_at": now,
    }
    validate_record(job)
    atomic_json(root / ".libraryos" / "jobs" / f"{job['id']}.json", job)
    return job


def get_job(library: str | Path, job_id: str) -> dict[str, Any]:
    """Return one validated durable job."""

    root, _ = open_library(library)
    return _read_job(root, job_id)


def update_job(
    library: str | Path,
    job_id: str,
    *,
    status: str,
    checkpoint: dict[str, Any] | None = None,
    result: Any = None,
) -> dict[str, Any]:
    """Advance a job through its state machine or persist a running checkpoint."""

    root, _ = open_library(library)
    path, lock = _job_paths(root, job_id)
    if not path.is_file():
        raise StorageError("Job does not exist", code="job_not_found", path=job_id)
    with exclusive_lock(lock):
        job = read_json_record(path)
        previous = job["status"]
        if status == previous:
            if status != "running" or checkpoint is None or result is not None:
                raise StorageError(
                    "A same-state update is only valid for a running checkpoint",
                    code="job_transition_invalid",
                    path=f"{previous}->{status}",
                )
        elif status not in _ALLOWED_TRANSITIONS[previous]:
            raise StorageError(
                "Job status transition is not allowed",
                code="job_transition_invalid",
                path=f"{previous}->{status}",
            )
        now = utc_now()
        history = list(job.get("history", []))
        if status != previous:
            history.append(
                _transition_event(
                    previous=previous,
                    status=status,
                    action="transitioned",
                    occurred_at=now,
                )
            )
        updated = {**job, "status": status, "history": history, "updated_at": now}
        if checkpoint is not None:
            updated["checkpoint"] = checkpoint
        if result is not None:
            updated["result"] = result
        validate_record(updated)
        atomic_json(path, updated)
    return updated


def cancel_job(
    library: str | Path,
    job_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Cancel queued or running work without deleting its durable state."""

    root, _ = open_library(library)
    path, lock = _job_paths(root, job_id)
    if not path.is_file():
        raise StorageError("Job does not exist", code="job_not_found", path=job_id)
    with exclusive_lock(lock):
        job = read_json_record(path)
        previous = job["status"]
        if previous not in {"queued", "running"}:
            raise StorageError(
                "Only queued or running jobs can be cancelled",
                code="job_cancel_invalid",
                path=previous,
            )
        now = utc_now()
        updated = {
            **job,
            "status": "cancelled",
            "history": [
                *job.get("history", []),
                _transition_event(
                    previous=previous,
                    status="cancelled",
                    action="cancelled",
                    occurred_at=now,
                    reason=reason,
                ),
            ],
            "updated_at": now,
        }
        validate_record(updated)
        atomic_json(path, updated)
    return updated


def retry_job(
    library: str | Path,
    job_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Requeue retryable work while preserving arguments and checkpoints."""

    root, _ = open_library(library)
    path, lock = _job_paths(root, job_id)
    if not path.is_file():
        raise StorageError("Job does not exist", code="job_not_found", path=job_id)
    with exclusive_lock(lock):
        job = read_json_record(path)
        previous = job["status"]
        if previous not in RETRYABLE_STATUSES:
            raise StorageError(
                "Job is not in a retryable state",
                code="job_retry_invalid",
                path=previous,
            )
        now = utc_now()
        updated = {
            **job,
            "status": "queued",
            "history": [
                *job.get("history", []),
                _transition_event(
                    previous=previous,
                    status="queued",
                    action="retried",
                    occurred_at=now,
                    reason=reason,
                ),
            ],
            "updated_at": now,
        }
        validate_record(updated)
        atomic_json(path, updated)
    return updated


def reconcile_jobs(library: str | Path) -> dict[str, Any]:
    """Mark jobs left running by a prior process as interrupted.

    Reconciliation records state only. It never reruns an operation.
    """

    root, _ = open_library(library)
    interrupted: list[str] = []
    for path in sorted((root / ".libraryos" / "jobs").glob("*.json")):
        job_id = path.stem
        job_path, lock = _job_paths(root, job_id)
        with exclusive_lock(lock):
            job = read_json_record(job_path)
            if job["status"] != "running":
                continue
            now = utc_now()
            updated = {
                **job,
                "status": "interrupted",
                "history": [
                    *job.get("history", []),
                    _transition_event(
                        previous="running",
                        status="interrupted",
                        action="reconciled",
                        occurred_at=now,
                        reason="previous process ended before recording completion",
                    ),
                ],
                "updated_at": now,
            }
            validate_record(updated)
            atomic_json(job_path, updated)
            interrupted.append(job_id)
    return {"interrupted": interrupted, "rerun": False}


def list_jobs(library: str | Path) -> list[dict[str, Any]]:
    root, _ = open_library(library)
    return [
        read_json_record(path)
        for path in sorted((root / ".libraryos" / "jobs").glob("*.json"))
    ]


def append_attempt(
    library: str | Path,
    job_id: str,
    *,
    status: str,
    provider: str | None = None,
    diagnostic: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Append one bounded provider/tool attempt without storing secret-bearing data."""

    root, _ = open_library(library)
    path, lock = _job_paths(root, job_id)
    if not path.is_file():
        raise StorageError("Job does not exist", code="job_not_found", path=job_id)
    with exclusive_lock(lock):
        job = read_json_record(path)
        now = utc_now()
        attempt = {
            "id": str(uuid.uuid4()),
            "started_at": now,
            "completed_at": now,
            "status": status,
        }
        if provider:
            attempt["provider"] = provider
        if diagnostic:
            attempt["diagnostic"] = diagnostic[:2000]
        if error_code:
            attempt["error_code"] = error_code
        updated = {**job, "attempts": [*job["attempts"], attempt], "updated_at": now}
        validate_record(updated)
        atomic_json(path, updated)
    return attempt


def append_exception(
    library: str | Path,
    job_id: str,
    *,
    category: str,
    message: str,
) -> dict[str, Any]:
    """Append one actionable exception to a durable job."""

    root, _ = open_library(library)
    path, lock = _job_paths(root, job_id)
    if not path.is_file():
        raise StorageError("Job does not exist", code="job_not_found", path=job_id)
    with exclusive_lock(lock):
        job = read_json_record(path)
        now = utc_now()
        exception = {
            "id": str(uuid.uuid4()),
            "category": category,
            "message": message[:2000],
            "state": "open",
            "created_at": now,
        }
        updated = {
            **job,
            "exceptions": [*job["exceptions"], exception],
            "updated_at": now,
        }
        validate_record(updated)
        atomic_json(path, updated)
    return exception
