"""Token-protected localhost JSON API."""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .operations import OPERATIONS, invoke, operation_contract
from .schemas import SchemaError
from .storage import StorageError


def openapi_document(host: str, port: int) -> dict[str, Any]:
    """Return generated OpenAPI paths backed by the operation registry."""

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "LibraryOS local API", "version": "1"},
        "servers": [{"url": f"http://{host}:{port}"}],
        "paths": {},
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
    }
    for name in sorted(OPERATIONS):
        contract = operation_contract(name)
        document["paths"][f"/v1/operations/{name}"] = {
            "post": {
                "operationId": name.replace(".", "_"),
                "summary": f"Invoke libraryos.{name}",
                "x-libraryos-required-capability": contract["required_capability"],
                "security": [{"bearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": contract["arguments_schema"],
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Versioned operation result",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": [
                                        "contract_version",
                                        "effects",
                                        "ok",
                                        "operation",
                                        "result",
                                    ],
                                    "properties": {
                                        "contract_version": {"const": 1},
                                        "effects": {
                                            "const": contract["effects"],
                                        },
                                        "ok": {"const": True},
                                        "operation": {"const": contract["operation"]},
                                        "result": contract["result_schema"],
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Invalid operation arguments"},
                    "401": {"description": "Missing or invalid bearer token"},
                },
            }
        }
    return document


def make_handler(
    token: str,
    capabilities: list[str],
) -> type[BaseHTTPRequestHandler]:
    """Build an isolated request handler for one unguessable session token."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "LibraryOS/1"

        def _json(self, status: int, value: object) -> None:
            payload = json.dumps(value, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/openapi.json":
                host, port = self.server.server_address
                self._json(200, openapi_document(host, port))
                return
            self._json(404, {"ok": False, "error": {"code": "route_not_found"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.headers.get("Authorization") != f"Bearer {token}":
                self._json(401, {"ok": False, "error": {"code": "unauthorized"}})
                return
            prefix = "/v1/operations/"
            if not self.path.startswith(prefix):
                self._json(404, {"ok": False, "error": {"code": "route_not_found"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1_000_000:
                    raise StorageError("Request is too large", code="request_too_large")
                arguments = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(arguments, dict):
                    raise StorageError("Arguments must be an object", code="arguments_invalid")
                self._json(
                    200,
                    invoke(
                        self.path[len(prefix):],
                        arguments,
                        capabilities=capabilities,
                    ),
                )
            except (json.JSONDecodeError, SchemaError, StorageError, TypeError) as error:
                self._json(
                    400,
                    {
                        "contract_version": 1,
                        "operation": f"libraryos.{self.path[len(prefix):]}",
                        "ok": False,
                        "error": {
                            "code": getattr(error, "code", "arguments_invalid"),
                            "message": str(error),
                            "path": getattr(error, "path", None),
                        },
                    },
                )

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
    capabilities: list[str] | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Create, but do not start, a localhost-only API server."""

    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise StorageError(
            "LibraryOS serves only localhost in v1",
            code="nonlocal_bind_rejected",
            path=host,
        )
    session_token = token or secrets.token_urlsafe(32)
    session_capabilities = list(capabilities) if capabilities is not None else ["*"]
    return (
        ThreadingHTTPServer(
            (host, port),
            make_handler(session_token, session_capabilities),
        ),
        session_token,
    )


def serve(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the local service until interrupted."""

    server, token = create_server(host=host, port=port)
    actual_host, actual_port = server.server_address
    print(
        json.dumps(
            {
                "url": f"http://{actual_host}:{actual_port}",
                "token": token,
                "warning": "Keep this per-session token private.",
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
