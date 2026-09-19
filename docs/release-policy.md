# Release and compatibility policy

LibraryOS is currently `0.1.0.dev0` and has not published a stable release.
Building an artifact locally is verification, not publication. A Git commit,
push, tag, package upload, signed application, production migration, and
production cutover are distinct actions.

## Versioning

LibraryOS uses semantic versioning for the Python package and independent major
versions in authoritative schema identifiers and operation envelopes.

- Patch releases fix behavior without intentionally changing supported
  contracts.
- Minor releases may add optional fields, record kinds, operations, or
  capabilities while preserving existing major-version readers.
- Major releases may make incompatible contract changes and require an explicit
  migration path.
- Development versions may change before the first stable release, but any
  authoritative-record change must still use a previewable migration rather
  than silently rewriting a library.

Readers reject unsupported newer schema majors with a structured error and
recovery guidance. Unknown namespaced extensions are preserved. Deprecations
must identify the replacement and remain supported for at least one subsequent
minor release after the first stable release unless retaining them would create
a security or data-integrity defect.

## Release checklist

Before proposing a release:

1. Run the complete test and lint suite on every supported Python version.
2. Build the source distribution and wheel from a clean checkout.
3. Install the wheel with dependencies in a fresh supported environment.
4. Verify that the wheel contains the executable package and schemas, and that
   the source distribution contains the repository documentation and
   repository-owned agent skill.
5. Verify operation contracts and an initialize/create/validate workflow from
   the installed wheel.
6. Run privacy scanning against source and built artifacts.
7. Review schema and operation changes, migrations, security implications, and
   release notes.
8. Confirm that no library instance, publication byte, credential, signed URL,
   or private assessment is tracked.

The wheel is the executable Python distribution and includes the JSON schemas
used at runtime. The source distribution and Git repository additionally carry
the operator documentation and `skills/libraryos/SKILL.md`. Installing the
wheel does not silently install or activate an agent skill; an agent host must
review and install the repository-owned skill through its own trust mechanism.

Publishing requires explicit owner authorization. The release operator then
creates the reviewed commit/tag and publishes only the verified artifacts.

## Production adoption

A package release does not authorize migration of a user's library. Production
adoption requires a fresh audit, preview, disposable rehearsal, backup, exact
count/hash comparison, and explicit approval for the live write. Keep the old
library recoverable until all consumers have passed the new configuration and
read/write checks.
