"""Portable collection manifests and external project registrations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .identity import resolve_identifier
from .library import open_library, utc_now
from .schemas import SchemaError, validate_record
from .storage import (
    StorageError,
    atomic_bytes,
    atomic_json,
    exclusive_lock,
    read_json_record,
    resolve_library_path,
)
from .works import get_work

COLLECTION_SCHEMA = "https://libraryos.dev/schemas/collection/v1"
REGISTRATION_SCHEMA = "https://libraryos.dev/schemas/collection-registration/v1"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses silent duplicate-key replacement."""


def _construct_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _format(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    raise StorageError(
        "Collection files must use .json, .yaml, or .yml",
        code="collection_format_unsupported",
        path=str(path),
    )


def _load_payload(path: Path, payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StorageError(
            "Collection file must be UTF-8",
            code="collection_encoding_invalid",
            path=str(path),
        ) from error
    try:
        value = (
            json.loads(text)
            if _format(path) == "json"
            else yaml.load(text, Loader=_UniqueKeyLoader)
        )
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise StorageError(
            f"Collection file could not be parsed: {error}",
            code="collection_parse_failed",
            path=str(path),
        ) from error
    if not isinstance(value, dict):
        raise StorageError(
            "Collection file must contain one mapping/object",
            code="collection_type_invalid",
            path=str(path),
        )
    try:
        validate_record(value, COLLECTION_SCHEMA)
    except SchemaError as error:
        raise StorageError(
            str(error),
            code=error.code,
            path=f"{path}{error.path}",
        ) from error
    return value


def read_collection_file(path: str | Path) -> dict[str, Any]:
    """Read and validate a JSON or safely-authored YAML collection."""

    source = Path(path).expanduser().absolute()
    if source.is_symlink():
        raise StorageError(
            "An authoritative collection path must not be a symlink",
            code="collection_path_symlink",
            path=str(source),
        )
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise StorageError(
            f"Could not read collection: {error}",
            code="collection_read_failed",
            path=str(source),
        ) from error
    return _load_payload(source, payload)


def _validate_work_references(root: Path, record: dict[str, Any]) -> None:
    for member in record["members"]:
        reference = member["work"]
        if isinstance(reference, str):
            get_work(root, reference)


def _registration_path(root: Path, collection_id: str) -> Path:
    return resolve_library_path(
        root,
        f"records/collection-registrations/{collection_id}.json",
    )


def register_external_collection(
    library: str | Path,
    path: str | Path,
) -> dict[str, Any]:
    """Bind an external authoritative collection to a library at its current hash."""

    root, _ = open_library(library)
    source = Path(path).expanduser().absolute()
    if source.is_symlink():
        raise StorageError(
            "An authoritative collection path must not be a symlink",
            code="collection_path_symlink",
            path=str(source),
        )
    source = source.resolve()
    try:
        source.relative_to(root)
    except ValueError:
        pass
    else:
        raise StorageError(
            "External collections must live outside the library root",
            code="collection_not_external",
            path=str(source),
        )
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise StorageError(
            f"Could not read external collection: {error}",
            code="collection_read_failed",
            path=str(source),
        ) from error
    record = _load_payload(source, payload)
    _validate_work_references(root, record)
    local = root / "collections" / f"{record['id']}.json"
    if local.exists():
        raise StorageError(
            "A library-owned collection already has this ID",
            code="collection_id_conflict",
            path=record["id"],
        )
    registration_path = _registration_path(root, record["id"])
    existing = read_json_record(registration_path) if registration_path.exists() else None
    if existing is not None and Path(existing["path"]) != source:
        raise StorageError(
            "This collection ID is already registered to another path",
            code="collection_registration_conflict",
            path=record["id"],
        )
    content_hash = _sha256(payload)
    if existing is not None and existing["sha256"] != content_hash:
        raise StorageError(
            "Registered collection changed since it was last validated",
            code="collection_external_conflict",
            path=str(source),
        )
    now = utc_now()
    registration = {
        "schema": REGISTRATION_SCHEMA,
        "schema_version": 1,
        "id": record["id"],
        "path": str(source),
        "format": _format(source),
        "sha256": content_hash,
        "validated_at": now,
        "created_at": existing["created_at"] if existing else now,
        "updated_at": now,
    }
    validate_record(registration, REGISTRATION_SCHEMA)
    atomic_json(registration_path, registration)
    return {"collection": record, "registration": registration}


def _registration(root: Path, collection_id: str) -> dict[str, Any] | None:
    path = _registration_path(root, collection_id)
    return read_json_record(path) if path.is_file() else None


def get_collection(library: str | Path, collection_id: str) -> dict[str, Any]:
    """Resolve a library-owned or registered external collection."""

    root, _ = open_library(library)
    local = root / "collections" / f"{collection_id}.json"
    if local.is_file():
        return {
            "collection": read_json_record(local),
            "location": {"kind": "library", "path": str(local)},
        }
    registration = _registration(root, collection_id)
    if registration is None:
        raise StorageError(
            "Collection does not exist",
            code="collection_not_found",
            path=collection_id,
        )
    source = Path(registration["path"])
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise StorageError(
            f"Could not read registered collection: {error}",
            code="collection_read_failed",
            path=str(source),
        ) from error
    actual_hash = _sha256(payload)
    if actual_hash != registration["sha256"]:
        raise StorageError(
            "Registered collection changed since it was last validated",
            code="collection_external_conflict",
            path=str(source),
        )
    record = _load_payload(source, payload)
    if record["id"] != collection_id:
        raise StorageError(
            "Registered collection ID no longer matches its registration",
            code="collection_registration_id_mismatch",
            path=str(source),
        )
    _validate_work_references(root, record)
    return {
        "collection": record,
        "location": {
            "kind": "external",
            "path": str(source),
            "sha256": actual_hash,
        },
    }


def _serialize_collection(record: dict[str, Any], file_format: str) -> bytes:
    if file_format == "json":
        text = json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    else:
        text = yaml.safe_dump(
            record,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    return text.encode("utf-8")


def put_external_collection(
    library: str | Path,
    collection_id: str,
    record: dict[str, Any],
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    """Replace a registered external manifest only if its known hash still matches."""

    root, _ = open_library(library)
    validate_record(record, COLLECTION_SCHEMA)
    if record["id"] != collection_id:
        raise StorageError(
            "Collection record ID does not match the requested collection",
            code="collection_id_mismatch",
            path=record["id"],
        )
    _validate_work_references(root, record)
    registration_path = _registration_path(root, collection_id)
    lock = root / ".libraryos" / "locks" / f"collection-{collection_id}.lock"
    with exclusive_lock(lock):
        if not registration_path.is_file():
            raise StorageError(
                "External collection is not registered",
                code="collection_not_found",
                path=collection_id,
            )
        registration = read_json_record(registration_path)
        source = Path(registration["path"])
        try:
            current_payload = source.read_bytes()
        except OSError as error:
            raise StorageError(
                f"Could not read registered collection: {error}",
                code="collection_read_failed",
                path=str(source),
            ) from error
        current_hash = _sha256(current_payload)
        if expected_sha256 != registration["sha256"] or current_hash != expected_sha256:
            raise StorageError(
                "External collection changed; reload it before writing",
                code="collection_external_conflict",
                path=str(source),
            )
        payload = _serialize_collection(record, registration["format"])
        atomic_bytes(source, payload)
        now = utc_now()
        registration = {
            **registration,
            "sha256": _sha256(payload),
            "validated_at": now,
            "updated_at": now,
        }
        atomic_json(registration_path, registration)
    return {"collection": record, "registration": registration}


def revalidate_external_collection(
    library: str | Path,
    collection_id: str,
    *,
    expected_registered_sha256: str,
) -> dict[str, Any]:
    """Validate and explicitly adopt an out-of-band external collection edit."""

    root, _ = open_library(library)
    registration_path = _registration_path(root, collection_id)
    lock = root / ".libraryos" / "locks" / f"collection-{collection_id}.lock"
    with exclusive_lock(lock):
        if not registration_path.is_file():
            raise StorageError(
                "External collection is not registered",
                code="collection_not_found",
                path=collection_id,
            )
        registration = read_json_record(registration_path)
        if registration["sha256"] != expected_registered_sha256:
            raise StorageError(
                "Registration changed; reload it before revalidating",
                code="collection_registration_conflict",
                path=collection_id,
            )
        source = Path(registration["path"])
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise StorageError(
                f"Could not read registered collection: {error}",
                code="collection_read_failed",
                path=str(source),
            ) from error
        record = _load_payload(source, payload)
        if record["id"] != collection_id:
            raise StorageError(
                "Registered collection ID no longer matches its registration",
                code="collection_registration_id_mismatch",
                path=str(source),
            )
        _validate_work_references(root, record)
        now = utc_now()
        registration = {
            **registration,
            "sha256": _sha256(payload),
            "validated_at": now,
            "updated_at": now,
        }
        atomic_json(registration_path, registration)
    return {"collection": record, "registration": registration}


def relocate_external_collection(
    library: str | Path,
    collection_id: str,
    new_path: str | Path,
    *,
    expected_registered_sha256: str,
) -> dict[str, Any]:
    """Rebind an unchanged external collection after its project path moves."""

    root, _ = open_library(library)
    destination = Path(new_path).expanduser().absolute()
    if destination.is_symlink():
        raise StorageError(
            "An authoritative collection path must not be a symlink",
            code="collection_path_symlink",
            path=str(destination),
        )
    destination = destination.resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise StorageError(
            "External collections must live outside the library root",
            code="collection_not_external",
            path=str(destination),
        )
    registration_path = _registration_path(root, collection_id)
    lock = root / ".libraryos" / "locks" / f"collection-{collection_id}.lock"
    with exclusive_lock(lock):
        if not registration_path.is_file():
            raise StorageError(
                "External collection is not registered",
                code="collection_not_found",
                path=collection_id,
            )
        registration = read_json_record(registration_path)
        if registration["sha256"] != expected_registered_sha256:
            raise StorageError(
                "Registration changed; reload it before relocation",
                code="collection_registration_conflict",
                path=collection_id,
            )
        try:
            payload = destination.read_bytes()
        except OSError as error:
            raise StorageError(
                f"Could not read relocated collection: {error}",
                code="collection_read_failed",
                path=str(destination),
            ) from error
        if _sha256(payload) != registration["sha256"]:
            raise StorageError(
                "Relocated collection bytes do not match the registered version",
                code="collection_relocation_content_mismatch",
                path=str(destination),
            )
        record = _load_payload(destination, payload)
        if record["id"] != collection_id:
            raise StorageError(
                "Relocated collection ID does not match its registration",
                code="collection_registration_id_mismatch",
                path=str(destination),
            )
        _validate_work_references(root, record)
        now = utc_now()
        updated = {
            **registration,
            "path": str(destination),
            "format": _format(destination),
            "validated_at": now,
            "updated_at": now,
        }
        atomic_json(registration_path, updated)
    return {"collection": record, "registration": updated}


def list_collections(library: str | Path) -> list[dict[str, Any]]:
    """List local and registered external collections with location metadata."""

    root, _ = open_library(library)
    result = [
        {
            "collection": read_json_record(path),
            "location": {"kind": "library", "path": str(path)},
        }
        for path in sorted((root / "collections").glob("*.json"))
    ]
    for path in sorted((root / "records" / "collection-registrations").glob("*.json")):
        registration = read_json_record(path)
        try:
            result.append(get_collection(root, registration["id"]))
        except StorageError as error:
            result.append(
                {
                    "collection": {"id": registration["id"]},
                    "location": {
                        "kind": "external",
                        "path": registration["path"],
                        "sha256": registration["sha256"],
                    },
                    "error": {"code": error.code, "message": str(error)},
                }
            )
    return result


def _resolve_member_work(root: Path, reference: Any) -> dict[str, Any]:
    if isinstance(reference, str):
        return get_work(root, reference)
    wanted = reference["identifier"]
    match = resolve_identifier(
        root,
        wanted["scheme"],
        wanted["value"],
        normalized=wanted.get("normalized"),
    )
    if match is None:
        raise StorageError(
            "Collection member does not resolve to a library work",
            code="collection_work_unresolved",
            path=f"{wanted['scheme']}:{wanted['value']}",
        )
    return match


def _ordered_members(root: Path, collection: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    indexed = list(enumerate(collection["members"]))
    indexed.sort(key=lambda item: (item[1].get("order", float("inf")), item[0]))
    return [
        (member, _resolve_member_work(root, member["work"]))
        for _, member in indexed
    ]


def _author_text(author: Any) -> str:
    if isinstance(author, str):
        return author
    if "literal" in author:
        return author["literal"]
    return ", ".join(part for part in (author.get("family"), author.get("given")) if part)


def _citation_key(member: dict[str, Any], work: dict[str, Any], used: set[str]) -> str:
    supplied = member.get("citation_key")
    if supplied:
        base = supplied
    else:
        authors = work["work"].get("authors", [])
        first = _author_text(authors[0]).split(",")[0].split()[-1] if authors else "Work"
        year = str(work["work"].get("issued") or "nd")[:4]
        base = re.sub(r"[^A-Za-z0-9_:-]", "", f"{first}{year}") or "Work"
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}{index}"
        index += 1
    used.add(candidate)
    return candidate


def _identifier(work: dict[str, Any], scheme: str) -> str | None:
    for item in work["work"]["identifiers"]:
        if item["scheme"].casefold() == scheme:
            return item.get("normalized", item["value"])
    return None


def _citation_entries(
    root: Path,
    collection: dict[str, Any],
) -> list[dict[str, Any]]:
    used: set[str] = set()
    entries = []
    for member, manifest in _ordered_members(root, collection):
        metadata = manifest["work"]
        entries.append(
            {
                "key": _citation_key(member, manifest, used),
                "libraryos_id": manifest["id"],
                "type": metadata["type"],
                "title": metadata["title"],
                "authors": metadata.get("authors", []),
                "container_title": metadata.get("container_title"),
                "publisher": metadata.get("publisher"),
                "issued": metadata.get("issued"),
                "doi": _identifier(manifest, "doi"),
                "url": _identifier(manifest, "url"),
                "member": member,
            }
        )
    return entries


def _bibtex(entries: list[dict[str, Any]]) -> str:
    blocks = []
    type_map = {
        "article": "article",
        "book": "book",
        "chapter": "incollection",
        "thesis": "phdthesis",
        "report": "techreport",
    }
    for entry in entries:
        fields = [("title", entry["title"])]
        if entry["authors"]:
            fields.append(("author", " and ".join(_author_text(item) for item in entry["authors"])))
        fields.extend(
            (name, value)
            for name, value in (
                ("journal", entry["container_title"]),
                ("publisher", entry["publisher"]),
                ("year", str(entry["issued"])[:4] if entry["issued"] is not None else None),
                ("doi", entry["doi"]),
                ("url", entry["url"]),
            )
            if value
        )
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        blocks.append(f"@{type_map.get(entry['type'], 'misc')}{{{entry['key']},\n{body}\n}}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _csl_json(entries: list[dict[str, Any]]) -> str:
    result = []
    for entry in entries:
        authors = []
        for author in entry["authors"]:
            if isinstance(author, str):
                authors.append({"literal": author})
            elif "literal" in author:
                authors.append({"literal": author["literal"]})
            else:
                authors.append(
                    {
                        key: author[key]
                        for key in ("family", "given")
                        if key in author
                    }
                )
        item: dict[str, Any] = {
            "id": entry["key"],
            "type": "article-journal" if entry["type"] == "article" else entry["type"],
            "title": entry["title"],
        }
        if authors:
            item["author"] = authors
        if entry["container_title"]:
            item["container-title"] = entry["container_title"]
        if entry["publisher"]:
            item["publisher"] = entry["publisher"]
        if entry["issued"] is not None:
            match = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(entry["issued"]))
            if match:
                item["issued"] = {
                    "date-parts": [[int(value) for value in match.groups() if value]]
                }
        if entry["doi"]:
            item["DOI"] = entry["doi"]
        if entry["url"]:
            item["URL"] = entry["url"]
        result.append(item)
    return json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _ris(entries: list[dict[str, Any]]) -> str:
    type_map = {"article": "JOUR", "book": "BOOK", "chapter": "CHAP", "thesis": "THES", "report": "RPRT"}
    records = []
    for entry in entries:
        lines = [f"TY  - {type_map.get(entry['type'], 'GEN')}", f"ID  - {entry['key']}"]
        lines.extend(f"AU  - {_author_text(author)}" for author in entry["authors"])
        lines.append(f"TI  - {entry['title']}")
        if entry["container_title"]:
            lines.append(f"JO  - {entry['container_title']}")
        if entry["publisher"]:
            lines.append(f"PB  - {entry['publisher']}")
        if entry["issued"] is not None:
            lines.append(f"PY  - {str(entry['issued'])[:4]}")
        if entry["doi"]:
            lines.append(f"DO  - {entry['doi']}")
        if entry["url"]:
            lines.append(f"UR  - {entry['url']}")
        lines.append("ER  -")
        records.append("\n".join(lines))
    return "\n\n".join(records) + ("\n" if records else "")


def _overview(collection: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    lines = [f"# {collection['title']}", ""]
    if collection.get("description"):
        lines.extend([collection["description"], ""])
    lines.extend(
        [
            "> Generated by LibraryOS from the authoritative collection manifest.",
            "> Scientific support is not inferred from collection membership.",
            "",
            "## Works",
            "",
        ]
    )
    for entry in entries:
        author = _author_text(entry["authors"][0]) if entry["authors"] else "Unknown author"
        year = str(entry["issued"])[:4] if entry["issued"] is not None else "n.d."
        identifiers = []
        if entry["doi"]:
            identifiers.append(f"DOI: {entry['doi']}")
        if entry["url"]:
            identifiers.append(entry["url"])
        suffix = f" ({'; '.join(identifiers)})" if identifiers else ""
        lines.append(f"- {author} ({year}). {entry['title']}.{suffix}")
    return "\n".join(lines).rstrip() + "\n"


def export_collection(
    library: str | Path,
    collection_id: str,
    *,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Regenerate configured, metadata-only collection outputs deterministically."""

    root, _ = open_library(library)
    resolved = get_collection(root, collection_id)
    collection = resolved["collection"]
    entries = _citation_entries(root, collection)
    location = resolved["location"]
    if output_root is None:
        if location["kind"] != "external":
            raise StorageError(
                "Library-owned collections require an explicit output root",
                code="collection_output_root_required",
                path=collection_id,
            )
        destination_root = Path(location["path"]).parent
    else:
        destination_root = Path(output_root).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    generators = {
        "overview_markdown": lambda: _overview(collection, entries),
        "bibtex": lambda: _bibtex(entries),
        "csl_json": lambda: _csl_json(entries),
        "ris": lambda: _ris(entries),
        "canonical_json": lambda: json.dumps(
            {
                "collection": collection,
                "works": [
                    {
                        "citation_key": entry["key"],
                        "libraryos_id": entry["libraryos_id"],
                        "metadata": {
                            key: value
                            for key, value in entry.items()
                            if key not in {"key", "libraryos_id", "member"}
                        },
                        "membership": entry["member"],
                    }
                    for entry in entries
                ],
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    }
    generated = []
    for output in collection.get("outputs", []):
        relative = output["path"]
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in relative:
            raise StorageError(
                "Collection output paths must remain below the output root",
                code="collection_output_path_unsafe",
                path=relative,
            )
        kind = output["kind"]
        destination = (destination_root / candidate).resolve()
        try:
            destination.relative_to(destination_root.resolve())
        except ValueError as error:
            raise StorageError(
                "Collection output path escapes the output root",
                code="collection_output_path_unsafe",
                path=relative,
            ) from error
        if location["kind"] == "external" and destination == Path(location["path"]):
            raise StorageError(
                "A generated output cannot overwrite its authoritative collection",
                code="collection_output_overwrites_manifest",
                path=str(destination),
            )
        payload = generators[kind]().encode("utf-8")
        atomic_bytes(destination, payload, mode=0o644)
        generated.append(
            {
                "kind": kind,
                "path": str(destination),
                "sha256": _sha256(payload),
                "bytes": len(payload),
            }
        )
    return {
        "collection_id": collection_id,
        "generated": generated,
        "publication_bytes_exported": False,
        "scientific_support": "not_assessed",
    }
