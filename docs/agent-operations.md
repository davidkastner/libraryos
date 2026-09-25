# Agent operations

Agents and humans use the same versioned LibraryOS operations. Agents should
call the Python dispatcher or local HTTP API rather than editing manifests or
querying SQLite.

## Discover the contract

```bash
libraryos operations
libraryos operations metadata.crossref.resolve
libraryos call library.status \
  --arguments '{"library":"/path/to/private-library"}'
```

Every response identifies its contract version and operation effects. Inspect
the operation contract before requesting a capability or invoking a mutation.
The optional operation name avoids loading the full catalog when an agent needs
one command's description, argument schema, effects, and required capability.
Commands accept `--format json` (the unchanged default) or `--format text` for
readable YAML without discarding result fields. Execution errors remain JSON.
Use `operations OPERATION` for callable argument contracts and `schema NAME`
for stored record schemas; these are distinct namespaces.

If `libraryos` is not on `PATH` in a source checkout, invoke
`.venv/bin/libraryos` from that checkout. For repeated use, install the checkout
as an editable tool with `uv tool install --editable /path/to/libraryos`; do not
depend on shell aliases or startup-file changes that other agents cannot see.

## Resolve, acquire, prepare, and inspect a work

The successful results for these operations include structured `next_actions`
with the identifiers already known at that stage:

1. `metadata.crossref.resolve` stores an unaccepted assertion and points to
   `metadata.assertion.accept`.
2. `metadata.assertion.accept` points to `source.crossref.discover`.
3. `source.crossref.discover` returns one `source.acquire` action per registered
   candidate. The agent must still choose the source role and explicitly
   authorize its access class.
4. `source.acquire` points to `source.prepare` using the acquired source ID.
5. `source.prepare` points to work-scoped `search.prepared` and `read.resolve`.

These actions expose a mechanical workflow; they do not choose among source
candidates, grant access, or establish that an acquired or prepared source was
scientifically inspected.

## Complete agent workflow: from citation to claim receipt

Use one explicit chain and preserve the identifiers returned at each stage:

1. Discover the exact operation contract:

   ```bash
   libraryos operations metadata.crossref.resolve
   libraryos operations source.acquire
   libraryos operations source.prepare
   libraryos operations search.prepared
   libraryos operations read.resolve
   ```

2. Resolve the citation, accept the selected metadata assertion, discover
   permitted source candidates, and acquire one explicitly selected source.
   Follow each result's structured `next_actions`; never infer an access class.
3. Prepare the acquired source. Preparation creates searchable text, page
   renderings, and figure routes when the source permits them; it is not source
   inspection.
4. Search a curator-selected work scope:

   ```bash
   libraryos call search.prepared --arguments \
     '{"library":"/path/to/library","query":"His 57 proton transfer",\
"query_mode":"literal","work_ids":["WORK_ID"]}'
   ```

5. Open the exact passage, page, or figure using `read.resolve` and the stable
   locator returned by search or preparation. A search snippet alone is not an
   inspected passage.
6. Save the source hash, locator, observed statement, and downstream claim or
   object mapping in the calling project's review record. LibraryOS preserves
   source identity and navigation; the adapter owns domain-specific claim
   semantics.

### Recovery without bypassing LibraryOS

- If `libraryos operation` fails, the discovery command is plural:
  `libraryos operations` or `libraryos operations OPERATION`.
- If `--args` fails, use the public spelling `--arguments` with one JSON object.
- On HTTP 429 throttling, preserve the error and retry later or select another
  registered source candidate. Do not hammer the provider or treat metadata as
  full text.
- On HTTP 401/403 or a restricted-source result, record the access boundary and
  try another lawful route such as an open repository, author manuscript, or
  supplied local file. Do not relabel an access-denied page as a paper.
- If a source reports an unexpected media type, inspect the recorded response
  metadata. XML may legitimately be `application/xml` or `text/xml`; upgrade
  LibraryOS if the installed contract rejects one of those standard forms.
- When a passage cannot settle a claim, inspect the exact page rendering and
  linked figure asset. Record figure inspection separately from text search.

Direct publisher or metadata queries may be useful for diagnosing a provider,
but they must not replace the LibraryOS work/source record when the result is
used as durable campaign evidence.

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

## Search prepared text

Use literal mode for ordinary names, residue numbers, and punctuation:

```bash
libraryos search --library /path/to/private-library --full-text \
  'Asp-102 protonated' --format text
libraryos call search.prepared --arguments \
  '{"library":"/path/to/private-library","query":"acetyl-CoA","query_mode":"literal"}'
```

Literal mode quotes each whitespace-separated searchable term and requires them
all. Standalone punctuation such as `/` or `→` is omitted because it has no
indexed token; all-punctuation queries are rejected. Attached punctuation such
as `NAD+` remains in the effective expression, but the index tokenizer does not
distinguish it from `NAD`. Punctuation is never
interpreted as an FTS operator or column selector. For a curated Boolean query,
use `--query-mode fts` or the `search.prepared` operation's existing FTS default:

```bash
libraryos search --library /path/to/private-library --full-text \
  --query-mode fts '"ATP" OR "GTP"'
```

The CLI reports `query_mode` and `effective_query` even for zero hits. Repeat
`--work-id` to scope CLI searches, or pass `work_ids` to `search.prepared`.
Malformed FTS returns `search_query_invalid` with a literal-mode recovery
action. It never silently changes the meaning of an explicit query.

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
