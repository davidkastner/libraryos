# LibraryOS Stage 1 hardening roadmap

Status: **complete and accepted on 2026-09-19**
Stage 1 gate: **passed**
Stage 2 rule: **UI planning may begin; implementation follows an accepted
Stage 2 design**

This roadmap translates the repository/core portion of the normative
[product specification](product-specification.md) into auditable completion
evidence. A checked item means the behavior exists and has automated or
recorded verification; it does not mean the entire product is complete. The
human-workflow acceptance criteria in section 17 of the specification belong to
Stage 2 and cannot be checked before the Library interface exists.

## Completion definition

Stage 1 is complete only when:

1. every required repository/core capability below is implemented or explicitly
   assigned to a later stage in the product specification;
2. the complete automated suite passes locally and CI is configured for every
   supported Python version;
3. the acceptance fixtures pass without domain-specific core behavior;
4. the legacy Mechanism Atlas corpus passes read-only compatibility and a
   disposable write-path rehearsal;
5. privacy, interruption, concurrency, recovery, and lifecycle tests pass;
6. installation and operator documentation work from a clean environment;
7. an acceptance report records commands, results, limitations, and any
   deferred work;
8. the user reviews and accepts the Stage 1 report.

Commit, push, release publication, and destructive corpus migration remain
separate user-authorized actions.

## A. Architecture and contracts

- [x] Normative, domain-neutral product and architecture specification.
- [x] Packaged versioned schemas for current authoritative record classes.
- [x] Ordinary authoritative files separated from rebuildable runtime state.
- [x] Atomic replacement, locks, root confinement, and traversal defenses.
- [x] Shared operation dispatcher for Python, generic CLI, and localhost API.
- [x] Published operation argument/result schemas and richer OpenAPI output.
- [x] Enforced capability grants, including collection-scoped reads.
- [x] Transactional schema migrations with preview and recovery records.

## B. Identity, discovery, and metadata

- [x] Domain-neutral work types and identifier schemes.
- [x] Identity-gated acquisition and durable verified source candidates.
- [x] Secret-free URL persistence and quarantining of ambiguous bytes.
- [x] Deterministic identifier normalization and uniqueness enforcement.
- [x] Identity-resolution and discovery-provider interfaces.
- [x] Deterministic metadata conflict handling and explicit acceptance.
- [x] BibTeX, CSL JSON, RIS, and generic metadata import.

## C. Sources and preparation

- [x] Immutable sources with hashes, provenance, and deduplication.
- [x] Deterministic text, conservative HTML/XML, and PDF-page preparation.
- [x] Exact derivative lineage, locators, quality, and warnings.
- [x] Acquisition, preparation, opening, review, and assessment stay separate.
- [x] Conservative JATS structure and source anchors.
- [x] PDF text/OCR, figures, and supplements with quality boundaries.
- [x] Converter checkpoints and source/configuration-bound safe resume.
- [x] Actual generator versions and configuration fingerprints.

## D. Collections and scientific state

- [x] Library-owned JSON collections.
- [x] Human-authored JSON/YAML external project collections.
- [x] Authoritative registration with absolute binding and validated hash.
- [x] Compare-and-swap writes detect concurrent project edits.
- [x] Immutable source-bound reviews and collection-scoped assessments.
- [x] Structural occurrences do not imply scientific support.
- [x] Explicit external-edit revalidation.
- [x] External registration relocation operation.
- [x] Canonical JSON, Markdown, BibTeX, CSL JSON, and RIS exports.
- [x] Collection policy validation and reading/recovery queues.
- [x] YAML authoring for project assessments.

## E. Search, jobs, and lifecycle

- [x] Rebuildable metadata catalog and search.
- [x] Prepared-text navigation is distinct from source inspection.
- [x] Deterministic Read resolution with recovery actions.
- [x] Durable jobs, attempts, exceptions, and diagnostics.
- [x] Non-destructive archive and purge previews.
- [x] Preferred-version policy and user preference for Read.
- [x] Checkpoints, cancellation, retry, and restart reconciliation.
- [x] Reversible archive, staged trash, reachability, and authorized purge.

## F. Compatibility and generality

- [x] Existing Mechanism Atlas library audit: 4,060 manifests, 15,696 files,
  zero findings.
- [x] Migration preview preserves historical provenance discrepancies.
- [x] Reimplement the bounded, reusable generic behavior needed for v1 while
  retaining domain-specific orchestration in `mechtools`.
- [x] Disposable migration rehearsal with full record/hash comparison.
- [x] Mechanism Atlas write-path rehearsal without moving production source
  bytes.
- [x] `LIBRARYOS_ROOT` transition after compatibility proof.
- [x] Acceptance fixtures for physics/arXiv, standards/reports,
  datasets/supplements, historical scans/OCR, and short-lived collections.

## G. Distribution, security, and evidence

- [x] License, contribution/security policy, README, package metadata, and CI.
- [x] Release/versioning policy and domain-neutral human/agent examples.
- [x] Repository-owned agent skill with scientific and operational boundaries.
- [x] Token-protected loopback-only HTTP service.
- [x] Restricted publication bytes stay outside Git.
- [x] Clean-environment installation and package-content test.
- [x] CLI parity for every public operation.
- [x] Privacy scanning of records, diagnostics, logs, exports, and builds.
- [x] Concurrency, bounded crash injection, recovery, and realistic-scale
  tests.
- [x] Threat model and operator backup/recovery guide.
- [x] Final Stage 1 acceptance report prepared for user review.

## Current verified baseline

As of 2026-09-19:

- legacy corpus audit: zero findings;
- disposable migration rehearsal: 4,060 works, 15,696 artifact hashes, and
  zero v1 validation findings;
- complete LibraryOS suite: 128 passed;
- source distribution: operator docs and repository-owned skill present;
- installed wheel: 14 schemas, 60 operations, and a successful shared
  initialize/create/rebuild/search/validate workflow;
- repository and extracted artifact privacy scans: zero findings;
- Ruff and `git diff --check`: clean;
- no commit, push, release, or destructive migration has been performed.

The detailed evidence and limitations are in
[the Stage 1 acceptance report](stage-1-acceptance-report.md). Stage 1 remains
closed following the project owner's review and acceptance.
