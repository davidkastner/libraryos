---
name: libraryos
description: Operate a local LibraryOS research library through its versioned API or CLI while preserving provenance, least authority, and the distinction between possession, preparation, review, and scientific assessment.
---

# LibraryOS

Use LibraryOS operations as the only write boundary. Discover the live contract
with `libraryos operations` or `libraryos.operations.describe_operations`;
do not edit authoritative manifests or query `.libraryos` SQLite files.

Before acting:

1. Resolve the intended private library explicitly.
2. Inspect the operation's mutation, network, and capability declarations.
3. Use the narrowest capability that can complete the task.
4. Preview schema migration, archive, removal, or purge operations before their
   separately authorized write step.

Preserve these distinctions in every result:

- acquired means bytes were obtained with recorded identity evidence;
- prepared means reproducible derivatives were generated;
- opened and searched do not mean reviewed;
- a review is immutable and bound to exact source hashes and coverage;
- an assessment is a purpose-specific judgment scoped to a collection;
- an occurrence records structure and implies no scientific support.

Treat sources as immutable. Report exact work/source IDs, hashes, locators,
warnings, and limitations when relevant. Route evidence claims back to source
pages or figures; derivative text is navigation unless its coverage was
explicitly reviewed.

Use `collection.read:<id>` for a collection-scoped read agent. Do not request
`source.acquire`, `derivative.prepare`, `library.maintain`, or
`destructive.purge` unless the requested operation needs that authority.

For examples and operational detail, read
[`docs/agent-operations.md`](../../docs/agent-operations.md). For backup,
recovery, and staged removal, read
[`docs/backup-and-recovery.md`](../../docs/backup-and-recovery.md).
