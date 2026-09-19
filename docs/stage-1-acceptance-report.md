# LibraryOS Stage 1 acceptance report

Date: 2026-09-19
Candidate version: `0.1.0.dev0`
Decision: **accepted by the project owner on 2026-09-19**

## Outcome

LibraryOS now has a domain-neutral, local-first core with versioned schemas,
ordinary authoritative files, immutable sources, provenance-bound derivatives,
collections, reviews, assessments, durable jobs, safe lifecycle controls,
search, migrations, a Python operation dispatcher, a CLI, and a
token-protected loopback service.

This report establishes implementation and engineering readiness for the
Stage 1 repository/core gate. It does not claim completion of the Stage 2 human
interface or of the full product-v1 acceptance criteria.
It does not certify the scientific contents of any publication, derivative,
collection, or Mechanism Atlas record. Acquisition, preparation, indexing,
search, opening, review, and scientific assessment remain distinct states.

## Automated verification

From the repository root:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
git diff --check
```

Results:

- 128 tests passed;
- Ruff passed;
- whitespace validation passed;
- five PyMuPDF SWIG deprecation warnings were emitted by the installed binary
  bindings; no LibraryOS test failed.

The suite covers schema validation, identity normalization, immutable imports,
acquisition quarantine, metadata conflicts, preparation, bibliography formats,
collections, source-bound reviews, contextual assessments, locators,
occurrences, jobs, migrations, operation contracts, CLI behavior, privacy,
reading resolution, archive/trash/restore/purge, legacy translation, and
domain-neutral acceptance scenarios.

## Generality evidence

Acceptance fixtures exercise:

- a physics preprint with arXiv identity, versions, and equation locators;
- a revised standard with restricted access and section-level assessments;
- a dataset with supplementary material and generic occurrences;
- a historical scan whose OCR explicitly requires source-page verification;
- export, staged removal, and restoration of a short-lived project collection.

Mechanism Atlas semantics are retained only in namespaced legacy data and its
external adapter boundary. The LibraryOS core does not import MECH schemas or
mechtools.

## Preparation and interruption safety

Preparation tests cover conservative HTML/JATS-derived structure and source
anchors, PDF page text and renders, optional OCR boundaries, embedded-image
extraction with a warning that it is not figure segmentation, supplement PDFs,
bounded ZIP extraction, lineage hashes, actual generator versions, and
configuration fingerprints.

A failure-injection test interrupts a two-page PDF after the first page. Retry
resumes only when the job, arguments, source SHA-256, and converter
configuration SHA-256 match, reuses the persisted first-page derivatives, and
completes the second page. Archive-member checkpoints follow the same binding
rules.

Atomic-write failure injection raises immediately before `os.replace`. The old
authoritative file remains complete and readable, the fully flushed candidate
is never exposed as a partial record, and the temporary file is cleaned up.
This proves the bounded pre-replacement failure case; it is not a claim that
Python tests can simulate every kernel, hardware, or filesystem failure.

Concurrent tests cover identity creation, job transition, and compare-and-swap
updates to external project collections. Migration tests cover rollback and
recovery records. Lifecycle tests cover archive, staged trash, restoration,
retention checks, reachability, and separately authorized permanent purge.

## Production compatibility rehearsal

The legacy production library was first audited read-only:

| Measure | Legacy count |
|---|---:|
| Works | 4,060 |
| Sources | 2,294 |
| Derivatives | 13,402 |
| Occurrences | 4,640 |
| Artifact files with verified hashes | 15,696 |
| Attempts | 22,389 |
| Deduplicated exceptions | 30 |

The audit reported zero findings. SQLite `quick_check` returned `ok`, foreign
keys had zero violations, and reconstructable database counts matched ordinary
files.

The complete corpus was then translated into a separate disposable v1 library
at `/private/tmp/libraryos-rehearsal-20260919`:

- 4,060 works became 4,060 v1 works;
- 2,294 sources became 2,294 v1 sources;
- 13,402 derivatives became 13,402 v1 derivatives;
- 4,640 occurrences became 4,640 v1 occurrences;
- the complete 15,696-item artifact-hash multiset matched;
- full v1 validation reported zero findings;
- production mutation was false and no production source byte was moved.

Deterministic UUIDv5 identities make rehearsal repeatable. Unmapped historical
state is retained under `org.libraryos.legacy`; historical acquired/prepared
label discrepancies are preserved rather than rewritten.

The production corpus itself was not migrated. The rehearsal copy is
disposable and currently remains available for inspection and scale tests.

## Realistic-scale measurements

On the rehearsed 4,060-work, 15,696-artifact library on the development Mac:

| Operation | Wall time | Result |
|---|---:|---|
| Rebuild metadata/prepared-text catalog | 5.11 s | completed |
| Metadata search for `mechanism` | 0.08 s | 50 results at the requested limit |
| Prepared-text search for `mechanism` | 0.09 s | 50 results at the requested limit |

These are single local observations, not portable performance guarantees or a
formal benchmark.

## Distribution proof

`python -m build` successfully produced both the source distribution and
`libraryos-0.1.0.dev0-py3-none-any.whl`.

An automated artifact-content check confirmed that the source distribution
contains the operator documentation and repository-owned agent skill, and that
the wheel contains the executable package and all 14 runtime schemas. The
wheel does not silently install or activate the agent skill; agent hosts retain
control of that trust decision.

The rebuilt wheel was installed normally, including dependencies, into a fresh
Python 3.11 virtual environment. From that installed artifact:

- package version was `0.1.0.dev0`;
- all 14 packaged JSON schemas were present;
- all 60 public operations were importable;
- `library.initialize` succeeded through the shared operation dispatcher;
- the console script created a report work, rebuilt the catalog, found the work
  in search with `scientific_support: not_assessed`, and validated the library
  with zero findings and hash verification enabled.

An additional attempt with the system Python 3.9.6 was correctly rejected by
the declared `Python >=3.11` requirement.

## Mechtools transition

After the production rehearsal passed, mechtools was narrowly updated so an
explicit CLI `--library` remains authoritative and defaults resolve through:

1. `LIBRARYOS_ROOT`;
2. deprecated `MECHTOOLS_LITERATURE_LIBRARY`, with a warning;
3. temporary existing sibling discovery.

The focused transition tests and the complete 62-test mechtools CLI suite pass.
MECH-specific inventory, occurrence, and evidence behavior remains in
mechtools. No broad adapter rewrite or production cutover was performed.

## Privacy, security, and recovery

- Library initialization refuses Git worktrees and uses owner-only defaults.
- Restricted source bytes remain in private library instances, outside this
  repository and exports.
- Persisted acquisition URLs exclude user information, query strings, and
  fragments.
- Ambiguous acquired bytes remain immutable in quarantine.
- Root confinement rejects absolute paths, traversal, backslash ambiguity, root
  symlinks, and symlink escapes.
- The HTTP service binds only to loopback and requires a generated bearer
  token.
- Disposable indexes can be rebuilt from authoritative files.
- Backup, restore, interrupted-job reconciliation, and staged-removal recovery
  are documented.
- Privacy tests scan records, diagnostics, logs, exports, and build products.
- The final repository scan covered 85 source files with zero findings. Extracted
  source-distribution and wheel scans covered 85 and 43 files respectively,
  with zero findings; the source-distribution scan skipped two non-text
  packaging metadata files rather than interpreting their bytes as text.

## Known limitations and deferred work

- LibraryOS remains pre-release; schema compatibility is not yet promised
  across arbitrary development snapshots.
- The graphical **Library** interface is Stage 2 and has not begun.
- Human-workflow acceptance—including visual browsing, filtering, reading,
  queues, and lifecycle controls without the CLI—cannot pass until Stage 2.
- GROBID remains an optional adapter, not a core v1 converter.
- JATS handling is intentionally conservative rather than a claim of complete
  preservation of every publisher-specific construct.
- Embedded PDF images are navigation aids, not semantic figure or panel
  segmentation; page renders remain the visual fallback.
- OCR depends on an available Tesseract executable and its output remains
  quality-labeled derivative text.
- Search results are capped by the requested limit and never imply relevance,
  review, or scientific support.
- The sibling and deprecated mechtools configuration fallbacks remain until an
  explicitly authorized production cutover.
- Broad delegation of legacy `mechtools literature` commands and migration of
  the live MECH/MechEdit project collections remain integration work after the
  repository/core gate; the current proof is a lossless disposable rehearsal,
  public LibraryOS operation coverage, and a tested configuration transition.
- A package release, production migration, and deletion of the disposable
  rehearsal library all remain separate actions requiring user direction.

## Acceptance gate

The project owner accepted this report on 2026-09-19. Stage 1 is closed.
Stage 2 UI planning may begin; this acceptance does not authorize a package
release or production-library migration.
