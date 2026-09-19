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

Creating or finding a work does not mean its source has been inspected or that
it supports a claim.

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
- A review identifies the exact source hashes and coverage a reviewer examined.
- An assessment records a purpose-specific judgment in a collection.
- An occurrence records an external structural reference and implies no support.
- Use source locators and page/figure fallbacks when reporting evidence.

On a failed operation, preserve the structured error code, path, and recovery
guidance. Do not bypass a failure by editing authoritative files directly.
