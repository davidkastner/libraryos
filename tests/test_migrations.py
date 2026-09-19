import json
import uuid

import pytest

from libraryos import (
    StorageError,
    apply_schema_migration,
    create_work,
    initialize_library,
    preview_schema_migration,
    rollback_schema_migration,
    validate_library,
    write_assessment,
)


def test_schema_migration_is_previewed_recoverable_and_source_free(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Before")
    manifest_path = root / "works" / work["id"] / "manifest.json"
    original = manifest_path.read_bytes()
    replacement = json.loads(original)
    replacement["work"]["title"] = "After"

    preview = preview_schema_migration(
        root, [{"path": f"works/{work['id']}/manifest.json", "record": replacement}]
    )
    assert preview["applied"] is False
    assert preview["source_bytes_changed"] == []
    assert manifest_path.read_bytes() == original

    applied = apply_schema_migration(
        root,
        [{"path": f"works/{work['id']}/manifest.json", "record": replacement}],
        expected_plan_sha256=preview["plan_sha256"],
    )
    assert json.loads(manifest_path.read_text())["work"]["title"] == "After"
    migration = applied["migration"]
    assert migration["status"] == "applied"
    backup = root / migration["changes"][0]["backup_path"]
    assert backup.read_bytes() == original
    assert applied["validation"]["valid"] is True

    rolled_back = rollback_schema_migration(root, migration["id"])
    assert rolled_back["migration"]["status"] == "rolled_back"
    assert manifest_path.read_bytes() == original


def test_schema_migration_refuses_stale_preview_and_rollback(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Before")
    relative = f"works/{work['id']}/manifest.json"
    manifest_path = root / relative
    replacement = json.loads(manifest_path.read_text())
    replacement["work"]["title"] = "After"
    replacements = [{"path": relative, "record": replacement}]
    preview = preview_schema_migration(root, replacements)

    with pytest.raises(StorageError) as stale_preview:
        apply_schema_migration(
            root, replacements, expected_plan_sha256="0" * 64
        )
    assert stale_preview.value.code == "migration_preview_stale"
    applied = apply_schema_migration(
        root, replacements, expected_plan_sha256=preview["plan_sha256"]
    )
    changed = json.loads(manifest_path.read_text())
    changed["work"]["title"] = "Later edit"
    manifest_path.write_text(json.dumps(changed))

    with pytest.raises(StorageError) as stale_rollback:
        rollback_schema_migration(root, applied["migration"]["id"])
    assert stale_rollback.value.code == "migration_rollback_stale"


def test_schema_migration_rejects_source_and_external_targets(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Before")
    source = root / "works" / work["id"] / "source.json"
    source.write_text("{}")

    with pytest.raises(StorageError) as invalid:
        preview_schema_migration(
            root,
            [{"path": f"works/{work['id']}/source.json", "record": {}}],
        )
    assert invalid.value.code == "migration_target_invalid"


def test_failed_migration_automatically_restores_all_changed_records(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Evidence")
    from libraryos import create_collection

    create_collection(root, collection_id="project-a", title="Project A")
    assessment = {
        "schema": "https://libraryos.dev/schemas/assessment/v1",
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "collection_id": "project-a",
        "subject": {"kind": "work", "id": work["id"]},
        "author": {"kind": "human", "id": "researcher"},
        "purpose": "scope",
        "summary": "Project-scoped judgment.",
        "created_at": "2026-09-19T12:00:00Z",
    }
    write_assessment(root, assessment)
    relative = f"records/assessments/{assessment['id']}.json"
    path = root / relative
    original = path.read_bytes()
    invalid_reference = {**assessment, "collection_id": "missing-collection"}
    replacements = [{"path": relative, "record": invalid_reference}]
    preview = preview_schema_migration(root, replacements)

    with pytest.raises(StorageError) as failed:
        apply_schema_migration(
            root,
            replacements,
            expected_plan_sha256=preview["plan_sha256"],
        )
    assert failed.value.code == "migration_validation_failed"
    assert path.read_bytes() == original
    records = sorted((root / "records" / "migrations").glob("*/migration.json"))
    assert len(records) == 1
    recovery = json.loads(records[0].read_text())
    assert recovery["status"] == "rolled_back"
    assert recovery["error_code"] == "migration_validation_failed"


def test_library_validation_checks_migration_recovery_records(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="report", title="Before")
    relative = f"works/{work['id']}/manifest.json"
    manifest_path = root / relative
    replacement = json.loads(manifest_path.read_text())
    replacement["work"]["title"] = "After"
    replacements = [{"path": relative, "record": replacement}]
    preview = preview_schema_migration(root, replacements)
    applied = apply_schema_migration(
        root,
        replacements,
        expected_plan_sha256=preview["plan_sha256"],
    )

    valid = validate_library(root)
    assert valid["valid"] is True
    backup = root / applied["migration"]["changes"][0]["backup_path"]
    backup.write_bytes(b"tampered")
    invalid = validate_library(root)
    assert invalid["valid"] is False
    assert invalid["findings"][0]["code"] == "migration_backup_hash_mismatch"
