# Contributing to LibraryOS

LibraryOS is infrastructure for preserving and operating over scholarly
sources. Changes must preserve the distinction between possession, preparation,
review, and scientific assessment.

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
python -m build
```

## Design constraints

- Keep the core domain-neutral. Domain behavior belongs in namespaced
  extensions or separate adapters.
- Treat ordinary manifests and immutable source bytes as authoritative.
  SQLite and search indexes must remain rebuildable.
- Never add real restricted publications, credentials, cookies, authorization
  headers, or signed URLs to fixtures or Git history.
- Never infer inspection from acquisition, preparation, search, or opening a
  source.
- Add or update schema fixtures and compatibility tests with every contract
  change.
- Make mutations atomic and make destructive behavior previewable.

Read [the product specification](docs/product-specification.md) before changing
the domain model or public operations.
