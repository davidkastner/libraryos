"""Token-protected localhost JSON API."""

from __future__ import annotations

import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
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
    library: str | Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Build an isolated request handler for one unguessable session token."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "LibraryOS/1"

        def _headers(self, content_type: str, length: int) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

        def _json(self, status: int, value: object) -> None:
            payload = json.dumps(value, sort_keys=True).encode()
            self.send_response(status)
            self._headers("application/json", len(payload))
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def _asset(self, relative: str) -> None:
            root = files("libraryos").joinpath("ui")
            candidate = root.joinpath(relative)
            if not candidate.is_file():
                self._json(404, {"ok": False, "error": {"code": "route_not_found"}})
                return
            payload = candidate.read_bytes()
            content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            self.send_response(200)
            self._headers(content_type, len(payload))
            if relative == "index.html":
                self.send_header(
                    "Set-Cookie",
                    f"libraryos_session={token}; HttpOnly; SameSite=Strict; Path=/",
                )
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/openapi.json":
                host, port = self.server.server_address
                self._json(200, openapi_document(host, port))
                return
            if library is not None:
                route = self.path.partition("?")[0]
                if route in {"/", "/index.html"}:
                    self._asset("index.html")
                    return
                if route.startswith("/assets/"):
                    self._asset(route.removeprefix("/"))
                    return
            self._json(404, {"ok": False, "error": {"code": "route_not_found"}})

        def do_POST(self) -> None:  # noqa: N802
            bearer_authorized = self.headers.get("Authorization") == f"Bearer {token}"
            cookie_authorized = f"libraryos_session={token}" in self.headers.get(
                "Cookie", ""
            ).split("; ")
            if cookie_authorized:
                expected_origin = f"http://{self.headers.get('Host')}"
                cookie_authorized = self.headers.get("Origin") == expected_origin
            if not bearer_authorized and not cookie_authorized:
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
                if library is not None:
                    supplied_library = arguments.get("library")
                    if supplied_library is not None and Path(supplied_library).resolve() != Path(library).resolve():
                        raise StorageError(
                            "This session is bound to a different library",
                            code="library_scope_conflict",
                        )
                    arguments["library"] = str(Path(library).resolve())
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
                            **(
                                {"details": error.details}
                                if getattr(error, "details", None) is not None
                                else {}
                            ),
                            **(
                                {"next_actions": error.next_actions}
                                if getattr(error, "next_actions", None) is not None
                                else {}
                            ),
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
    library: str | Path | None = None,
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
            make_handler(session_token, session_capabilities, library),
        ),
        session_token,
    )


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    library: str | Path | None = None,
    open_browser: bool = True,
    print_token: bool = True,
) -> None:
    """Run the local service until interrupted."""

    server, token = create_server(host=host, port=port, library=library)
    actual_host, actual_port = server.server_address
    launch = {"url": f"http://{actual_host}:{actual_port}"}
    if print_token:
        launch.update(
            {
                "token": token,
                "warning": "Keep this per-session token private.",
            }
        )
    print(json.dumps(launch), flush=True)
    if library is not None and open_browser:
        import webbrowser

        webbrowser.open(f"http://{actual_host}:{actual_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
