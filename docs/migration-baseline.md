# Legacy migration baseline

Date: 2026-09-19

LibraryOS's generic legacy reader was run against the first production
evidence-bundle corpus without importing a domain package and without changing
source, derivative, manifest, inventory, or SQLite sidecar mtimes.

The private corpus itself is not part of this repository. The reproducible
structural baseline is:

| Measure | Count |
|---|---:|
| Work manifests | 4,060 |
| Manifest-declared files verified | 15,696 |
| Citation occurrences | 4,640 |
| Sources | 2,294 |
| Derivatives | 13,402 |
| Attempts | 22,389 |
| Deduplicated exceptions | 30 |
| Dataset entries represented | 1,241 |

SQLite `quick_check` returned `ok`, foreign-key violations were zero, and all
six reconstructable table counts matched the ordinary-file records exactly.
Every declared file size and SHA-256 matched. This establishes read-only
compatibility only; it does not claim that scientific contents were inspected.

The corresponding write-path rehearsal created a separate v1 library at
`/private/tmp/libraryos-rehearsal-20260919`. It translated 4,060 works, 2,294
sources, 13,402 derivatives, and 4,640 occurrences. All 15,696 artifact hashes
matched as a multiset and full v1 validation reported zero findings. Source
bytes and mtimes in the production library remained unchanged; no production
file was moved or rewritten.

The corpus reports 544 works whose terminal legacy acquisition label is
`acquired` and 671 whose conversion label is `prepared`. The apparent
discrepancy consists of 127 prepared works that all have registered source and
derivative files, but whose acquisition label was later overwritten by another
attempt: 101 are labeled `failed` and 26 `restricted`.

This is historical state, not missing evidence. Migration therefore derives
source availability from immutable source records, retains every attempt and
exception separately, and preserves the legacy terminal label as provenance.
It must not discard sources or rewrite history to make the old aggregate labels
agree.
