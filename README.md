# LibraryOS

**The research library agents can operate over.**

LibraryOS is a local-first, agent-native research library. It gives researchers
and software agents a shared, provenance-aware environment for acquiring,
preparing, organizing, inspecting, and reusing scholarly sources.

LibraryOS is not a chatbot over PDFs, a citation formatter, or an arbiter of
scientific truth. It preserves authoritative source material and the durable
state needed by many research workflows while keeping acquisition, preparation,
inspection, and scientific assessment explicitly distinct.

## Project status

LibraryOS is a hardened, pre-release Stage 1 implementation. Its v1 schemas,
safe filesystem foundation, immutable source import, identity-gated discovery
and acquisition with quarantine, metadata resolution, rebuildable search,
source-bound derivative preparation, collections, reviews, assessments, jobs,
safe lifecycle controls, transactional migrations, Python operations, CLI, and
token-protected localhost API are implemented and tested. A disposable
migration of the 4,060-work first production corpus preserved all 15,696
artifact hashes and passed v1 validation. The graphical human interface is the
next stage. Until a stable release exists, maintain independent backups and do
not use this development version as the only copy of a library.

The current authoritative design is [docs/product-specification.md](docs/product-specification.md).
It defines the product boundary, data model, interfaces, human experience,
security model, migration strategy, delivery phases, and v1 acceptance
criteria. The [Stage 1 hardening roadmap](docs/stage-1-roadmap.md) turns those
criteria into an auditable checklist, and the
[Stage 1 acceptance report](docs/stage-1-acceptance-report.md) records the
verification evidence and remaining limitations. Stage 1 was accepted on
2026-09-19. The [Stage 2 UI plan](docs/stage-2-ui-plan.md) now specifies the
graphical **Library** application and is the design gate before UI
implementation.

## Core idea

```text
researchers · agents · research applications
                    │
          LibraryOS operations
                    │
     works · sources · derivatives
 collections · reviews · assessments
                    │
 portable manifests + immutable files
          + rebuildable indexes
```

LibraryOS is domain-neutral. Mechanism Atlas is its first substantial
integration and migration case, not an assumption in the core model.

## Working product vocabulary

- **LibraryOS** — the platform and public project
- **Library** — the human-facing application
- **library** — one managed local store
- **work** — a scholarly object such as an article, preprint, thesis, dataset,
  standard, book chapter, or report
- **source** — an authoritative acquired representation of a work
- **derivative** — a generated representation used for navigation or analysis
- **collection** — a project-specific selection and ordering of works
- **review** — a record of exactly what source material was examined
- **assessment** — a contextual judgment made for a particular purpose
- **locator** — a precise page, section, figure, table, passage, or other
  evidence location

## Repository boundary

This repository will contain schemas, migrations, the core implementation,
interfaces, UI, tests, documentation, and small redistributable fixtures.

It will never contain a user's publication library, restricted source files,
credentials, cookies, signed URLs, or private research assessments. A library
is a separate local data instance outside Git.

## Design principle

> LibraryOS manages scholarly sources and durable research state. Projects
> describe why those sources matter.

Domain-specific meaning belongs in project collections or namespaced
extensions. It does not belong in the universal work schema.

Collections may be private-library JSON records or external JSON/YAML files
owned by project repositories. LibraryOS registers an external file by path and
validated SHA-256 without copying it. Updates require the expected hash, so a
human or agent cannot silently overwrite a concurrent project-side edit.

## Install for development

LibraryOS requires Python 3.11 or newer.

```bash
git clone https://github.com/davidkastner/libraryos.git
cd libraryos
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Initialize a private library outside a Git worktree:

```bash
libraryos init /path/to/private-library --title "Research Library"
libraryos work-create \
  --library /path/to/private-library \
  --type article \
  --title "Example work" \
  --identifier doi:10.0000/example
libraryos rebuild --library /path/to/private-library
libraryos search --library /path/to/private-library example
libraryos validate --library /path/to/private-library
```

Commands emit canonical JSON by default. The Python entry point for all client
operations is `libraryos.operations.invoke`. Start the local API with:

```bash
libraryos serve
```

The service binds to `127.0.0.1`, prints a new session token, requires that
token for operations, and exposes its contract at `/openapi.json`.

External-system adapters can use `work.resolve_identifiers`,
`external_document.sync.preview`, `external_document.sync.apply`,
`external_document.query`, `occurrence.query`, and `read.resolve_many` to
synchronize document-scoped references and navigate available sources. These
operations are domain-neutral: adapter-specific meaning stays in opaque
namespaced extensions, and synchronization never implies scientific inspection
or support.

Launch the graphical **Library** interface for a local library with:

```bash
libraryos serve --library /path/to/private-library
```

The browser interface lists and searches papers, filters by local PDF
availability, and opens authoritative local PDFs in macOS Preview. Opening a
paper does not create a review or claim that scientific inspection occurred.

For a development checkout on macOS, install a local `Library.app` wrapper with:

```bash
packaging/macos/install-development-app.sh
```

This unsigned development wrapper runs the current checkout rather than copying
its Python environment into the application. A distributable, signed,
self-contained macOS build remains a later packaging stage.

## Data safety

- Keep library instances outside Git and maintain independent backups.
- Source bytes are copied into private work bundles and never stored in SQLite.
- URL acquisition requires explicit identity evidence before network access.
- Persisted URLs omit user information, queries, and fragments; ambiguous or
  deceptive nonempty responses are retained unchanged in quarantine and are
  never attached to a work.
- Indexes under `.libraryos/` are disposable and can be rebuilt.
- Import, preparation, search, or opening never means scientific inspection.
- Reviews are immutable and require exact source hashes plus explicit coverage.
- Collection removal is previewed, staged into recoverable trash, and only
  permanently purged with separate authority and exact transaction-ID
  confirmation.

See [the product specification](docs/product-specification.md) for the complete
model, [the threat model](docs/threat-model.md), the
[backup and recovery guide](docs/backup-and-recovery.md), and
[the migration baseline](docs/migration-baseline.md) for the first large-corpus
compatibility result.

For agent use, see the [agent operations guide](docs/agent-operations.md) and
the repository-owned [LibraryOS skill](skills/libraryos/SKILL.md). Release and
schema compatibility rules are documented in the
[release policy](docs/release-policy.md).

The agent skill is shipped in Git checkouts and source distributions. It is not
silently installed or activated by the Python wheel; agent hosts retain control
of skill review and installation.
