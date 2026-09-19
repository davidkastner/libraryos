# Backup and recovery

A LibraryOS library is recoverable from its ordinary authoritative files.
SQLite indexes, caches, and rendered search state are disposable.

## What to back up

Back up, as one filesystem-consistent snapshot:

- `library.json`;
- `works/`, including manifests and all source and derivative bytes;
- `collections/`;
- `records/`, including external-collection registrations and archive
  snapshots;
- `quarantine/`;
- `.libraryos/jobs/`, `.libraryos/trash/`, and
  `.libraryos/trash-ledger/`.

The rest of `.libraryos/` may be rebuilt, but retaining it is harmless. External
collection files live outside the library and require their own backup. Their
registrations alone are not a backup of their contents.

Use a backup system that preserves permissions and does not follow symlinks.
Encrypt any destination that receives restricted publications. Keep at least
one disconnected or versioned copy.

## Safe backup sequence

1. Stop writers, including the localhost service and agent jobs.
2. Run `libraryos validate --library LIBRARY`.
3. Take an atomic filesystem snapshot when available; otherwise copy the whole
   library while writers remain stopped.
4. Record the validation result, LibraryOS version, date, and backup checksum.
5. Test restoration periodically into a new, private directory outside Git.

## Restore verification

1. Restore into an empty directory with owner-only permissions.
2. Confirm that `library.json` exists and external collection paths are valid.
3. Run full hash validation.
4. Reconcile interrupted jobs; this changes state only and never reruns work.
5. Rebuild disposable indexes.
6. Re-run validation and compare counts with the backup record.

```bash
libraryos validate --library RESTORED_LIBRARY
libraryos call job.reconcile \
  --arguments '{"library":"RESTORED_LIBRARY"}'
libraryos rebuild --library RESTORED_LIBRARY
libraryos validate --library RESTORED_LIBRARY
```

## Recover staged removal

A staged collection removal remains under `.libraryos/trash/TRANSACTION_ID`
until its retention deadline. Restore it with:

```bash
libraryos call purge.restore \
  --arguments '{"library":"LIBRARY","transaction_id":"TRANSACTION_ID"}'
```

Permanent purge is separately authorized, requires the exact transaction ID,
and writes an audit ledger. After purge, LibraryOS cannot recover the staged
records; restore them from backup. Shared works and sources are never deleted by
collection removal.

## Failure handling

Do not manually edit or delete recovery records while diagnosing a failure.
Copy the affected library first, preserve logs that do not contain publication
content, and perform repair or migration rehearsal only on the copy. A failed
validation is a reason to stop writes, not to discard the record that exposed
the inconsistency.
