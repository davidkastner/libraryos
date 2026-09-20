# LibraryOS product and architecture specification

Status: **design baseline for review**
Target: **LibraryOS v1**
Last updated: **2026-09-19**

This document is normative for the first implementation of LibraryOS. It
describes the intended finished product, the v1 delivery boundary, and the
constraints that should remain true as the system grows. When implementation
and this document disagree, the disagreement must be resolved explicitly rather
than silently changing the product model.

## 1. Product definition

LibraryOS is a local-first, agent-native research library: a programmable
operating layer through which researchers and agents acquire, verify, prepare,
organize, inspect, and reuse scholarly sources while preserving provenance and
project context.

Its short description is:

> The research library agents can operate over.

LibraryOS provides shared, persistent state for interchangeable humans, agents,
and research applications. It makes original publications easy to recover and
inspect, generated representations easy to search, and claims about what was
reviewed difficult to overstate accidentally.

### 1.1 Product promises

LibraryOS must make these questions cheap and reliable:

1. What scholarly work is this?
2. Which exact representations and versions are available locally?
3. Where did each source come from, and under what access conditions?
4. Which derivative is best for search, reading, figures, or agent navigation?
5. What failed, remains unavailable, or has known quality limitations?
6. What exact material did a person or agent inspect?
7. Why does this work matter to this project?
8. Which precise source locations support or challenge that assessment?
9. Can another tool reproduce, validate, or migrate this state without the UI?
10. Can a project be archived or removed without corrupting shared library data?

### 1.2 Non-goals

LibraryOS does not:

- decide whether a scientific claim is true;
- treat acquisition, conversion, search, or opening a file as inspection;
- treat a citation occurrence as proof that the cited source supports a claim;
- silently resolve consequential identity conflicts with fuzzy matching;
- replace authoritative source files with extracted text or summaries;
- require a hosted service or central LibraryOS account;
- store publication bytes in SQLite;
- embed one research domain, dataset, or manuscript workflow in its core;
- automate a graphical interface as its agent API;
- attempt to replace every citation-writing or PDF-annotation feature in v1.

### 1.3 Users and primary jobs

Researchers need to:

- build and maintain reliable research libraries;
- open the best locally available representation of a work;
- create temporary or long-lived project collections;
- maintain reading queues and recovery queues;
- record review scope and project-specific judgments honestly;
- export citations and collection summaries;
- understand failures and storage costs;
- archive or purge finished projects safely.

Agents and research applications need to:

- resolve exact work identity;
- acquire sources only through authorized access classes;
- receive canonical structured records;
- search prepared representations and recover authoritative locations;
- create reproducible derivatives;
- record bounded review and assessment state;
- operate within explicit library and collection capabilities;
- resume work safely after interruption;
- avoid direct dependence on SQLite or UI structure.

Library maintainers need to:

- validate every manifest and hash;
- rebuild indexes from authoritative files;
- migrate schemas transactionally;
- observe jobs, attempts, exceptions, and storage;
- regenerate stale derivatives without reacquiring sources;
- export portable metadata without exporting restricted bytes.

## 2. Architectural principles

The following are invariants, not preferences.

1. **Local first.** A fully useful library can run on one machine without a
   cloud account.
2. **Files are authoritative.** Portable manifests and immutable source bytes
   are the durable record. Databases and search indexes are rebuildable views.
3. **Sources are immutable.** Correction creates a new source record or work
   relation; source bytes are never edited in place.
4. **Derivatives declare lineage.** Every generated artifact identifies its
   input hash, generator, generator version, time, and quality state.
5. **Identity precedes acquisition.** A filename or search result is not enough
   to assign source bytes to a scholarly work.
6. **Stages remain separate.** Discovery, acquisition, preparation, opening,
   review, and assessment cannot imply one another.
7. **Context lives with context.** Narrative role, relevance, priority, and
   claim support belong to a collection or project, not the global work.
8. **Interfaces precede clients.** The app, CLI, Python API, HTTP API, and
   future protocol adapters call the same application operations.
9. **Domain behavior is layered.** Core schemas are domain-neutral. Adapters
   may add namespaced extensions without changing core meaning.
10. **Restricted material remains private.** Access to a source is not
    permission to redistribute it or its derivatives.
11. **Operations are resumable.** Mutations are atomic, idempotent where
    practical, hash-guarded, and recorded.
12. **Agents receive least authority.** Read, acquisition, review, assessment,
    collection mutation, and deletion are distinct capabilities.

## 3. System boundary

```text
┌───────────────────────────────────────────────────────────────┐
│ Clients                                                       │
│ Library.app · CLI · Python · local HTTP · future MCP          │
└────────────────────────────┬──────────────────────────────────┘
                             │ versioned operations
┌────────────────────────────▼──────────────────────────────────┐
│ LibraryOS core                                                │
│ identity · acquisition · preparation · search · collections   │
│ reviews · assessments · validation · jobs · import/export     │
└──────────────┬──────────────────────────────┬─────────────────┘
               │                              │
┌──────────────▼─────────────┐  ┌────────────▼─────────────────┐
│ Authoritative local files  │  │ Rebuildable runtime state    │
│ manifests · sources        │  │ SQLite · full-text indexes   │
│ derivatives · records      │  │ caches · sessions · queues   │
└────────────────────────────┘  └──────────────────────────────┘
               ▲
               │ explicit adapters
┌──────────────┴────────────────────────────────────────────────┐
│ Research projects and domain tools                            │
│ manuscript repos · mechtools · systematic reviews · datasets  │
└───────────────────────────────────────────────────────────────┘
```

### 3.1 Code repository

The public `libraryos` repository owns:

- versioned JSON Schemas and schema documentation;
- migrations and compatibility policy;
- Python core and application services;
- CLI and local HTTP API;
- human-facing web UI and desktop packaging;
- provider and converter plugin interfaces;
- generic agent instructions and examples;
- tests and small legally redistributable fixtures;
- import/export adapters for standard bibliographic formats;
- documentation and release tooling.

It does not own any user's actual library.

### 3.2 Library instance

A library instance is a private, configurable directory outside source-control
worktrees. The target shape is:

```text
library-root/
├── library.json
├── works/
│   └── <opaque-work-key>/
│       ├── manifest.json
│       ├── source/
│       ├── derived/
│       └── supplements/
├── records/
│   ├── reviews/
│   └── assessments/
├── collections/
├── quarantine/
└── .libraryos/
    ├── index.sqlite
    ├── search/
    ├── cache/
    ├── jobs/
    └── locks/
```

`library.json`, work manifests, collection manifests, review records,
assessment records, and file hashes are authoritative. Everything under
`.libraryos/` is disposable and rebuildable, except active lock semantics.

Collections may instead live in an external project repository. The library
records their registered location and last validated hash; it does not copy
them silently.

The binding is an authoritative collection-registration record under
`records/collection-registrations/`, not disposable runtime state. External
writes use compare-and-swap semantics: the caller's expected hash, the
registration's last validated hash, and the current file hash must agree before
atomic replacement. An out-of-band edit is reported as a conflict and is never
silently adopted or overwritten.

### 3.3 Project ownership

A project owns its collection membership and contextual research judgments.
This permits the same work to be used differently in different projects and
lets project state be version-controlled without publication bytes.

Generated project outputs may include:

- a human-readable literature overview;
- a BibTeX, CSL JSON, or RIS bibliography;
- agent-readable canonical JSON;
- a reading or recovery queue;
- a collection validation report.

The collection manifest, not a generated README or bibliography, is
authoritative.

## 4. Domain model

### 4.1 Work

A `Work` is a conceptual scholarly object. It may be an article, preprint,
thesis, book, chapter, report, standard, dataset, software paper, patent, or
other cited research object.

A work owns:

- an opaque LibraryOS identifier;
- zero or more external identifiers with scheme and normalized value;
- type and bibliographic metadata;
- metadata assertions and their providers;
- relationships to other works;
- source candidates, operation history, and current exception summaries;
- references to its sources, derivatives, and supplements.

DOI must not be required. External identifiers include DOI, PMID, PMCID, arXiv,
ISBN, ISSN-scoped citation, URL, accession, and user-defined schemes. Identifier
aliases are unique only within their scheme.

LibraryOS deterministically normalizes recognized schemes before authoritative
writes. DOI resolver forms and case, PMID/PMCID wrappers and leading zeroes,
arXiv resolver forms, URL host/default-port/fragment forms, and ISBN punctuation
therefore cannot create duplicate works. URL query parameters are retained
because they may carry identity. Unknown and user-defined schemes are opaque:
their value is preserved exactly unless the caller supplies an explicit
normalized alias. Similar titles never establish identity. Preprints,
manuscripts, corrections, and versions remain separate works joined by explicit
relations.

The work directory key is opaque and stable. Users and APIs address works by
LibraryOS ID or an unambiguous external identifier. Implementations must not
expose hash-derived directory names as the primary interface or rename legacy
work directories merely for aesthetics.

### 4.2 Source

A `Source` is an acquired, authoritative representation of a work:

- publisher PDF;
- publisher JATS XML or full-text HTML;
- accepted manuscript;
- preprint;
- repository copy;
- dataset archive;
- supplementary file;
- user-provided source.

Each source records:

- source ID and work ID;
- bundle-relative path;
- SHA-256 and byte size;
- detected media type;
- semantic role and publication version;
- identity-verification status and method;
- provider and stable canonical URL;
- acquisition time and access class;
- license assertion and its provenance;
- original filename when useful;
- warnings and relationships to other sources.

Access class and redistribution permission are independent. No credential,
cookie, authorization header, or expiring signed URL may be persisted.

Source registration is append-only. If bytes differ, LibraryOS records another
source rather than overwriting the existing source.

### 4.3 Derivative

A `Derivative` is generated from one or more exact source hashes. Examples:

- source-faithful Markdown;
- extracted or OCR text;
- GROBID TEI;
- rendered pages;
- extracted figures and captions;
- tables;
- thumbnails;
- search indexes;
- embeddings;
- explicitly labeled navigation summaries.

Each derivative records its inputs, generator identity and version,
configuration fingerprint, creation time, output hash, semantic role, media
type, quality state, warnings, and any source locators.

Derivatives never replace sources. Generated scientific summaries must remain
separate from source-faithful text. Search and embeddings are navigation aids,
not evidence adjudication.

### 4.4 Collection

A `Collection` is an ordered, project-specific selection of works. Its kind is
descriptive and extensible, not an enum that restricts behavior. Examples
include `manuscript`, `dataset-curation`, `systematic-review`, `reading-list`,
and `experiment-design`.

A collection owns:

- stable ID, title, description, status, and optional kind;
- creation/update metadata and optional project links;
- policies, such as whether citation export requires a local source;
- ordered membership;
- references to project-specific assessments and locators;
- namespaced extensions;
- output-generation configuration.

A membership may record:

- work reference by LibraryOS ID or external identifier;
- order, priority, disposition, and tags;
- citation key and citation policy;
- project-local notes;
- review requirements;
- assessment references;
- namespaced extensions.

Core schemas do not define values such as `foundational`, `introduction`, or
`mechanism_reference`. Projects may use vocabulary they document.

### 4.5 Review

A `Review` is a source-hash-bound record of material actually examined by a
human or agent. It records fact of examination, not agreement or truth.

A review owns:

- stable review ID;
- exact source hashes and work ID;
- reviewer kind and stable local identity;
- declared purpose;
- start and completion timestamps;
- coverage expressed as locators;
- observations and limitations;
- method or tool context when relevant;
- supersession links;
- optional cryptographic or application provenance.

Coverage must distinguish text, pages, figures, tables, supplements, and whole
source. “Opened” is never a review state. Inspection of extracted text does not
imply inspection of the original page or figure. Review does not automatically
transfer to a different source version or changed source hash.

Reviews live centrally because source-level examination can be reused, but they
remain immutable records. Project-specific interpretation belongs in an
assessment.

### 4.6 Assessment

An `Assessment` is a contextual judgment for a stated purpose. It may concern a
work, source, collection membership, claim, or project object.

It records:

- assessment ID and collection/project scope;
- subject reference;
- author kind and identity;
- purpose and optional controlled vocabulary;
- judgment, summary, observations, and limitations;
- supporting or challenging locators;
- related review IDs;
- time and supersession links;
- namespaced extensions.

An assessment may say that a work is relevant, foundational, conflicting,
insufficient, or useful for a narrative role. LibraryOS preserves the
assessment and its provenance; it does not endorse it.

### 4.7 Locator

A `Locator` points into a source or derivative. Supported generic forms include:

- page or page range;
- section or heading path;
- figure, panel, scheme, table, equation, or supplement;
- paragraph or line anchor in a stable derivative;
- quoted passage with context and hash;
- time range for audiovisual material;
- external fragment identifier.

A locator identifies both the target artifact and the coordinate system. A
derivative locator should carry a route back to the source when possible.

### 4.8 Relation

A typed `Relation` connects works or sources. Core relation types include:

- `is-version-of`;
- `is-preprint-of`;
- `is-correction-of`;
- `is-retraction-of`;
- `is-supplement-to`;
- `is-dataset-for`;
- `is-commentary-on`;
- `is-translation-of`;
- `is-part-of`;
- `references`.

Unknown relation types are preserved through namespaced extensions rather than
forced into an inaccurate core type.

### 4.9 Occurrence

An `Occurrence` records that a work identifier or citation appears in an
external document. It owns structural facts:

- adapter and external source identity;
- source document path or URI and content hash;
- external record/reference ID;
- normalized work reference or normalization error;
- attachment class and external object paths;
- inventory time.

An occurrence never asserts that the work supports the object to which it is
attached. Occurrences are imported through adapters and may be regenerated from
their authoritative external source.

### 4.10 Job, attempt, exception, and event

Long-running operations use a durable job model:

- a `Job` describes requested work, policy, capability scope, and progress;
- an `Attempt` records one provider/tool execution and bounded diagnostic;
- an `Exception` records a condition needing retry, changed authority, or
  reasoning;
- an `Event` records a state transition for audit and observability.

Jobs must be stoppable and resumable. Errors use stable machine-readable codes.
Diagnostics must not contain secrets or publication contents unnecessarily.

## 5. Schema and extension contract

### 5.1 Format

Authoritative machine records use JSON. JSON Schema defines and validates every
record type. YAML is accepted as a human-authoring representation for
collections and project assessments; it must round-trip through the same
canonical data model.

Every top-level record includes:

```yaml
schema: https://libraryos.dev/schemas/collection/v1
schema_version: 1
id: example-id
```

Schema URLs are identifiers and need not imply that validation requires network
access. Released schemas ship with the package.

### 5.2 Compatibility

- Additive optional fields are backward compatible within a major schema
  version.
- Meaning changes or required-field changes require a new major schema version.
- Readers reject unsupported newer major versions with structured recovery
  guidance.
- Migrations preview their changes, create a recovery record, validate output,
  and update atomically.
- Unknown namespaced extensions are preserved losslessly.

### 5.3 Extensions

Domain-specific data uses reverse-DNS keys:

```yaml
extensions:
  org.mechanismatlas.mech:
    entry_ids: [mcsa_0123]
    occurrence_type: mechanism_reference
```

The core validates that extensions are JSON-compatible but does not interpret
their contents. An installed adapter may publish a separate schema for its
namespace.

Extensions may add context; they may not override core identity, source hashes,
access controls, or review semantics.

### 5.4 Illustrative collection

```yaml
schema: https://libraryos.dev/schemas/collection/v1
schema_version: 1
id: enzyme-representation-paper
title: Enzyme representation paper
kind: manuscript
status: active

policy:
  citation_requires_local_source: true

members:
  - work:
      identifier:
        scheme: doi
        value: 10.1093/nar/gkl774
    order: 10
    priority: essential
    disposition: core
    citation_key: Holliday_2007
    tags: [historical-precedent]
    assessments:
      - purpose: narrative-role
        summary: >
          Establishes an early searchable representation of catalytic steps.
        limitations: >
          It is database-centered rather than a portable exchange document.
```

The example happens to describe a manuscript, but no field is specific to
enzyme mechanisms or manuscript writing.

## 6. Status semantics

LibraryOS exposes independent dimensions rather than a single `ready` flag.

### 6.1 Work pipeline

- identity: `unresolved`, `candidate`, `verified`, `conflicting`;
- discovery: `not_attempted`, `ready`, `partial`, `failed`;
- acquisition: `not_attempted`, `acquired`, `partial`, `unavailable`,
  `restricted`, `failed`;
- text preparation: `not_attempted`, `prepared`, `partial`, `not_applicable`,
  `failed`;
- visual preparation: `not_attempted`, `prepared`, `partial`,
  `not_applicable`, `failed`;
- supplements: `not_advertised`, `not_attempted`, `prepared`, `partial`,
  `unavailable`, `failed`;
- exception: `none`, `queued`, `assigned`, `resolved`, `deferred`.

These statuses are computed projections over records where practical. They do
not create scientific meaning.

### 6.2 Review and assessment

Review is expressed through actual review records, not a mutable
`scientific_inspection` flag. User-facing summaries may compute:

- no review;
- text partially reviewed;
- visuals partially reviewed;
- supplement partially reviewed;
- review coverage satisfies a named collection requirement.

Assessment state is collection-specific and must never be inferred from review
coverage.

### 6.3 Best available reading representation

The default **Read** action resolves deterministically:

1. preferred local PDF when available;
2. local structured full text in the Library reader;
3. verified local HTML;
4. stable source landing page;
5. recovery actions when metadata-only.

Users can always choose another available representation. Opening any
representation records navigation history only if enabled; it never creates a
review.

## 7. Core operations

All clients use a shared application layer implementing these operations:

| Operation | Contract |
|---|---|
| `resolve` | Establish or challenge exact scholarly identity |
| `discover` | Find legitimate source candidates without acquiring them |
| `acquire` | Retrieve an authorized candidate and verify its bytes and identity |
| `import` | Register user-provided bytes after identity verification |
| `prepare` | Create reproducible, source-linked derivatives |
| `search` | Navigate metadata and prepared content |
| `read` | Resolve and open the best representation |
| `collect` | Create and maintain project collections |
| `review` | Record hash-bound examination coverage |
| `assess` | Record contextual judgments and locators |
| `cite` | Export verified bibliographic records |
| `validate` | Check schemas, paths, hashes, lineage, and indexes |
| `rebuild` | Recreate disposable indexes from authoritative records |
| `archive` | Freeze and export project state |
| `purge` | Remove project state or explicitly selected unreferenced assets |

Each operation has:

- a versioned input and result schema;
- stable error codes and next-action guidance;
- an explicit mutation and network-access declaration;
- capability requirements;
- synchronous execution for bounded work or a durable job for long work;
- the same semantics across Python, CLI, HTTP, and UI.

## 8. Agent contract

Agents are first-class clients, not UI automation scripts.

### 8.1 Interfaces

V1 provides:

- a typed Python API;
- a CLI whose default output is canonical JSON;
- a localhost HTTP API with generated OpenAPI;
- an agent skill describing scientific and operational boundaries.

A future MCP server wraps the same application operations; it is not a second
implementation.

### 8.2 Capabilities

Sessions may be granted:

- `library.read`;
- `source.acquire`;
- `derivative.prepare`;
- `collection.write:<collection-id>`;
- `review.write`;
- `assessment.write:<collection-id>`;
- `library.maintain`;
- `destructive.purge`.

Read access can be scoped to one collection. A collection-scoped agent need not
learn the existence of unrelated private works.

### 8.3 Agent result requirements

Agent-facing results identify:

- the operation and contract version;
- exact LibraryOS work/source IDs;
- source and derivative hashes where relevant;
- warnings and limitations;
- whether network or mutation occurred;
- next actions for bounded failures;
- locators that route navigation results back to source material.

Agents must not query SQLite directly. Stable IDs and APIs, not filesystem paths,
are the integration contract.

## 9. Human application

The human-facing app is named **Library**. It should feel like a calm,
high-density research environment, not an administrative database.

### 9.1 Navigation

Primary navigation:

- Library
- Collections
- Reading Queue
- Recently Added
- Needs Source
- Needs Preparation
- Needs Review
- Exceptions

Maintenance and settings remain secondary.

### 9.2 Work list

Each row shows:

- title, primary authors, year, venue, and work type;
- source availability and version;
- text, visual, and supplement preparation state;
- review coverage without implying assessment;
- collection membership;
- warnings or unresolved exceptions.

Search covers verified metadata and prepared content. Filters use explicit
status dimensions. Search hits show matched context and a source-navigation
route.

### 9.3 Work detail

The detail view separates:

1. Identity and bibliographic metadata
2. Sources and acquisition provenance
3. Prepared representations
4. Reviews
5. Project-specific use and assessments
6. Relations and versions
7. Attempts, warnings, and exceptions

Primary action: **Read**.

Secondary actions include open PDF, read structured article, inspect pages,
figures, tables, and supplements, visit source page, copy citation, add to
collection, and reveal evidence bundle.

### 9.4 Reader

The internal reader supports:

- source-linked structured text;
- page and figure navigation;
- visible extraction warnings;
- copyable stable locators;
- side-by-side derivative and source-page inspection;
- explicit review recording with selected coverage;
- project assessment entry without conflating it with review.

V1 does not implement a custom PDF annotation engine. It can open local PDFs in
the system viewer and use rendered pages for integrated source inspection.

### 9.5 Collection workspace

A collection view provides:

- ordered membership;
- priority and disposition;
- reading and recovery queues;
- project-specific assessments and locators;
- validation against collection policy;
- generated literature overview and bibliography previews;
- archive and purge controls with impact preview.

## 10. Acquisition, preparation, and search

### 10.1 Acquisition

LibraryOS:

- resolves identity before storing source bytes;
- prefers legitimate open structured sources and repositories;
- permits institutional or user-provided access only when authorized;
- applies bounded requests, redirects, sizes, retries, and per-host limits;
- detects login pages, bot challenges, abstract-only pages, and content-type
  mismatches;
- records stable provenance without credentials or session state;
- quarantines ambiguous bytes rather than assigning them speculatively;
- serializes mutation of one work while allowing unrelated works concurrently.

The operator authorizes access classes. Being connected to an institutional
network does not itself grant LibraryOS authority to use it.

Acquisition starts only after the caller supplies identity evidence that agrees
with the target work. V1 accepts an expected source hash, an identifier or exact
title that can be checked against textual source bytes, or a previously
verified provider candidate registered on that work with verifier and
timestamp. Candidate acquisition must match its canonical URL and access class;
the registered candidate supplies its authoritative identity method, so callers
need only pass its `candidate_id`. An inline assertion cannot self-certify a
candidate. The successful transfer of bytes is not identity evidence. Source
records may be marked `verified` only after one of these checks succeeds.

Request URLs may contain transient query parameters needed for retrieval, but
durable job, source, and quarantine records retain only a canonical HTTP(S)
origin and path. User information, queries, fragments, credentials, cookies,
authorization headers, and signed state are never persisted. A nonempty
response rejected as ambiguous, restricted, abstract-only, a landing page, a
bot challenge, corrupt, or type-mismatched is copied byte-for-byte into a
hash-addressed quarantine record. It is not a work source and cannot be
prepared or reviewed as one.

### 10.2 Preparation

Preparation preserves:

- headings and reading order where reliable;
- page anchors;
- captions and tables;
- links to rendered source pages;
- figures with complete-page fallbacks;
- scientific notation without silent repair;
- quality and extraction warnings.

Mechanistic drawings, equations, charts, and multi-panel figures may contain
claims absent from prose. Page renders remain the visual authority when
extraction is incomplete.

### 10.3 Search

V1 supports:

- normalized metadata search;
- literal and ranked term search over prepared text;
- filters over identifiers, type, year, source, status, collections, and tags;
- bounded snippets with artifact and source locators.

Search results never imply review or support. Semantic/vector search may be
added later as another navigation view, never as authority.

## 11. Lifecycle, archive, and purge

Projects are cheap to create and safe to retire. Works are deduplicated across
projects and generally longer-lived.

### 11.1 Archive

Archiving a collection:

- freezes a hash-addressed snapshot of its manifest;
- optionally exports generated human and citation artifacts;
- records schema and generator versions;
- changes status without deleting works or assessments;
- remains reversible.

### 11.2 Remove collection

Removing a collection deletes or detaches only its:

- membership and ordering;
- project-local notes;
- contextual assessments;
- generated outputs;
- registered external collection link.

The operation previews all effects and does not delete shared works or sources.

### 11.3 Garbage collection

Garbage collection is always explicit and previewable. A work is a candidate
only when no active or archived collection, review, assessment, relation,
external occurrence, or retention policy references it.

Deletion is two-stage:

1. move selected bundles to a library trash area with a recovery deadline;
2. permanently delete only through a separate confirmed operation.

LibraryOS may retain a metadata-and-hash tombstone to prevent accidental
reacquisition, depending on library policy. Restricted source deletion also
removes restricted derivatives derived from it. Audit output records what was
removed and whether it remains recoverable.

## 12. Security and privacy

V1 is single-user and local-only.

- The service binds to `127.0.0.1`, never all interfaces by default.
- Each launch uses an unguessable per-session token.
- Files are served only after resolving manifest-declared paths beneath the
  configured library root.
- Symlink escapes and path traversal are rejected.
- Browser content is treated as untrusted; restrictive content security policy
  and download headers are used.
- Credentials, cookies, authorization headers, and signed URLs are never
  persisted in records, logs, diagnostics, or crash reports.
- Logs avoid publication contents and redact configured sensitive metadata.
- Destructive operations require a distinct capability and impact preview.
- Restricted sources and derivatives remain outside Git and export bundles
  unless explicitly, lawfully selected by the user.
- No telemetry is enabled by default.

V1 does not claim hardened multi-user isolation. Remote hosting, shared servers,
and team authorization require a later threat model.

## 13. Technology and repository ownership

### 13.1 Implementation shape

Recommended implementation:

- Python 3.11+ core and local service;
- Pydantic or equivalent typed boundary models backed by published JSON Schema;
- SQLite for rebuildable catalog, queues, and metadata search;
- filesystem manifests and content hashes as durable state;
- React and TypeScript for the Library UI;
- a browser-first local application served by `libraryos serve`;
- a thin signed macOS wrapper after the browser interface is stable.

The architecture should permit alternate frontends without forking semantics.

### 13.2 Target repository

```text
libraryos/
├── README.md
├── pyproject.toml
├── src/libraryos/
│   ├── application/
│   ├── domain/
│   ├── storage/
│   ├── providers/
│   ├── preparation/
│   ├── search/
│   ├── api/
│   └── cli/
├── schemas/
├── ui/
├── skills/paper-evidence/
├── tests/
├── docs/
└── packaging/macos/
```

This is an ownership map, not a requirement to create empty packages. Modules
are introduced only with working behavior and tests.

### 13.3 Dependency direction

```text
domain adapters and research applications
                 │
                 ▼
             LibraryOS
                 │
                 ▼
 generic parsers, providers, and converters
```

LibraryOS never imports `mechtools`, `mech-format`, private datasets, or local
checkout paths.

## 14. Mechanism Atlas integration

Mechanism Atlas is the first production integration and migration test.

### 14.1 Move from mechtools to LibraryOS

- library initialization and validation;
- generic work identity and metadata;
- source discovery, acquisition, and import;
- evidence bundles;
- generic preparation and search;
- attempts, jobs, and exceptions;
- collections, reviews, and assessments;
- UI and general agent interfaces.

### 14.2 Remain in mechtools

- parsing citations from MECH;
- mapping citations to MECH entries, mechanisms, steps, and changes;
- classifying MECH citation occurrences;
- synchronizing one MECH document into an occurrence inventory;
- mechanism-curation handoff and domain-specific evidence packets;
- interpretation of `org.mechanismatlas.*` extensions.

`mechtools` calls LibraryOS through its supported Python API. It does not import
private implementation modules or query LibraryOS SQLite.

### 14.3 Compatibility transition

- Accept `LIBRARYOS_ROOT` as the canonical library setting.
- Temporarily accept `MECHTOOLS_LITERATURE_LIBRARY`, warn when it is the only
  configured setting, and document removal timing.
- Generic `mechtools literature` commands initially delegate to LibraryOS.
- Preserve MECH-specific `entry` and `sync-entry` behavior in `mechtools`.
- Remove hardcoded sibling discovery of `manuscript-library`.
- Do not break current evidence-bundle paths during the first migration.

## 15. Existing-library migration

The current Mechanism Atlas library contains thousands of manifests and several
gigabytes of private material. Migration must be read-first and lossless.

1. Freeze and validate the current library; save counts and hashes.
2. Implement legacy-schema readers in LibraryOS.
3. Run read-only parity checks over every manifest and indexed occurrence.
4. Copy generic behavior and tests from `mechtools` with preserved semantics.
5. Make LibraryOS operate against the existing directory without relocation.
6. Rebuild a LibraryOS index and compare normalized counts and statuses.
7. Introduce new records through explicit, previewable schema migrations.
8. Convert MECH and MechEdit Markdown selections into authoritative collection
   YAML and regenerate their README and BibTeX outputs.
9. Delegate generic `mechtools literature` operations to LibraryOS.
10. Relocate or rename the physical library only after all consumers use
    configuration rather than discovery.

The migration must investigate and explain the current difference between
prepared and acquired work counts. It must not “fix” that discrepancy by
discarding historical/imported sources.

Legacy `scientific_inspection` flags are not promoted into formal reviews
without evidence of reviewer, source hash, scope, and purpose. Existing
scope-inspection notes may become project assessments with an explicit
`migration-unverified` limitation.

## 16. Delivery plan

### Phase 0 — contract and fixtures

- Review and approve this specification.
- Finalize entity terminology and JSON Schema conventions.
- Capture anonymized, redistributable fixtures representing major legacy cases.
- Define operation envelopes, errors, and capability declarations.
- Record baseline validation metrics for the current library.

Exit: schemas and behavioral examples are reviewable before implementation.

### Phase 1 — compatible core

- Package setup, domain models, schema validation, and atomic filesystem layer.
- Open existing library read-only.
- Read legacy manifests and rebuild a parity index.
- List, show, search metadata, validate, and report status.
- No manifest mutation until parity tests pass.

Exit: LibraryOS can faithfully describe the existing library from ordinary
files and detect discrepancies without changing it.

### Phase 2 — maintained write path

- Initialize new libraries.
- Resolve, discover, acquire, import, and prepare.
- Jobs, attempts, exceptions, locks, and resumability.
- New manifest writer and previewable migrations.
- Search prepared text and resolve best reading representation.

Exit: the generic `mechtools literature` implementation can delegate without
loss of capability.

### Phase 3 — collections and scientific state

- Collection YAML/JSON and external collection registration.
- Reviews, assessments, and locators.
- Markdown and bibliography generators.
- Archive, remove, and garbage-collection preview.
- Migrate MECH and MechEdit selections.

Exit: manuscript repositories hold authoritative collections and regenerate
their current literature products.

### Phase 4 — Library web app

- Browse, search, filter, work detail, and best-source opening.
- Structured reader with page/figure navigation.
- Collection workspace and queues.
- Review/assessment recording.
- Job and exception monitoring.

Exit: daily human literature work no longer requires filesystem navigation.

### Phase 5 — ecosystem integration

- Public Python API, CLI contracts, and local OpenAPI.
- `mechtools` adapter and environment transition.
- Collection-scoped agent capability examples.
- General-domain example independent of Mechanism Atlas.
- Performance and interruption testing at current library scale.

Exit: both agents and humans use LibraryOS as the authoritative generic layer.

### Phase 6 — macOS packaging

- Thin Library.app wrapper over the stable local interface.
- Signed/notarized packaging plan, file opening, and deep links.
- First-run library creation or selection.

Exit: LibraryOS is installable as a polished desktop application without
changing its underlying API or data model.

## 17. Product acceptance criteria

The repository/core criteria below gate completion of Stage 1. The human
workflow criteria gate Stage 2, and the final integration and packaging criteria
gate their corresponding delivery phases. Product v1 is complete only when all
of the following are demonstrated. Passing the Stage 1 gate must not be
described as completing the UI or the full v1 product.

### Portability and integrity

- A new library can be initialized outside Git.
- Every authoritative record validates against a shipped schema.
- Every source and derivative hash can be verified.
- SQLite and search indexes can be deleted and rebuilt without losing user
  meaning.
- Unknown extension namespaces survive read/write round trips.
- An interrupted mutation leaves either the old valid state or the new valid
  state, never a partial authoritative record.

### Generality

- DOI is optional and at least DOI, PMID/PMCID, arXiv, URL, and local identifiers
  are represented.
- Tests cover an article, a preprint/version relation, a report or standard, a
  dataset with supplements, and a local-only work.
- A non-biomedical, non-manuscript collection works without custom schema
  fields.
- No core package imports or names Mechanism Atlas concepts.

### Scientific honesty

- Acquisition and preparation never create review records.
- Opening a source never marks it reviewed.
- Review coverage is bound to exact source hashes and artifact locators.
- Project assessments are visibly distinct from source-level reviews.
- Search results link to sources and carry no support implication.
- Citation occurrence import creates structural records only.

### Human workflow

These criteria are intentionally **Stage 2** and remain open at the Stage 1
repository gate.

- A user can browse, search, filter, and inspect all works.
- **Read** opens the deterministic best local representation.
- A user can inspect text, source pages, figures, and supplements.
- A user can create a collection, order works, record assessments, and generate
  a literature overview and bibliography.
- Recovery, preparation, review, and exception queues are understandable
  without using the CLI.
- Project removal and garbage collection show a precise impact preview.

### Agent workflow

- CLI defaults to canonical JSON and has versioned errors.
- Python and HTTP interfaces perform the same operations with the same result
  semantics.
- An agent can be restricted to read one collection.
- Mutation and network use are explicit in operation contracts and results.
- Agents can resume long acquisition/preparation jobs safely.
- No supported agent workflow requires GUI automation or direct SQLite access.

### Security

- The service binds only to localhost by default and requires a session token.
- Path traversal and symlink escape tests pass.
- Secrets and ephemeral authorization data are absent from manifests, indexes,
  logs, and exports.
- Restricted bytes are excluded from default exports and Git-safe project
  outputs.
- Permanent purge is separately authorized from ordinary collection editing.

### Migration and scale

- All existing Mechanism Atlas manifests and occurrences pass read-only parity
  checks or produce a categorized, reviewed discrepancy report.
- Existing source bytes are not moved or rewritten during initial adoption.
- MECH and MechEdit collection YAML regenerate their literature README and
  BibTeX deterministically.
- `mechtools` retains its MECH-specific workflows while delegating generic
  library behavior.
- Browse and common search remain interactive at the current library scale.

For the Stage 1 repository gate, a lossless disposable migration rehearsal,
public operation parity, and a tested configuration transition are sufficient.
Production cutover, broad delegation of the legacy mechtools command surface,
and migration of the live manuscript collections require separate review and
authorization after the core repository is accepted.

## 18. Validation against unrelated workflows

The design is not considered general merely because it avoids MECH field names.
Before v1, it must pass these scenario tests:

1. **Physics dissertation:** papers and arXiv versions, chapter collections,
   equation/page locators, and a temporary reading list.
2. **Standards review:** standards with revisions, technical reports, stable
   URLs, restricted local PDFs, and section-level assessments.
3. **Dataset construction:** articles plus dataset archives and supplements,
   extraction provenance, and occurrence import from source records.
4. **Historical scholarship:** books, chapters, scans requiring OCR, local-only
   identifiers, and page-image review.
5. **Short-lived exploration:** create a collection, acquire sources, export a
   snapshot, remove the project, and preview reclaimable storage.

If a scenario requires a core field specific to one discipline, the model must
be revised or the field moved into a documented extension.

## 19. Deferred after v1

- cloud synchronization and hosted accounts;
- networked multi-user collaboration;
- custom PDF annotation;
- Word, Google Docs, and browser extensions;
- citation-style editing;
- automatic scientific summaries as authoritative artifacts;
- semantic/vector search as an authority;
- autonomous claim adjudication;
- unrestricted plugin execution;
- hosting or sharing restricted publications;
- a general Zotero replacement;
- remote agent execution.

Interchange with Zotero, CSL JSON, BibTeX, and RIS is desirable without making
Zotero or any hosted provider authoritative.

## 20. Decisions and open risks

### Accepted design decisions

- Product name: **LibraryOS**
- Human app name: **Library**
- Category: **local-first, agent-native research library**
- Code repository and local library instances are separate.
- Sources and portable records are authoritative; indexes are rebuildable.
- Collections own contextual meaning and may live with projects.
- Reviews are central, immutable, and source-hash-bound.
- Assessments are project-scoped and may cite reviews and locators.
- Core schemas are domain-neutral and extended through namespaced data.
- Browser-first UI precedes a thin macOS wrapper.
- Mechanism Atlas is the first adapter and migration, not a core dependency.

### Risks to resolve during schema design

1. **Metadata assertion model.** A single flattened metadata record is simple,
   but conflicting provider assertions need enough provenance to audit and
   reconcile.
2. **Work/version identity.** Preprint, accepted manuscript, version of record,
   correction, and retraction relationships need precise examples before schema
   finalization.
3. **Review reuse.** Source-bound review is reusable, but collection policies
   need a deterministic way to state required coverage without implying
   scientific agreement.
4. **External collection writes.** Atomic updates and conflict detection must
   work when a collection lives in another Git repository.
5. **Derivative licensing.** Some source licenses may constrain redistribution
   of extracted text and figures even when metadata is exportable.
6. **Plugin trust.** Provider and converter plugins handle untrusted network
   content and local files; v1 needs a narrow interface even without process
   sandboxing.
7. **Scale semantics.** Attempt histories can grow much faster than work
   manifests; compaction must preserve audit value without making manifests
   unwieldy.
8. **Product-name collision.** Repository ownership is established, but package,
   application, domain, and trademark collisions must be checked before public
   release.

## 21. Implementation gate

Implementation begins only after review confirms:

- the boundary between work, source, derivative, review, and assessment;
- central source reviews plus project-scoped assessments;
- external project ownership of collection manifests;
- browser-first delivery followed by Library.app;
- v1 scope and explicit deferrals;
- the no-relocation-first migration strategy.

After approval, Phase 0 produces actual versioned schema drafts and executable
fixtures. No application scaffolding should precede that contract work.
