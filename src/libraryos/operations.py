"""Versioned application operations shared by every client."""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from jsonschema import Draft202012Validator, validators

from .acquisition import acquire_url
from .bibliography import import_bibliography
from .catalog import rebuild_catalog, search_catalog, search_prepared
from .collections import (
    export_collection,
    get_collection,
    list_collections,
    put_external_collection,
    register_external_collection,
    relocate_external_collection,
    revalidate_external_collection,
)
from .jobs import (
    cancel_job,
    create_job,
    get_job,
    list_jobs,
    reconcile_jobs,
    retry_job,
    update_job,
)
from .legacy import (
    audit_legacy_library,
    list_legacy_works,
    preview_legacy_migration,
    rehearse_legacy_migration,
    show_legacy_work,
)
from .library import initialize_library, open_library, validate_library
from .metadata import (
    accept_metadata_assertion,
    discover_crossref,
    resolve_crossref,
)
from .migrations import (
    apply_schema_migration,
    preview_schema_migration,
    rollback_schema_migration,
)
from .preparation import prepare_source
from .privacy import scan_privacy
from .reading import resolve_read
from .records import (
    archive_collection,
    archive_collection_preview,
    collection_queues,
    create_collection,
    import_assessment,
    list_records,
    purge_preview,
    purge_staged_removal,
    put_collection,
    restore_collection,
    restore_staged_removal,
    stage_collection_removal,
    work_reachability,
    write_assessment,
    write_occurrence,
    write_review,
)
from .storage import StorageError
from .works import (
    create_work,
    get_work,
    import_source,
    list_works,
    register_derivative,
    register_source_candidate,
)

Operation = Callable[..., Any]


@dataclass(frozen=True)
class OperationDefinition:
    """One public operation and its externally visible effects."""

    function: Operation
    mutation: bool
    network: bool
    capability: str


def _library_status(library: str | Path) -> dict[str, Any]:
    return {"descriptor": open_library(library)[1]}


def _list_reviews(library: str | Path) -> list[dict[str, Any]]:
    return list_records(library, "reviews")


def _list_assessments(library: str | Path) -> list[dict[str, Any]]:
    return list_records(library, "assessments")


def _list_occurrences(library: str | Path) -> list[dict[str, Any]]:
    return list_records(library, "occurrences")


OPERATIONS: dict[str, OperationDefinition] = {
    "legacy.audit": OperationDefinition(audit_legacy_library, False, False, "library.read"),
    "legacy.work.list": OperationDefinition(list_legacy_works, False, False, "library.read"),
    "legacy.work.show": OperationDefinition(show_legacy_work, False, False, "library.read"),
    "legacy.migration.preview": OperationDefinition(
        preview_legacy_migration, False, False, "library.maintain"
    ),
    "legacy.migration.rehearse": OperationDefinition(
        rehearse_legacy_migration, True, False, "library.maintain"
    ),
    "library.initialize": OperationDefinition(
        initialize_library, True, False, "library.maintain"
    ),
    "library.status": OperationDefinition(_library_status, False, False, "library.read"),
    "library.validate": OperationDefinition(validate_library, False, False, "library.maintain"),
    "library.rebuild": OperationDefinition(rebuild_catalog, True, False, "library.maintain"),
    "migration.preview": OperationDefinition(
        preview_schema_migration, False, False, "library.maintain"
    ),
    "migration.apply": OperationDefinition(
        apply_schema_migration, True, False, "library.maintain"
    ),
    "migration.rollback": OperationDefinition(
        rollback_schema_migration, True, False, "library.maintain"
    ),
    "privacy.scan": OperationDefinition(scan_privacy, False, False, "library.maintain"),
    "work.create": OperationDefinition(create_work, True, False, "library.maintain"),
    "work.list": OperationDefinition(list_works, False, False, "library.read"),
    "work.show": OperationDefinition(get_work, False, False, "library.read"),
    "bibliography.import": OperationDefinition(
        import_bibliography, True, False, "library.maintain"
    ),
    "metadata.crossref.resolve": OperationDefinition(
        resolve_crossref, True, True, "library.maintain"
    ),
    "metadata.assertion.accept": OperationDefinition(
        accept_metadata_assertion, True, False, "library.maintain"
    ),
    "source.crossref.discover": OperationDefinition(
        discover_crossref, True, True, "source.acquire"
    ),
    "source.import": OperationDefinition(import_source, True, False, "source.acquire"),
    "source.candidate.register": OperationDefinition(
        register_source_candidate, True, False, "source.acquire"
    ),
    "source.acquire": OperationDefinition(acquire_url, True, True, "source.acquire"),
    "source.prepare": OperationDefinition(
        prepare_source, True, False, "derivative.prepare"
    ),
    "derivative.register": OperationDefinition(
        register_derivative, True, False, "derivative.prepare"
    ),
    "search": OperationDefinition(search_catalog, False, False, "library.read"),
    "search.prepared": OperationDefinition(search_prepared, False, False, "library.read"),
    "read.resolve": OperationDefinition(resolve_read, False, False, "library.read"),
    "collection.create": OperationDefinition(
        create_collection, True, False, "library.maintain"
    ),
    "collection.put": OperationDefinition(
        put_collection, True, False, "collection.write:{collection_id}"
    ),
    "collection.get": OperationDefinition(
        get_collection, False, False, "collection.read:{collection_id}"
    ),
    "collection.queues": OperationDefinition(
        collection_queues, False, False, "collection.read:{collection_id}"
    ),
    "collection.list": OperationDefinition(list_collections, False, False, "library.read"),
    "collection.external.register": OperationDefinition(
        register_external_collection, True, False, "library.maintain"
    ),
    "collection.external.put": OperationDefinition(
        put_external_collection, True, False, "collection.write:{collection_id}"
    ),
    "collection.external.revalidate": OperationDefinition(
        revalidate_external_collection, True, False, "collection.write:{collection_id}"
    ),
    "collection.external.relocate": OperationDefinition(
        relocate_external_collection, True, False, "collection.write:{collection_id}"
    ),
    "collection.export": OperationDefinition(
        export_collection, True, False, "collection.read:{collection_id}"
    ),
    "review.write": OperationDefinition(write_review, True, False, "review.write"),
    "review.list": OperationDefinition(_list_reviews, False, False, "library.read"),
    "assessment.write": OperationDefinition(
        write_assessment, True, False, "assessment.write:{collection_id}"
    ),
    "assessment.import": OperationDefinition(
        import_assessment, True, False, "assessment.write:{collection_id}"
    ),
    "assessment.list": OperationDefinition(_list_assessments, False, False, "library.read"),
    "occurrence.write": OperationDefinition(
        write_occurrence, True, False, "library.maintain"
    ),
    "occurrence.list": OperationDefinition(_list_occurrences, False, False, "library.read"),
    "archive.preview": OperationDefinition(
        archive_collection_preview, False, False, "collection.read:{collection_id}"
    ),
    "archive.apply": OperationDefinition(
        archive_collection, True, False, "collection.write:{collection_id}"
    ),
    "archive.restore": OperationDefinition(
        restore_collection, True, False, "collection.write:{collection_id}"
    ),
    "purge.preview": OperationDefinition(
        purge_preview, False, False, "destructive.purge"
    ),
    "purge.stage": OperationDefinition(
        stage_collection_removal, True, False, "destructive.purge"
    ),
    "purge.restore": OperationDefinition(
        restore_staged_removal, True, False, "destructive.purge"
    ),
    "purge.confirm": OperationDefinition(
        purge_staged_removal, True, False, "destructive.purge"
    ),
    "work.reachability": OperationDefinition(
        work_reachability, False, False, "library.maintain"
    ),
    "job.create": OperationDefinition(create_job, True, False, "library.maintain"),
    "job.get": OperationDefinition(get_job, False, False, "library.maintain"),
    "job.update": OperationDefinition(update_job, True, False, "library.maintain"),
    "job.cancel": OperationDefinition(cancel_job, True, False, "library.maintain"),
    "job.retry": OperationDefinition(retry_job, True, False, "library.maintain"),
    "job.reconcile": OperationDefinition(
        reconcile_jobs, True, False, "library.maintain"
    ),
    "job.list": OperationDefinition(list_jobs, False, False, "library.maintain"),
}


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    if annotation in {Any, inspect.Parameter.empty, inspect.Signature.empty}:
        return {}
    if annotation is Path:
        return {"type": "string"}
    if annotation is type(None):
        return {"type": "null"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {Union, types.UnionType}:
        alternatives: list[dict[str, Any]] = []
        for item in arguments:
            candidate = _annotation_schema(item)
            if candidate not in alternatives:
                alternatives.append(candidate)
        return alternatives[0] if len(alternatives) == 1 else {"anyOf": alternatives}
    if origin is list:
        return {"type": "array", "items": _annotation_schema(arguments[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": True}
    return {}


def operation_contract(operation: str) -> dict[str, Any]:
    """Return the generated, versioned contract for one operation."""

    definition = OPERATIONS.get(operation)
    if definition is None:
        raise StorageError(
            "Unknown LibraryOS operation",
            code="operation_unknown",
            path=operation,
        )
    signature = inspect.signature(definition.function)
    hints = get_type_hints(definition.function)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        properties[name] = _annotation_schema(hints.get(name, parameter.annotation))
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = parameter.default
    arguments_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }
    return {
        "contract_version": 1,
        "operation": f"libraryos.{operation}",
        "effects": {
            "mutation": definition.mutation,
            "network": definition.network,
        },
        "required_capability": definition.capability,
        "arguments_schema": arguments_schema,
        "result_schema": _annotation_schema(hints.get("return", signature.return_annotation)),
    }


def describe_operations() -> dict[str, Any]:
    """Return all public operation contracts in stable name order."""

    return {
        "contract_version": 1,
        "operations": {
            name: operation_contract(name)
            for name in sorted(OPERATIONS)
        },
    }


_PATH_AWARE_VALIDATOR = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "string",
        lambda _checker, value: isinstance(value, (str, Path)),
    ),
)


def _validate_arguments(operation: str, arguments: dict[str, Any]) -> None:
    contract = operation_contract(operation)
    errors = sorted(
        _PATH_AWARE_VALIDATOR(contract["arguments_schema"]).iter_errors(arguments),
        key=lambda error: [str(item) for item in error.absolute_path],
    )
    if not errors:
        return
    error = errors[0]
    path = ".".join(str(item) for item in error.absolute_path) or None
    raise StorageError(
        f"Operation arguments are invalid: {error.message}",
        code="arguments_invalid",
        path=path,
    )


def _collection_id(operation: str, arguments: dict[str, Any]) -> str | None:
    if isinstance(arguments.get("collection_id"), str):
        return arguments["collection_id"]
    record = arguments.get("record")
    if operation in {"collection.put", "assessment.write"} and isinstance(record, dict):
        field = "id" if operation == "collection.put" else "collection_id"
        value = record.get(field)
        return value if isinstance(value, str) else None
    return None


def _work_is_in_collection(
    library: str | Path,
    collection_id: str,
    work_id: str,
) -> bool:
    collection = get_collection(library, collection_id)["collection"]
    return any(member.get("work") == work_id for member in collection["members"])


def _authorize(
    operation: str,
    arguments: dict[str, Any],
    definition: OperationDefinition,
    capabilities: list[str] | None,
) -> None:
    # An omitted grant set represents the local owner calling the Python API or CLI.
    if capabilities is None or "*" in capabilities:
        return
    grants = set(capabilities)
    required = definition.capability
    collection_id = _collection_id(operation, arguments)
    if "{collection_id}" in required:
        if collection_id is None:
            raise StorageError(
                "The operation has no collection scope to authorize",
                code="capability_scope_missing",
            )
        required = required.format(collection_id=collection_id)
    if required in grants:
        return
    if required.startswith("collection.read:") and f"collection.write:{collection_id}" in grants:
        return
    if required == "library.read" and operation in {"work.show", "read.resolve"}:
        library = arguments.get("library")
        work_id = arguments.get("work_id")
        if library is not None and isinstance(work_id, str):
            scopes = sorted(
                grant.removeprefix("collection.read:")
                for grant in grants
                if grant.startswith("collection.read:")
            )
            if any(_work_is_in_collection(library, scope, work_id) for scope in scopes):
                return
    raise StorageError(
        "Session lacks the capability required by this operation",
        code="capability_denied",
        path=required,
    )


def invoke(
    operation: str,
    arguments: dict[str, Any],
    *,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Invoke one application operation and return its canonical envelope."""

    definition = OPERATIONS.get(operation)
    if definition is None:
        raise StorageError(
            "Unknown LibraryOS operation",
            code="operation_unknown",
            path=operation,
        )
    _validate_arguments(operation, arguments)
    _authorize(operation, arguments, definition, capabilities)
    result = definition.function(**arguments)
    return {
        "contract_version": 1,
        "effects": {"mutation": definition.mutation, "network": definition.network},
        "ok": True,
        "operation": f"libraryos.{operation}",
        "result": result,
    }
