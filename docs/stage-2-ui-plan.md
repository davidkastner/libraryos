# Stage 2 plan: the Library human application

Status: **accepted direction; implementation in progress**
Branch: `stage-2-ui-plan`
Last updated: **2026-09-19**

This document turns the human-application requirements in the
[product specification](product-specification.md) into an implementation-ready
plan. The owner accepted implementation on 2026-09-19 with a narrower initial
experience: paper browsing, flexible search, and opening local PDFs in macOS
Preview. The embedded reader described below is deferred unless later use shows
that it is necessary.

## 1. Outcome

Stage 2 delivers **Library**, a local, desktop-first research interface over the
same versioned LibraryOS operations used by agents, Python, and the CLI.

A researcher must be able to find a work, understand what material is
available, open the best representation, inspect source-linked content, record
exact review coverage, express project-specific judgments, operate collection
queues, and understand failures without opening a terminal.

The interface must remain useful for a general research library. Mechanism
Atlas is a scale and workflow test, not a visual theme or data-model assumption.

### 1.1 Success statement

At acceptance, a new user can open a 4,000-work library and answer, without
training:

- What is in this library?
- Do I have a trustworthy source for this work?
- What can I read or inspect now?
- What has actually been reviewed, by whom, and over which exact source?
- Why is this work relevant to a particular project?
- What needs attention next?
- What will an archive, removal, or purge operation change?

### 1.2 Non-goals

Stage 2 does not deliver:

- a hosted or multi-user service;
- a mobile-first application;
- a custom PDF annotation engine;
- semantic search represented as scientific authority;
- automatic scientific adjudication or authoritative summaries;
- citation-style editing comparable to a dedicated reference manager;
- unrestricted browser access to arbitrary local files;
- a native macOS wrapper before the browser application is stable;
- direct browser access to SQLite or filesystem traversal;
- a visual workflow that cannot also be expressed through shared operations.

## 2. Experience principles

1. **Research object first.** The primary unit is a scholarly work, not a file,
   database row, or ingestion event.
2. **One calm surface, progressive depth.** Common reading tasks remain simple;
   provenance, lineage, attempts, and maintenance details are available without
   dominating every screen.
3. **State is named honestly.** Acquired, prepared, opened, reviewed, and
   assessed are visibly different states.
4. **The source remains authoritative.** Prepared text accelerates navigation;
   source pages and source files remain the fallback for visual and scientific
   inspection.
5. **Project meaning stays contextual.** Relevance, disposition, and judgment
   are shown in their collection context, never promoted to global truth.
6. **Every result has a route forward.** Empty, unavailable, failed, and
   warning states explain the next bounded action.
7. **Destructive effects are concrete.** The interface names records, files,
   bytes, references, recovery deadlines, and irreversibility before acting.
8. **Keyboard and pointer are peers.** Browsing, reading, queue triage, and
   collection membership are efficient with either.
9. **No hidden semantic fork.** UI actions invoke published LibraryOS
   operations. UI-specific queries are added to that operation layer.

## 3. Users and primary jobs

### Researcher

- browse, filter, and search the whole library;
- read the best available representation;
- compare extracted material with the source;
- collect and order works for a purpose;
- record honest review coverage and contextual assessments;
- resolve preparation, recovery, and review queues;
- export an overview or bibliography.

### Library maintainer

- understand library health and storage;
- inspect acquisitions, preparations, jobs, warnings, and quarantine;
- validate and rebuild derived state;
- archive, restore, stage removal, and deliberately purge.

### Agent operator

- see what capabilities a session or action requires;
- inspect the same operation results an agent receives;
- diagnose failures without exposing source contents in logs;
- trust that UI state does not exist only inside the frontend.

Stage 2 is single-user. These are modes of work, not accounts or permission
roles.

## 4. Information architecture

The application has three persistent regions:

```text
┌──────────────┬──────────────────────────────────────┬───────────────┐
│ Navigation   │ Main workspace                       │ Context panel │
│              │                                      │ (when useful) │
│ Library      │ list · detail · reader · collection  │ filters       │
│ Collections  │                                      │ outline       │
│ Queues       │                                      │ job detail    │
│ Exceptions   │                                      │ provenance    │
└──────────────┴──────────────────────────────────────┴───────────────┘
```

The left navigation is stable. The context panel appears only when it preserves
the user's place: filters beside a result list, outline beside a reader, and
details beside a queue or job. It must not become a permanent inspector full of
low-value metadata.

### 4.1 Primary navigation

- **Library** — all works and unified search
- **Collections** — active and archived project contexts
- **Reading Queue** — collection-aware works selected for reading
- **Recently Added** — recently created works and imported sources
- **Needs Source** — identified works without an inspectable source
- **Needs Preparation** — sources lacking requested useful derivatives
- **Needs Review** — collection policy or member state requiring review
- **Exceptions** — actionable failures, warnings, and quarantined acquisitions

### 4.2 Secondary navigation

Accessible from the library switcher/status area:

- Jobs
- Storage and recovery
- Validation and rebuild
- Settings
- About and operation contracts

Queue navigation preserves collection scope when entered from a collection and
shows that scope prominently. A global queue may aggregate several collections
but may not erase their differing purposes.

### 4.3 URL model

Stable, bookmarkable routes:

```text
/library
/works/:workId
/works/:workId/read/:artifactId?
/collections
/collections/:collectionId
/queues/:queueKind?collection=:collectionId
/exceptions
/jobs
/jobs/:jobId
/maintenance
```

Search, sort, filters, selected tab, and reader locator use URL state where
reasonable. Transient dialogs, draft form contents, and credentials never do.

## 5. Screen specifications

### 5.1 Application shell and first run

The header shows the library title, current health, background activity, global
search entry, and a compact command menu. A disconnected service, missing
library, schema incompatibility, or validation problem has its own explicit
state; the app never resembles an empty healthy library in those cases.

First run selects or initializes a library directory, explains that the library
is private and separate from the code repository, and confirms the server is
localhost-only. It does not silently create a library in the repository.

### 5.2 Library browse

The default is a compact, readable list rather than cards. Each result shows:

- title, primary authors, year, venue, and work type;
- source availability and identity state;
- text, page/visual, and supplement preparation state;
- review coverage, stated as coverage rather than truth;
- collection membership;
- warnings and unresolved exceptions.

Columns may be configured, but title and state remain visible. Sorting and
filtering are server-backed and deterministic. The list supports pagination or
cursor-based virtual loading and must not load thousands of complete manifests
into browser memory.

Bulk selection is limited initially to adding works to a collection and
starting safe, previewable preparation actions. Destructive bulk action is
deferred until its effect model is independently accepted.

### 5.3 Unified search

One search field covers verified metadata and prepared text. Results visually
distinguish:

- **Metadata match** — matched identity or bibliographic fields
- **Prepared-text match** — bounded context, artifact, and source locator

Every result carries the invariant `scientific_support: not_assessed`. The UI
renders this through explanatory language such as “Search match; support not
assessed,” never as a badge suggesting evidence.

Filters include type, year range, identifier scheme, source availability,
preparation state, review coverage, collection, tag, warning, and exception
state. Active filters are readable as a sentence and removable individually.
No-results state separates “nothing matched” from “prepared-text index is not
available.”

### 5.4 Work detail

The header contains identity, citation summary, state strip, collection
membership, and primary **Read** action. Content is divided into:

1. Overview
2. Sources
3. Prepared representations
4. Reviews
5. Project use
6. Relations and versions
7. History and exceptions

The overview answers what the work is and what can be done next. Technical
lineage and acquisition attempts live in their relevant tabs. Source rows show
identity verification method, provenance, media type, version label, hash,
access class, and restrictions. Derivative rows show input hash, generator,
quality, warnings, and available navigation modes.

**Read** invokes deterministic resolution. The interface reports the chosen
mode: local PDF, source-faithful text, local structured source, stable source
URL, or unavailable. Opening never changes review state. When unavailable,
Discover and Import are offered according to capability.

### 5.5 Reading action

The initial interface does not embed a PDF viewer. **Open in Preview** resolves
the preferred authoritative local PDF through LibraryOS and asks macOS to open
it in Preview. No local path is exposed to the browser. Opening never creates a
review or claims that scientific inspection occurred.

If no local PDF is available, the action is disabled and the interface states
whether another source exists or source acquisition is needed.

### 5.6 Deferred integrated reader

If later workflows demonstrate a need for source-linked in-app inspection, a
reader may add three modes:

- **Prepared** — structured text and bounded artifacts with source locators
- **Source pages** — rendered pages as visual authority
- **Compare** — derivative and corresponding source page side by side

An outline navigates sections, pages, figures, tables, and supplements when
available. Current location can be copied as a stable locator. Extraction
warnings stay visible at the affected item and in a reader-level summary.

The reader records navigation locally for session continuity but does not
persist “read,” “reviewed,” or assessment state automatically. Opening a system
PDF viewer is a deliberate action and leaves the review state unchanged.

The review composer is entered explicitly. The user selects exact source
hashes, coverage locators, purpose, reviewer, method, observations, and
limitations. Before writing, the UI states that the record is immutable and
will be bound to those source bytes. Editing an existing review creates a
superseding record.

The assessment composer requires a collection context. It records purpose,
subject, judgment, summary, observations, limitations, locators, and optionally
linked reviews. It never presents an assessment as a replacement for source
review.

### 5.7 Collections index and workspace

The index separates active and archived collections and shows purpose,
membership count, queue counts, last update, and external/local ownership.

The workspace contains:

- **Members** — ordered works with priority, disposition, tags, citation key,
  notes, assessment state, and queue reasons
- **Queues** — reading, source recovery, preparation, review, and exception
  views with explicit inclusion reasons
- **Assessments** — collection-scoped judgments and supersession history
- **Outputs** — previews and generation state for overview and bibliography
- **Policy and validation** — collection requirements and findings
- **Lifecycle** — archive, remove, restore, and purge entry points

Reordering is keyboard accessible and commits an explicit complete order.
External collections show their path, validated hash, and conflict state.
Out-of-band changes trigger revalidation or conflict resolution; the app never
silently overwrites them.

### 5.8 Queues

Queues are explainable projections, not hidden task lists. Every row answers:
“Why is this here?” and “What action removes it?”

- Reading: member disposition or explicit collection state
- Needs Source: no acceptable inspectable source
- Needs Preparation: source exists but requested derivative does not
- Needs Review: collection policy or member requirement is unmet
- Exceptions: unresolved warning, failed operation, or quarantine record

Actions execute immediately only when short and atomic. Acquisition,
preparation, rebuild, and other resumable work create or expose jobs.

### 5.9 Jobs and exceptions

The activity indicator summarizes queued, running, failed, and interrupted
jobs. The jobs screen exposes operation, initiator, capabilities, timestamps,
attempt history, checkpoint, bounded arguments, exceptions, and result. Source
content and sensitive URL state never appear in logs.

Running jobs may be cancelled when supported. Failed, cancelled, and
interrupted jobs expose Retry only when the operation is resumable and the
required capability is present.

The Exceptions screen groups actionable problems by category and work, with
links to provenance and affected artifacts. Quarantine detail shows safe
metadata and reason but never renders untrusted bytes inline. Rejected bytes
may be revealed in the filesystem only through an explicit safe action.

### 5.10 Maintenance and lifecycle

Maintenance reports descriptor/schema state, index state, storage by class,
validation findings, trash retention, and quarantine counts. Validate is
read-only; rebuild replaces only explicitly rebuildable state.

Archive preview names the collection snapshot and confirms that shared works,
sources, reviews, and assessments are not deleted. Archive is reversible.

Removal and purge are distinct:

1. Preview exact collection-local records and generated outputs affected.
2. Stage recoverably and show transaction ID plus recovery deadline.
3. Permit restore while retained.
4. Permanently purge only with `destructive.purge`, a second warning, and exact
   transaction-ID entry.

The confirmation text states “permanently delete” and “cannot be recovered.”
Color alone never communicates danger.

## 6. Scientific-state language

The following labels are reserved:

| State | Meaning in the interface | Must not imply |
|---|---|---|
| Identified | Work identity is represented | A source was acquired |
| Acquired | Source bytes are attached with provenance | Content was prepared or read |
| Prepared | A derivative was generated | Extraction is complete or inspected |
| Opened | A representation was launched in this session | Review occurred |
| Reviewed | An immutable review covers named source hashes and locators | Agreement or project relevance |
| Assessed | A purpose-specific judgment exists in a collection | Global scientific truth |
| Occurs in | A structural reference points to the work | Citation support |
| Search match | Metadata or prepared text matched a query | Relevance or support |

“Verified” is always qualified: verified identity, verified file hash, or
verified schema. The unqualified phrases “verified paper” and “verified
evidence” are not used.

## 7. Visual system

The desired character is editorial, quiet, precise, and dense enough for daily
research. It should resemble a well-designed reading room more than an admin
console.

### 7.1 Foundations

- Neutral, warm-gray canvas with near-white reading surfaces
- Deep ink text; restrained blue for navigation and actions
- Green reserved for successful system state, not scientific validity
- Amber for limitations or attention; red for failure and destructive action
- One sans-serif UI family and an optional highly legible serif for long-form
  reading; system fallbacks prevent font loading from blocking the app
- Four-pixel spacing unit with compact and comfortable density settings
- Moderate corner radius, hairline borders, and almost no decorative shadow
- Tabular numerals for years, counts, hashes, pages, and job data

Exact tokens are chosen during the visual prototype and committed as CSS custom
properties. Components consume semantic tokens, never raw status colors.

### 7.2 Interaction

Icons supplement text and use one coherent outlined set. Motion is short and
functional: panel transitions, progress changes, and focus restoration. It
honors `prefers-reduced-motion`. Loading uses stable skeleton geometry; content
must not jump when counts or status arrive.

Light mode ships first, but tokens must support dark mode without semantic
changes. Dark mode is part of Stage 2 only if it does not delay core workflow
acceptance.

## 8. Responsive behavior and accessibility

The primary target is a laptop or larger display. At narrower widths, the
context panel becomes a drawer and navigation collapses; the reader switches
from comparison to a deliberate Prepared/Source toggle. Mobile supports lookup
and reading but not every maintenance workflow.

Acceptance targets WCAG 2.2 AA:

- complete keyboard navigation with visible focus;
- semantic headings, landmarks, tables, and form labels;
- skip links and predictable focus after navigation or dialog closure;
- no color-only status communication;
- minimum target sizes and readable contrast;
- accessible sortable tables and reorder controls;
- announced job/status updates without noisy live regions;
- source pages retain useful alternate descriptions and page labels where
  known.

Global shortcuts are discoverable and avoid browser/assistive-technology
conflicts. Initial set: search, command menu, return to list, next/previous work
or result, copy locator, and open Read.

## 9. Application states

- **Loading:** preserve layout and show the object being requested.
- **Empty:** explain whether the library, collection, queue, or result is empty
  and provide one valid next action.
- **Unavailable:** show why no read representation resolved and the bounded
  Discover/Import actions.
- **Partial:** display available data plus warnings; do not hide a work because
  one derivative failed.
- **Stale/conflict:** show expected and current external collection hashes and
  require revalidation before writing.
- **Unauthorized:** identify the missing capability without offering an action
  that cannot succeed.
- **Disconnected:** retain safe navigation context, disable mutations, and
  reconnect deliberately; no offline writes in v1.
- **Failed:** preserve useful context, error code, retry eligibility, and route
  to related job or exception.

## 10. Shared operation contract

The frontend calls `/v1/operations/<name>` through a generated typed client. It
does not query SQLite, infer manifest paths, or walk a library directory.
Operation results remain canonical envelopes with effect and capability
metadata.

### 10.1 Existing operation map

| Interface behavior | Existing operation | Capability |
|---|---|---|
| Library identity/status | `library.status` | `library.read` |
| Basic work list/detail | `work.list`, `work.show` | `library.read` |
| Metadata/full-text search | `search`, `search.prepared` | `library.read` |
| Choose reading representation | `read.resolve` | `library.read` |
| Collections | `collection.list/get/queues` | library or collection read |
| Collection changes | `collection.create/put`, external variants | maintain or collection write |
| Review history/write | `review.list`, `review.write` | library read / review write |
| Assessment history/write | `assessment.list/import/write` | library read / collection assessment write |
| Occurrences | `occurrence.list` | `library.read` |
| Source import/acquisition/preparation | source operations | source acquire / derivative prepare |
| Jobs | `job.list/get/cancel/retry` | `library.maintain` |
| Archive | `archive.preview/apply/restore` | collection read/write |
| Removal and purge | `purge.preview/stage/restore/confirm` | `destructive.purge` |
| Validation/rebuild | `library.validate/rebuild` | `library.maintain` |

The existing calls are semantically valid but several are whole-library record
lists. They are not sufficient as the final screen API.

### 10.2 Required UI-supporting operations

These operations must be designed as public, client-neutral LibraryOS
operations before their screens depend on them:

| Proposed operation | Purpose | Capability |
|---|---|---|
| `library.summary` | Counts, health, storage classes, active job summary | `library.read` with sensitive maintenance fields omitted; full detail for maintain |
| `work.query` | Cursor pagination, deterministic sort, filters, bounded enriched rows | `library.read`, plus collection-scoped form |
| `work.overview` | One bounded detail aggregate: work, sources, derivatives, relation summaries, review coverage, collection uses, warnings | `library.read`, scope-aware |
| `search.query` | Unified paginated metadata/prepared search with typed hits and facets | `library.read`, scope-aware |
| `collection.overview` | Enriched member rows, counts, policy state, outputs, and ownership | `collection.read:<id>` |
| `queue.query` | Paginated queue projection with inclusion reasons and actions | appropriate library/collection read |
| `review.query` | Filter by work/source/reviewer/purpose without disclosing unrelated scoped records | `library.read` or scoped read |
| `assessment.query` | Filter by collection/subject/purpose with scoped authorization | `assessment.write:<id>` or collection read |
| `exception.query` | Aggregate manifest warnings, job exceptions, validation findings, and quarantine summaries | `library.maintain` |
| `quarantine.list/show` | Safe metadata only; never bytes inline | `library.maintain` |
| `trash.list/show` | Staged transaction state and recovery deadline | `destructive.purge` |
| `artifact.describe` | Authorized media metadata, hierarchy, locators, and safe navigation targets | scope-aware read |
| `artifact.open` | Short-lived opaque URL/handle for an authorized manifest-declared artifact | scope-aware read |
| `reader.document` | Structured blocks and source links for bounded reader navigation | scope-aware read |
| `reader.page` | One safely rendered page/image and page metadata | scope-aware read |
| `activity.since` | Bounded cursor feed for job and state changes | capability-filtered |
| `capability.describe` | Effective session capabilities for conditional controls | session-local |

Names are provisional; semantics are not. Every query defines stable ordering,
cursor behavior, bounds, empty behavior, authorization filtering, and result
schema. Collection-scoped sessions must not infer the existence of unrelated
works from counts, facets, errors, or timing-sensitive lookup behavior.

### 10.3 Safe artifact delivery

Binary content is not returned inside operation JSON. The service issues a
short-lived, unguessable handle after authorization and serves only a
manifest-declared path resolved beneath the configured library root.

Artifact responses set an allow-listed media type, `nosniff`, restrictive CSP,
private/no-store caching, and disposition appropriate to view versus download.
HTML and active content are downloaded or sandboxed, never trusted in the app
origin. SVG is treated as active content. Range requests may be supported for
PDFs after path and authorization tests exist. No absolute local path crosses
the browser boundary.

### 10.4 Progress model

V1 uses bounded polling through `activity.since` and `job.get`; WebSockets are
not required. This keeps the protocol small while allowing responsive jobs and
queue counts. The cursor feed must be lossy-safe: after expiry, the client
refreshes affected queries rather than assuming events are authoritative.

## 11. Frontend architecture

The first browse/search slice uses semantic HTML, CSS, and a small JavaScript
client shipped directly in the Python package. This keeps the executable
surface minimal while the product interaction is validated. If later
collection, review, and queue workflows make a component framework worthwhile,
the recommended growth path is:

- Vite, React, and strict TypeScript
- React Router for routes and URL state
- TanStack Query for server state, invalidation, polling, and mutation state
- a small headless accessibility foundation (Radix primitives or React Aria,
  selected after prototype comparison)
- CSS Modules plus semantic CSS custom properties
- generated TypeScript request/result types from checked-in operation schemas
- Vitest and Testing Library for unit/component tests
- Playwright for end-to-end and accessibility journeys
- Storybook only if it demonstrably improves isolated state and visual review;
  it is not a prerequisite

Avoid a heavy visual component framework. Library needs an editorial identity,
high-density tables, and specialized reader states that generic dashboard
components tend to fight.

Proposed repository shape:

```text
ui/
├── src/
│   ├── app/          # shell, routing, providers
│   ├── api/          # generated contracts and client
│   ├── features/     # works, reader, collections, queues, maintenance
│   ├── components/   # reusable semantic UI
│   ├── styles/       # tokens and global foundations
│   └── test/
├── e2e/
├── package.json
└── vite.config.ts
```

Feature code may map canonical operation results into view models but may not
invent durable state. Draft reviews and assessments may be kept in browser
memory or session storage; successful writes return canonical records.

The Python package serves built static assets in production. Development uses
the Vite server with an explicit localhost API origin and session token.
Packaging must fail if the expected built UI is absent; wheels must not silently
ship a broken shell.

## 12. Security and privacy

- The app and API bind to localhost and use an unguessable per-session token.
- The token stays in memory and is never placed in URLs, local storage, logs,
  screenshots, or error reports.
- Publication content is not written to browser logs or analytics.
- Telemetry is absent by default.
- Rendering treats source and derivative content as untrusted.
- Markdown or extracted HTML is parsed into an allow-listed representation;
  arbitrary script and style are never injected.
- External navigation is explicit and prevents opener access.
- UI authorization is convenience only; the server enforces every capability.
- Restricted bytes remain outside Git, default exports, and frontend bundles.
- Crash/error displays redact transient queries, credentials, and signed URLs.

The server needs a browser-specific CSP rather than the current API response
policy of `default-src 'none'`. API JSON remains locked down; app routes receive
a separately reviewed policy.

## 13. Testing and evidence

### Contract

- generated frontend types remain synchronized with operation schemas;
- Python, CLI, HTTP, and UI invoke the same operation semantics;
- scope and capability tests cover counts, facets, failures, and artifact
  handles—not only successful detail calls;
- path traversal, symlink escape, active-content, and expired-handle tests pass.

### Unit and component

- state vocabulary and status mappings;
- result/queue explanation;
- review and assessment validation;
- lifecycle impact summaries;
- all empty, partial, stale, unauthorized, and failed variants.

### End to end

1. Find a work, resolve Read, inspect prepared text and a source page, copy a
   locator, and confirm no review was created.
2. Write a source-hash-bound review with explicit coverage.
3. Add the work to two collections and create different assessments.
4. Triage Needs Source and Needs Preparation through visible jobs.
5. Reconcile an external collection conflict without overwriting it.
6. Preview archive, archive, and restore.
7. Preview removal, stage it, restore it, then separately test exact-ID purge
   on disposable fixtures.
8. Operate with a collection-scoped token and prove unrelated works are absent.

### Accessibility and visual quality

- automated axe checks plus manual keyboard and screen-reader smoke tests;
- contrast, zoom to 200%, reduced motion, and narrow-window checks;
- visual regression for core screens and every consequential state;
- source-page and prepared-text comparison at realistic scientific complexity.

### Scale and performance

Use a synthetic, redistributable library shaped like the 4,060-work rehearsal,
without private metadata or publication bytes. Define budgets during the first
vertical slice and measure them in CI or a repeatable benchmark:

- initial shell and first useful browse results;
- filter/sort response;
- search response and incremental rendering;
- work overview;
- reader page transition;
- queue aggregation.

The target experience is interactive on a typical research laptop. Acceptance
uses measured percentiles and fixture details, not “feels fast.”

## 14. Delivery sequence

### 2A — Contract and visual prototype

- accept this plan and the scientific-state vocabulary;
- define schemas for query, aggregate, artifact, and activity operations;
- create low-fidelity flows for browse, detail, reader, collection, and purge;
- create one high-fidelity visual direction with real scientific fixture data;
- decide the headless component foundation;
- set measured performance budgets.

Gate: owner accepts information architecture, visual direction, and new public
contracts.

### 2B — Shared read foundation

- implement query pagination/filtering and bounded aggregates;
- implement safe artifact/page delivery;
- implement scoped authorization and leak tests;
- generate the TypeScript client;
- serve a minimal production UI shell from `libraryos serve`.

Gate: one work can be found and read end-to-end without direct filesystem or
SQLite access.

### 2C — Core research workflow

- Library browse and unified search;
- work detail and deterministic Read;
- prepared/source/compare reader;
- explicit review and assessment creation;
- collection workspace, ordering, queues, and outputs.

Gate: the primary human acceptance journeys pass on realistic fixtures.

### 2D — Operations and lifecycle

- global queues, jobs, exceptions, quarantine, and maintenance;
- archive/restore and staged removal/purge;
- accessibility, visual regression, scale, privacy, and security hardening.

Gate: all Stage 2 acceptance criteria pass and limitations are documented.

### 2E — Browser stabilization

- run sustained use against a disposable copy of the Mechanism Atlas library;
- categorize discrepancies and usability failures;
- complete documentation and install/build checks.

Gate: browser UI is accepted as stable enough to design the thin macOS wrapper.
Native packaging remains a separate authorization.

## 15. Stage 2 acceptance criteria

Stage 2 is complete only when:

- all primary and secondary screens work without CLI intervention;
- the UI consumes only public LibraryOS operations and authorized artifact
  handles;
- a collection-scoped session cannot discover unrelated library state;
- search clearly distinguishes navigation matches from scientific assessment;
- opening or navigating a source never creates a review;
- reviews bind exact source hashes and coverage; assessments require collection
  context;
- warnings, limitations, derivative lineage, and source-page fallback remain
  visible;
- jobs, queues, exceptions, quarantine, and recovery state are understandable;
- archive is reversible and permanent purge requires separate authority plus
  exact transaction-ID confirmation;
- keyboard, screen-reader smoke, contrast, zoom, and reduced-motion checks pass;
- security and privacy tests cover content delivery as well as JSON operations;
- browse, search, detail, reader, and queues meet accepted measured budgets at
  realistic scale;
- the Python test suite, frontend suite, contract checks, and production wheel
  build all pass;
- no private library contents or restricted bytes enter Git or build artifacts;
- a disposable Mechanism Atlas rehearsal completes without mutating the live
  library.

## 16. Decisions for owner review

The plan recommends the following decisions:

1. Keep the product navigation listed here, but group the four task queues under
   a single collapsible **Queues** heading when screen width is constrained.
2. Make the dense list the default library view; do not ship a card-grid default.
3. Use a three-mode reader—Prepared, Source pages, Compare—with system PDF open
   as a secondary action.
4. Keep review and assessment as separate explicit composers.
5. Keep the first slice dependency-light; reconsider Vite + React + TypeScript
   when more stateful workflows are authorized.
6. Add screen-oriented query operations to the shared LibraryOS layer before
   building feature screens.
7. Use bounded polling rather than WebSockets for v1.
8. Ship light mode first while designing semantic tokens for dark mode.
9. Stabilize the browser UI before authorizing a native macOS wrapper.

Acceptance of this plan authorizes Phase 2A, not the entire UI implementation or
native packaging.
