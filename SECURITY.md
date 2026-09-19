# Security policy

LibraryOS v1 is designed for one user on a trusted local machine. It is not yet
a hardened multi-user or remotely hosted service.

## Reporting

Please report security issues privately to the repository owner rather than
opening a public issue. Include the affected version, reproduction steps, and
the potential impact without attaching copyrighted publication files.

## Security invariants

- The local service binds to `127.0.0.1` by default and requires a per-session
  token.
- Manifest paths are relative and must resolve beneath the configured library.
- Symlink escapes and path traversal are rejected.
- Credentials, cookies, authorization headers, and expiring signed URLs are
  never persisted.
- Publication bytes are not stored in SQLite, logs, diagnostics, or telemetry.
- Restricted sources and their derivatives are excluded from default exports.
- Destructive operations use a separate capability and provide an impact
  preview.
- Telemetry is disabled by default.

Do not expose a LibraryOS v1 service to an untrusted network.
