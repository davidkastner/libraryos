"""Packaged JSON Schema discovery and validation."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMA_PREFIX = "https://libraryos.dev/schemas/"


class SchemaError(ValueError):
    """A stable, user-facing schema validation error."""

    def __init__(self, message: str, *, code: str, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def _schema_root():
    return files("libraryos").joinpath("schemas")


def available_schemas() -> tuple[str, ...]:
    """Return packaged schema names without file extensions."""

    return tuple(
        sorted(item.name.removesuffix(".json") for item in _schema_root().iterdir() if item.name.endswith(".json"))
    )


def load_schema(name: str) -> dict[str, Any]:
    """Load one packaged schema by name or canonical LibraryOS schema URI."""

    normalized = name
    if normalized.startswith(SCHEMA_PREFIX):
        normalized = normalized.removeprefix(SCHEMA_PREFIX).replace("/", "-")
    normalized = normalized.removesuffix(".json")
    path = _schema_root().joinpath(f"{normalized}.json")
    if not path.is_file():
        raise SchemaError(
            f"Unknown LibraryOS schema: {name}",
            code="schema_not_found",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    registry = Registry()
    for name in available_schemas():
        schema = load_schema(name)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


def validate_record(record: Any, schema: str | None = None) -> None:
    """Validate a record against an explicit or self-declared schema."""

    if schema is None:
        if not isinstance(record, dict) or not isinstance(record.get("schema"), str):
            raise SchemaError(
                "Record must declare a string 'schema' URI",
                code="schema_declaration_missing",
                path="/schema",
            )
        schema = record["schema"]
    definition = load_schema(schema)
    errors = sorted(
        Draft202012Validator(
            definition,
            registry=_registry(),
            format_checker=FormatChecker(),
        ).iter_errors(record),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        pointer = "".join(f"/{part}" for part in error.absolute_path)
        raise SchemaError(
            error.message,
            code="schema_validation_failed",
            path=pointer,
        )


def load_record(path: str | Path) -> Any:
    """Load a JSON record."""

    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaError(
            f"Could not read JSON record {source}: {error}",
            code="record_read_failed",
        ) from error
