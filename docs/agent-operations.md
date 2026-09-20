# Agent operations

Agents and humans use the same versioned LibraryOS operations. Agents should
call the Python dispatcher or local HTTP API rather than editing manifests or
querying SQLite.

## Discover the contract

```bash
libraryos operations
libraryos call library.status \
  --arguments '{"library":"/path/to/private-library"}'
```

Every response identifies its contract version and operation effects. Inspect
the operation contract before requesting a capability or invoking a mutation.

## Domain-neutral Python example

```python
from libraryos.operations import invoke

library = "/path/to/private-library"

created = invoke(
    "work.create",
    {
        "library": library,
        "work_type": "standard",
        "title": "Cryogenic equipment safety requirements",
        "identifiers": [{"scheme": "local", "value": "LAB-STD-14"}],
    },
)
work_id = created["result"]["id"]

invoke("library.rebuild", {"library": library})
matches = invoke("search", {"library": library, "query": "cryogenic"})
```

## Discover backward and forward citation candidates

Use the public, non-mutating OpenAlex relation operation for a DOI seed:

```python
neighbors = invoke(
    "relations.openalex.discover",
    {
        "doi": "10.1000/example",
        "directions": ["references", "citations"],
        "limit": 25,
    },
)["result"]
```

Each result retains the relation direction, identifiers, provider work ID, and
provider URL. Discovery does not add the candidate to the library, establish
that anyone inspected it, or imply that it supports a scientific claim.

Creating or finding a work does not mean its source has been inspected or that
it supports a claim.

## Synchronize references from an external document

Adapters may atomically synchronize the complete set of work occurrences
contributed by one external document. The adapter—not LibraryOS—defines the
document identity, attachment classes, external object paths, and any
namespaced extension semantics.

Resolve identifiers in a bounded batch, preserving invalid identifiers as
unresolved occurrence records:

```python
resolved = invoke(
    "work.resolve_identifiers",
    {
        "library": library,
        "identifiers": [{"scheme": "doi", "value": "10.0000/example"}],
        "create_missing": True,
    },
)["result"]
work_id = resolved["items"][0]["work_id"]
```

Preview and apply the same complete replacement request. The preview token
binds the exact request to the observed prior source hash:

```python
request = {
    "library": library,
    "collection_id": "example-corpus",
    "adapter": "org.example.documents",
    "document_id": "document-17",
    "location": "records/document-17.yaml",
    "source_sha256": "0" * 64,
    "expected_previous_sha256": None,
    "occurrences": [{
        "external_key": "reference:1",
        "work": work_id,
        "attachment_class": "bibliography",
        "external_object_paths": ["/references/0"],
    }],
}
preview = invoke("external_document.sync.preview", request)["result"]
applied = invoke(
    "external_document.sync.apply",
    {**request, "preview_token": preview["preview_token"]},
)
```

Synchronizing an empty `occurrences` array retains the known document and
records that it currently contributes no references. Removing an occurrence
does not delete its shared work. Use `external_document.query`,
`occurrence.query`, and `read.resolve_many` for subsequent navigation.
Synchronization records structure only: every result explicitly leaves
scientific inspection unperformed and scientific support unassessed.

## Least-authority collection access

An agent assigned only `collection.read:standards-review` can fetch that
collection and works belonging to it, but cannot list unrelated works or write
records:

```python
result = invoke(
    "collection.get",
    {"library": library, "collection_id": "standards-review"},
    capabilities=["collection.read:standards-review"],
)
```

Request broader authority only when the task requires it. `source.acquire`
permits network acquisition; `derivative.prepare` permits generated files;
`destructive.purge` is deliberately separate from ordinary collection editing.

## Scientific-state boundaries

- Acquisition proves possession and recorded identity evidence, not review.
- Preparation creates navigational derivatives, not scientific conclusions.
- Search results always carry `scientific_support: not_assessed`.
- `search.prepared` accepts an optional `work_ids` array (at most 1000 IDs) to
  search a curator-selected source set before widening to the whole library.
  Results are deterministically ordered by full-text relevance and stable IDs.
  Scoping and ranking improve navigation only; neither implies relevance,
  inspection, or support.
- A review identifies the exact source hashes and coverage a reviewer examined.
- An assessment records a purpose-specific judgment in a collection.
- An occurrence records an external structural reference and implies no support.
- Use source locators and page/figure fallbacks when reporting evidence.

On a failed operation, preserve the structured error code, path, and recovery
guidance. Do not bypass a failure by editing authoritative files directly.
