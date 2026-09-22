"""LibraryOS command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial

import yaml

from . import __version__
from .catalog import prepared_query, rebuild_catalog, search_catalog, search_prepared
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

    commands.add_parser("schemas", help="List packaged contract schemas")

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

    search = commands.add_parser("search", help="Search work metadata or prepared full text")
    search.add_argument("--library", required=True)
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--full-text", action="store_true", help="Search prepared text instead of metadata")
    search.add_argument(
        "--query-mode", choices=("literal", "fts"), default=None,
        help="Full-text interpretation (default: literal terms); fts preserves explicit Boolean syntax",
    )
    search.add_argument("--work-id", action="append", dest="work_ids", help="Restrict full-text search to this work; repeatable")
    search.add_argument("query")

    serve_command = commands.add_parser("serve", help="Run the token-protected local API")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8765)
    serve_command.add_argument("--library")
    serve_command.add_argument("--no-open", action="store_true")
    serve_command.add_argument("--no-print-token", action="store_true")

    call = commands.add_parser("call", help="Invoke any versioned operation")
    call.add_argument("operation", help="Operation name, for example work.list")
    call.add_argument(
        "--arguments",
        default="{}",
        help="JSON object of operation arguments",
    )
    operations = commands.add_parser(
        "operations",
        help="List versioned operations or inspect one machine-readable contract",
    )
    operations.add_argument(
        "operation",
        nargs="?",
        help="Optional operation name, for example metadata.crossref.resolve",
    )
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Result presentation (default: json)")
    for command in commands.choices.values():
        if command is serve_command:
            continue
        command.add_argument(
            "--format", choices=("json", "text"), default=argparse.SUPPRESS,
            help="Result presentation (default: json); execution errors remain JSON",
        )
    return parser


def _write(value: object, *, output_format: str = "json") -> None:
    if output_format == "text":
        sys.stdout.write(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        return
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def _error(error: SchemaError | StorageError, operation: str) -> int:
    detail = {
        "code": error.code,
        "message": str(error),
        "path": error.path or None,
    }
    if isinstance(error, StorageError):
        if error.details is not None:
            detail["details"] = error.details
        if error.next_actions is not None:
            detail["next_actions"] = error.next_actions
    _write(
        {
            "contract_version": 1,
            "error": detail,
            "ok": False,
            "operation": operation,
        }
    )
    return 2


def _result(operation: str, result: object, *, mutation: bool, output_format: str = "json") -> None:
    _write(
        {
            "contract_version": 1,
            "effects": {"mutation": mutation, "network": False},
            "ok": True,
            "operation": operation,
            "result": result,
        },
        output_format=output_format,
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
    write = partial(_write, output_format=arguments.format)
    write_result = partial(_result, output_format=arguments.format)
    operation = "libraryos.cli"
    try:
        if arguments.command == "schemas":
            operation = "libraryos.schema.list"
            names = available_schemas()
            if arguments.format == "text":
                sys.stdout.write("\n".join(names) + "\n")
            else:
                write({"contract_version": 1, "ok": True, "schemas": list(names)})
            return 0
        if arguments.command == "schema":
            operation = "libraryos.schema.show"
            write(load_schema(arguments.name))
            return 0
        if arguments.command == "validate-record":
            operation = "libraryos.schema.validate"
            record = load_record(arguments.path)
            validate_record(record, arguments.schema)
            write(
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
            write(
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
            write(
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
            write(
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
            write(
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
            write_result(operation, result, mutation=True)
            return 0
        if arguments.command == "work-list":
            operation = "libraryos.work.list"
            write_result(operation, {"works": list_works(arguments.library)}, mutation=False)
            return 0
        if arguments.command == "work-show":
            operation = "libraryos.work.show"
            write_result(operation, get_work(arguments.library, arguments.work_id), mutation=False)
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
            write_result(operation, result, mutation=result["created"])
            return 0
        if arguments.command == "rebuild":
            operation = "libraryos.library.rebuild"
            write_result(operation, rebuild_catalog(arguments.library), mutation=True)
            return 0
        if arguments.command == "search":
            operation = "libraryos.search"
            if arguments.full_text:
                query_mode = arguments.query_mode or "literal"
                operation = "libraryos.search.prepared"
                write_result(
                    operation,
                    {
                        "query": arguments.query,
                        "query_mode": query_mode,
                        "effective_query": prepared_query(arguments.query, query_mode),
                        "work_ids": arguments.work_ids,
                        "scientific_support": "not_assessed",
                        "results": search_prepared(
                            arguments.library, arguments.query, limit=arguments.limit,
                            work_ids=arguments.work_ids, query_mode=query_mode,
                        ),
                    },
                    mutation=False,
                )
                return 0
            if arguments.query_mode is not None or arguments.work_ids is not None:
                raise StorageError(
                    "--query-mode and --work-id require --full-text",
                    code="arguments_invalid",
                )
            write_result(
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
                print_token=not arguments.no_print_token,
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
            write(invoke(arguments.operation, values))
            return 0
        if arguments.command == "operations":
            from .operations import describe_operations, operation_contract

            write(
                operation_contract(arguments.operation)
                if arguments.operation
                else describe_operations()
            )
            return 0
    except (SchemaError, StorageError) as error:
        return _error(error, operation)
    return 2
