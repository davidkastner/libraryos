# `mechtools literature` compatibility contract

This document separates reusable research-library behavior from
Mechanism Atlas behavior before either repository delegates production calls.
LibraryOS does not import `mechtools`, MECH schemas, or a private corpus.

## Capability map

| Existing `mechtools literature` behavior | LibraryOS operation or contract | Compatibility action |
|---|---|---|
| `init_library`, safe roots, atomic files, locks | `initialize_library`; storage primitives | Delegate generic initialization after an explicit migration gate. |
| DOI normalization and DOI-keyed bundles | Generic identifier normalization; UUID work identity | Resolve DOI to a LibraryOS work ID. Do not preserve DOI as physical identity. |
| `resolve_metadata` / Crossref | `metadata.crossref.resolve`, `metadata.assertion.accept` | Delegate; LibraryOS deliberately separates assertion from acceptance. |
| `discover_sources` | `source.crossref.discover`, provider contract | Delegate supported providers; port additional generic providers independently. |
| `register_source`, `import_source`, acquisition | `source.candidate.register`, `source.import`, `source.acquire` | Delegate after manifest/path parity tests. |
| structured HTML/JATS preparation | `source.prepare` | Delegate. LibraryOS preserves conservative text plus source anchors and fingerprints. |
| PDF text, page renders, OCR, embedded images | `source.prepare` | Delegate. Outputs remain navigation derivatives and never imply review. |
| supplement PDF/archive preparation | `source.prepare` on a source whose role is `supplement` | Delegate. Supplement bytes remain immutable sources; extracted files are derivatives. |
| GROBID conversion | provider/converter extension, not yet a core v1 route | Keep optional adapter until a bounded, provenance-complete LibraryOS converter exists. |
| `rebuild_index`, status, work display | `library.rebuild`, `library.status`, `work.show`, `read.resolve` | Delegate; consumers must not query either SQLite schema directly. |
| prepared-text search | `search.prepared` | Delegate; search results have `scientific_support: not_assessed`. |
| attempts, exceptions, resumable processing | durable job operations | Delegate generic execution; translate legacy statuses at the adapter boundary. |
| `validate_library` | `library.validate` plus `legacy.audit` during transition | Run both until the disposable migration comparison passes. |
| exception export/resolution | job exceptions and collection queues | Port only generic exception disposition that is not represented by existing jobs. |
| `add_work`, `process_library` orchestration | operation dispatcher and durable jobs | Implement as client orchestration, not a new monolithic core operation. |

## Behavior that remains in `mechtools`

- scanning MECH datasets and parsing their citation objects;
- mapping citations to entries, mechanisms, steps, changes, and attachments;
- `inventory_corpus`, `sync_inventory_entry`, and `entry_context`;
- MECH-specific citation classifications and evidence handoffs;
- interpretation of `org.mechanismatlas.*` extension data.

Those behaviors may call LibraryOS using its public Python operation dispatcher.
They must not import LibraryOS implementation modules or query its rebuildable
SQLite catalog.

## Configuration transition

The disposable compatibility rehearsal passed. `mechtools` now resolves its
literature-library default in this order:

1. explicit command argument;
2. `LIBRARYOS_ROOT`;
3. temporary fallback to `MECHTOOLS_LITERATURE_LIBRARY` with a deprecation
   warning;
4. temporary sibling discovery of `manuscript-library`.

The final fallback is retained so existing local workflows do not break before
the production cutover. Both legacy configuration paths can be removed after
that cutover is explicitly authorized. No production source bytes were moved
or rewritten by this transition.

## Compatibility evidence

1. Audit every legacy manifest, file hash, occurrence, attempt, and exception.
2. Rehearse conversion in a disposable directory.
3. Compare pre/post record counts, source hashes, derivative hashes, statuses,
   and categorized discrepancies.
4. Exercise source import, preparation, search, and MECH occurrence sync
   through the adapter.
5. Prove that rollback leaves the production corpus unchanged.

The historical acquired/prepared discrepancy is retained as provenance. It is
not repaired by deleting or relabeling records.

Items 1–3 and 5 passed for the entire production corpus in the disposable
rehearsal. The environment-variable transition and existing legacy CLI behavior
also pass the mechtools CLI suite. Item 4's broad adapter delegation remains a
post-cutover integration task; Stage 1 deliberately does not replace the
MECH-specific legacy module or claim that production is already operating on
the rehearsed v1 copy.
