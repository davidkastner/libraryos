"""LibraryOS command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .catalog import rebuild_catalog, search_catalog
from .legacy import audit_legacy_library
from .library import initialize_library, open_library, validate_library
from .schemas import SchemaError, available_schemas, load_record, load_schema, validate_record
from .server import serve
from .storage import StorageError
from .works import create_work, get_work, import_source, list_works


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="libraryos",
        description="The research library agents can operate over.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    schemas = commands.add_parser("schemas", help="List packaged contract schemas")
    schemas.add_argument("--format", choices=("json", "text"), default="json")

    schema = commands.add_parser("schema", help="Print one packaged JSON Schema")
    schema.add_argument("name")

    validate = commands.add_parser("validate-record", help="Validate one JSON record")
    validate.add_argument("path")
    validate.add_argument("--schema")

    init = commands.add_parser("init", help="Initialize a private library instance")
    init.add_argument("path")
    init.add_argument("--title")
    init.add_argument("--description")

    status = commands.add_parser("status", help="Show one library descriptor")
    status.add_argument("--library", required=True)

    validate_library_command = commands.add_parser(
        "validate",
        help="Validate authoritative library records and files",
    )
    validate_library_command.add_argument("--library", required=True)
    validate_library_command.add_argument("--skip-hashes", action="store_true")

    legacy_audit = commands.add_parser(
        "audit-legacy",
        help="Read-only audit of a legacy evidence-bundle library",
    )
    legacy_audit.add_argument("--library", required=True)
    legacy_audit.add_argument("--skip-hashes", action="store_true")

    work_create = commands.add_parser("work-create", help="Create an authoritative work")
    work_create.add_argument("--library", required=True)
    work_create.add_argument("--type", required=True)
    work_create.add_argument("--title")
    work_create.add_argument("--identifier", action="append", default=[], metavar="SCHEME:VALUE")

    work_list = commands.add_parser("work-list", help="List authoritative works")
    work_list.add_argument("--library", required=True)

    work_show = commands.add_parser("work-show", help="Show one authoritative work")
    work_show.add_argument("--library", required=True)
    work_show.add_argument("work_id")

    source_import = commands.add_parser("source-import", help="Import an immutable source")
    source_import.add_argument("--library", required=True)
    source_import.add_argument("--work-id", required=True)
    source_import.add_argument("--role", required=True)
    source_import.add_argument("--version", default="unknown")
    source_import.add_argument("--access", default="user_provided")
    source_import.add_argument("--identity-status", default="unverified")
    source_import.add_argument("path")

    rebuild = commands.add_parser("rebuild", help="Rebuild disposable indexes")
    rebuild.add_argument("--library", required=True)

    search = commands.add_parser("search", help="Search normalized work metadata")
    search.add_argument("--library", required=True)
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("query")

    serve_command = commands.add_parser("serve", help="Run the token-protected local API")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8765)
    serve_command.add_argument("--library")
    serve_command.add_argument("--no-open", action="store_true")

    call = commands.add_parser("call", help="Invoke any versioned operation")
    call.add_argument("operation", help="Operation name, for example work.list")
    call.add_argument(
        "--arguments",
        default="{}",
        help="JSON object of operation arguments",
    )
    commands.add_parser(
        "operations",
        help="List every versioned operation and its machine-readable contract",
    )
    return parser


def _write(value: object) -> None:
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def _error(error: SchemaError | StorageError, operation: str) -> int:
    _write(
        {
            "contract_version": 1,
            "error": {
                "code": error.code,
                "message": str(error),
                "path": error.path or None,
            },
            "ok": False,
            "operation": operation,
        }
    )
    return 2


def _result(operation: str, result: object, *, mutation: bool) -> None:
    _write(
        {
            "contract_version": 1,
            "effects": {"mutation": mutation, "network": False},
            "ok": True,
            "operation": operation,
            "result": result,
        }
    )


def _identifiers(values: list[str]) -> list[dict[str, str]]:
    result = []
    for value in values:
        scheme, separator, identifier = value.partition(":")
        if not separator or not scheme or not identifier:
            raise StorageError(
                "Identifiers must use SCHEME:VALUE",
                code="identifier_argument_invalid",
                path=value,
            )
        result.append({"scheme": scheme.casefold(), "value": identifier})
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    operation = "libraryos.cli"
    try:
        if arguments.command == "schemas":
            operation = "libraryos.schema.list"
            names = available_schemas()
            if arguments.format == "text":
                sys.stdout.write("\n".join(names) + "\n")
            else:
                _write({"contract_version": 1, "ok": True, "schemas": list(names)})
            return 0
        if arguments.command == "schema":
            operation = "libraryos.schema.show"
            _write(load_schema(arguments.name))
            return 0
        if arguments.command == "validate-record":
            operation = "libraryos.schema.validate"
            record = load_record(arguments.path)
            validate_record(record, arguments.schema)
            _write(
                {
                    "contract_version": 1,
                    "ok": True,
                    "operation": "libraryos.schema.validate",
                    "path": arguments.path,
                    "schema": arguments.schema or record["schema"],
                }
            )
            return 0
        if arguments.command == "init":
            operation = "libraryos.library.init"
            result = initialize_library(
                arguments.path,
                title=arguments.title,
                description=arguments.description,
            )
            _write(
                {
                    "contract_version": 1,
                    "effects": {"mutation": True, "network": False},
                    "ok": True,
                    "operation": operation,
                    "result": result,
                }
            )
            return 0
        if arguments.command == "status":
            operation = "libraryos.library.status"
            root, descriptor = open_library(arguments.library)
            _write(
                {
                    "contract_version": 1,
                    "effects": {"mutation": False, "network": False},
                    "ok": True,
                    "operation": operation,
                    "result": {"descriptor": descriptor, "path": str(root)},
                }
            )
            return 0
        if arguments.command == "validate":
            operation = "libraryos.library.validate"
            result = validate_library(
                arguments.library,
                verify_hashes=not arguments.skip_hashes,
            )
            _write(
                {
                    "contract_version": 1,
                    "effects": {"mutation": False, "network": False},
                    "ok": result["valid"],
                    "operation": operation,
                    "result": result,
                }
            )
            return 0 if result["valid"] else 1
        if arguments.command == "audit-legacy":
            operation = "libraryos.legacy.audit"
            result = audit_legacy_library(
                arguments.library,
                verify_hashes=not arguments.skip_hashes,
            )
            _write(
                {
                    "contract_version": 1,
                    "effects": {"mutation": False, "network": False},
                    "ok": result["valid"],
                    "operation": operation,
                    "result": result,
                }
            )
            return 0 if result["valid"] else 1
        if arguments.command == "work-create":
            operation = "libraryos.work.create"
            result = create_work(
                arguments.library,
                work_type=arguments.type,
                title=arguments.title,
                identifiers=_identifiers(arguments.identifier),
            )
            _result(operation, result, mutation=True)
            return 0
        if arguments.command == "work-list":
            operation = "libraryos.work.list"
            _result(operation, {"works": list_works(arguments.library)}, mutation=False)
            return 0
        if arguments.command == "work-show":
            operation = "libraryos.work.show"
            _result(operation, get_work(arguments.library, arguments.work_id), mutation=False)
            return 0
        if arguments.command == "source-import":
            operation = "libraryos.source.import"
            result = import_source(
                arguments.library,
                arguments.work_id,
                arguments.path,
                role=arguments.role,
                version=arguments.version,
                access=arguments.access,
                identity_status=arguments.identity_status,
            )
            _result(operation, result, mutation=result["created"])
            return 0
        if arguments.command == "rebuild":
            operation = "libraryos.library.rebuild"
            _result(operation, rebuild_catalog(arguments.library), mutation=True)
            return 0
        if arguments.command == "search":
            operation = "libraryos.search"
            _result(
                operation,
                {"query": arguments.query, "results": search_catalog(
                    arguments.library, arguments.query, limit=arguments.limit
                )},
                mutation=False,
            )
            return 0
        if arguments.command == "serve":
            serve(
                host=arguments.host,
                port=arguments.port,
                library=arguments.library,
                open_browser=not arguments.no_open,
            )
            return 0
        if arguments.command == "call":
            from .operations import invoke

            try:
                values = json.loads(arguments.arguments)
            except json.JSONDecodeError as error:
                raise StorageError(
                    f"Arguments are not valid JSON: {error}",
                    code="arguments_invalid",
                ) from error
            if not isinstance(values, dict):
                raise StorageError(
                    "Arguments must be a JSON object",
                    code="arguments_invalid",
                )
            operation = f"libraryos.{arguments.operation}"
            _write(invoke(arguments.operation, values))
            return 0
        if arguments.command == "operations":
            from .operations import describe_operations

            _write(describe_operations())
            return 0
    except (SchemaError, StorageError) as error:
        return _error(error, operation)
    return 2
