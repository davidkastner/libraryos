# Threat model

LibraryOS v1 is a local-first application for one owner on a trusted machine.
It protects research-library integrity against accidental misuse, malformed
inputs, concurrent local writers, and over-broad agent authority. It is not a
multi-user authorization service or a secure remote document host.

## Protected assets

- immutable publication and supplement bytes;
- authoritative manifests, collections, reviews, and assessments;
- provenance, hashes, locators, and lifecycle audit records;
- credentials and private local paths;
- the distinction between retrieval, preparation, inspection, and scientific
  judgment.

## Trust boundaries

The filesystem owner is trusted. A direct Python or CLI call that omits a
capability set acts with owner authority. HTTP and agent sessions must carry an
explicit capability set and a fresh bearer token. Registered external
collections cross a project/library boundary and therefore use absolute-path
registration, content hashes, and compare-and-swap writes.

Source providers, downloaded bytes, imported metadata, authored YAML, PDF/XML
parsers, and agents are untrusted inputs. Git repositories and exports are
publication-free zones unless the operator explicitly establishes a different
policy outside this repository.

## Principal threats and controls

| Threat | Controls |
| --- | --- |
| Path traversal or symlink escape | Root confinement, relative manifest paths, symlink rejection |
| Silent overwrite or concurrent edit | Advisory locks, atomic replacement, expected hashes |
| Wrong paper attached to a work | Identity-gated acquisition; ambiguous bytes are quarantined |
| Secret-bearing URL persistence | Persisted URLs omit user info, query, and fragment |
| Agent overreach | Declared operation effects and least-authority capability grants |
| Accidental evidence deletion | Preview, recoverable staging, retention period, exact-ID purge confirmation |
| False scientific implication | Separate source, derivative, review, assessment, and occurrence records |
| Repository/export leakage | Ignore rules, restricted-byte export policy, bounded privacy scan |
| Interrupted work | Durable job history, checkpoints, interruption reconciliation, explicit retry |

The `allow_before_retention` purge argument is an emergency owner override. It
does not make deletion safer and must only be exposed to a session holding
`destructive.purge`. A caller must still supply the exact transaction ID.

## Out of scope in v1

- hostile users with access to the same operating-system account;
- a compromised kernel, interpreter, converter binary, or dependency;
- untrusted remote hosting or internet exposure of the localhost service;
- digital-rights enforcement or redistribution authorization;
- malware detection in downloaded publications;
- truth evaluation of reviews or assessments.

Do not expose the v1 HTTP service beyond loopback. Use operating-system disk
encryption and access controls for restricted material.

## Security verification

Run:

```bash
libraryos validate --library /path/to/library
libraryos call privacy.scan \
  --arguments '{"path":"/path/to/export","allow_publication_bytes":false}'
```

Privacy findings identify the file and category but never echo matched secret
values or publication content.
