from concurrent.futures import ThreadPoolExecutor

import pytest

from libraryos import (
    StorageError,
    append_attempt,
    append_exception,
    cancel_job,
    create_job,
    get_job,
    initialize_library,
    list_jobs,
    reconcile_jobs,
    retry_job,
    update_job,
)


def _create_example_job(root):
    return create_job(
        root,
        operation="libraryos.derivative.prepare",
        arguments={"work_id": "example"},
        requested_by={"kind": "agent", "id": "worker"},
        capabilities=["derivative.prepare"],
    )


def test_job_checkpoint_is_durable_and_resumable(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    job = _create_example_job(root)
    updated = update_job(
        root,
        job["id"],
        status="running",
        checkpoint={"completed_pages": 4},
    )
    assert updated["checkpoint"] == {"completed_pages": 4}
    append_attempt(root, job["id"], status="failed", diagnostic="bounded diagnostic")
    append_exception(root, job["id"], category="conversion_failed", message="Needs review")
    assert list_jobs(root)[0]["status"] == "running"
    assert len(list_jobs(root)[0]["attempts"]) == 1


def test_job_state_machine_rejects_invalid_transitions(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    job = _create_example_job(root)

    with pytest.raises(StorageError, match="not allowed") as captured:
        update_job(root, job["id"], status="succeeded")
    assert captured.value.code == "job_transition_invalid"

    running = update_job(root, job["id"], status="running")
    succeeded = update_job(root, job["id"], status="succeeded", result={"ok": True})
    assert running["history"][-1]["from_status"] == "queued"
    assert succeeded["history"][-1]["to_status"] == "succeeded"

    with pytest.raises(StorageError) as captured:
        update_job(root, job["id"], status="failed")
    assert captured.value.code == "job_transition_invalid"


def test_cancel_and_retry_preserve_arguments_and_checkpoint(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    job = _create_example_job(root)
    update_job(
        root,
        job["id"],
        status="running",
        checkpoint={"completed_pages": [1, 2, 3]},
    )

    cancelled = cancel_job(root, job["id"], reason="operator requested stop")
    retried = retry_job(root, job["id"], reason="dependency is available")

    assert cancelled["status"] == "cancelled"
    assert cancelled["history"][-1]["reason"] == "operator requested stop"
    assert retried["status"] == "queued"
    assert retried["arguments"] == job["arguments"]
    assert retried["checkpoint"] == {"completed_pages": [1, 2, 3]}
    assert retried["history"][-1]["action"] == "retried"


def test_reconcile_marks_running_jobs_interrupted_without_rerunning(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    running = _create_example_job(root)
    queued = _create_example_job(root)
    update_job(
        root,
        running["id"],
        status="running",
        checkpoint={"cursor": 8},
    )

    result = reconcile_jobs(root)

    assert result == {"interrupted": [running["id"]], "rerun": False}
    interrupted = get_job(root, running["id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["checkpoint"] == {"cursor": 8}
    assert interrupted["history"][-1]["action"] == "reconciled"
    assert get_job(root, queued["id"])["status"] == "queued"
    assert reconcile_jobs(root) == {"interrupted": [], "rerun": False}


def test_succeeded_job_cannot_be_cancelled_or_retried(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    job = _create_example_job(root)
    update_job(root, job["id"], status="running")
    update_job(root, job["id"], status="succeeded")

    with pytest.raises(StorageError) as cancelled:
        cancel_job(root, job["id"])
    assert cancelled.value.code == "job_cancel_invalid"
    with pytest.raises(StorageError) as retried:
        retry_job(root, job["id"])
    assert retried.value.code == "job_retry_invalid"


def test_concurrent_transition_allows_only_one_start(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    job = _create_example_job(root)

    def start_job(_):
        try:
            update_job(root, job["id"], status="running")
        except StorageError as error:
            return error.code
        return "started"

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(start_job, range(24)))

    assert outcomes.count("started") == 1
    assert outcomes.count("job_transition_invalid") == 23
    stored = get_job(root, job["id"])
    assert stored["status"] == "running"
    assert [event["to_status"] for event in stored["history"]] == ["queued", "running"]


def test_job_id_cannot_escape_jobs_directory(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)

    with pytest.raises(StorageError) as captured:
        get_job(root, "../outside")
    assert captured.value.code == "job_id_invalid"
