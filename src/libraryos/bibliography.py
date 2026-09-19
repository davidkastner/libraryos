"""Deterministic imports for common bibliographic interchange formats."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .identity import normalize_identifiers, resolve_identifier
from .library import open_library, utc_now
from .storage import StorageError
from .works import create_work

_FORMATS = {"bibtex", "csl-json", "ris"}
_BIBTEX_TYPE_MAP = {
    "article": "article",
    "book": "book",
    "booklet": "book",
    "conference": "conference-paper",
    "inbook": "book-chapter",
    "incollection": "book-chapter",
    "inproceedings": "conference-paper",
    "manual": "manual",
    "mastersthesis": "thesis",
    "misc": "other",
    "phdthesis": "thesis",
    "proceedings": "proceedings",
    "techreport": "report",
    "unpublished": "manuscript",
}
_RIS_TYPE_MAP = {
    "BOOK": "book",
    "CHAP": "book-chapter",
    "CONF": "conference-paper",
    "DATA": "dataset",
    "JOUR": "article",
    "MGZN": "article",
    "RPRT": "report",
    "STD": "standard",
    "THES": "thesis",
    "UNPB": "manuscript",
}


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = " ".join(value.strip().split())
    return result or None


def _issued_year(value: Any) -> int | str | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.search(r"\b(\d{4})\b", value)
        return int(match.group(1)) if match else _clean(value)
    if isinstance(value, dict):
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            return _issued_year(parts[0][0])
        return _issued_year(value.get("raw"))
    return None


def _author_from_csl(value: Any) -> dict[str, str] | str | None:
    if isinstance(value, str):
        return _clean(value)
    if not isinstance(value, dict):
        return None
    if _clean(value.get("literal")):
        return {"literal": _clean(value["literal"])}
    family = _clean(value.get("family"))
    given = _clean(value.get("given"))
    if family:
        return {
            "family": family,
            **({"given": given} if given else {}),
        }
    return None


def _identifier_values(record: dict[str, Any]) -> list[dict[str, str]]:
    mappings = (
        ("doi", ("DOI", "doi")),
        ("pmid", ("PMID", "pmid")),
        ("pmcid", ("PMCID", "pmcid")),
        ("arxiv", ("arXiv", "arxiv", "eprint")),
        ("isbn", ("ISBN", "isbn")),
        ("url", ("URL", "url")),
    )
    identifiers: list[dict[str, str]] = []
    for scheme, names in mappings:
        value = next((_clean(record.get(name)) for name in names if _clean(record.get(name))), None)
        if value:
            identifiers.append({"scheme": scheme, "value": value})
    return normalize_identifiers(identifiers)


def _from_csl(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise StorageError("CSL JSON entries must be objects", code="bibliography_invalid")
    title = _clean(record.get("title"))
    authors = [
        author
        for value in record.get("author", [])
        if (author := _author_from_csl(value)) is not None
    ] if isinstance(record.get("author", []), list) else []
    container = record.get("container-title")
    if isinstance(container, list):
        container = next((_clean(value) for value in container if _clean(value)), None)
    return {
        "work_type": _clean(record.get("type")) or "other",
        "title": title,
        "identifiers": _identifier_values(record),
        "metadata": {
            **({"authors": authors} if authors else {}),
            **({"container_title": container} if _clean(container) else {}),
            **({"publisher": _clean(record.get("publisher"))} if _clean(record.get("publisher")) else {}),
            **({"issued": issued} if (issued := _issued_year(record.get("issued"))) is not None else {}),
        },
    }


def _split_bibtex_entries(text: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    position = 0
    while (start := text.find("@", position)) >= 0:
        header = re.match(r"@([A-Za-z]+)\s*([\{\(])", text[start:])
        if header is None:
            position = start + 1
            continue
        entry_type, opening = header.group(1).casefold(), header.group(2)
        closing = "}" if opening == "{" else ")"
        body_start = start + header.end()
        depth = 1
        quote = False
        escaped = False
        cursor = body_start
        while cursor < len(text) and depth:
            character = text[cursor]
            if quote:
                if character == '"' and not escaped:
                    quote = False
                escaped = character == "\\" and not escaped
                if character != "\\":
                    escaped = False
            elif character == '"':
                quote = True
            elif character == opening:
                depth += 1
            elif character == closing:
                depth -= 1
            cursor += 1
        if depth:
            raise StorageError("BibTeX entry is not balanced", code="bibliography_invalid")
        body = text[body_start : cursor - 1]
        comma = body.find(",")
        if comma < 0:
            raise StorageError("BibTeX entry has no citation key", code="bibliography_invalid")
        entries.append((entry_type, body[:comma].strip(), body[comma + 1 :]))
        position = cursor
    return entries


def _parse_bibtex_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    position = 0
    while position < len(body):
        while position < len(body) and (body[position].isspace() or body[position] == ","):
            position += 1
        if position == len(body):
            break
        match = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=", body[position:])
        if match is None:
            raise StorageError("BibTeX field is malformed", code="bibliography_invalid")
        name = match.group(1).casefold()
        position += match.end()
        while position < len(body) and body[position].isspace():
            position += 1
        if position == len(body):
            raise StorageError("BibTeX field has no value", code="bibliography_invalid")
        if body[position] in '{"':
            opening = body[position]
            closing = "}" if opening == "{" else '"'
            position += 1
            start = position
            depth = 1
            escaped = False
            while position < len(body):
                character = body[position]
                if opening == "{" and character == "{" and not escaped:
                    depth += 1
                elif character == closing and not escaped:
                    depth -= 1
                    if depth == 0:
                        break
                escaped = character == "\\" and not escaped
                if character != "\\":
                    escaped = False
                position += 1
            if depth:
                raise StorageError("BibTeX field is not balanced", code="bibliography_invalid")
            value = body[start:position]
            position += 1
        else:
            start = position
            while position < len(body) and body[position] != ",":
                position += 1
            value = body[start:position]
        fields[name] = " ".join(value.strip().split())
    return fields


def _bibtex_authors(value: str | None) -> list[dict[str, str] | str]:
    if not value:
        return []
    authors: list[dict[str, str] | str] = []
    for name in re.split(r"\s+and\s+", value):
        if "," in name:
            family, given = (_clean(part) for part in name.split(",", 1))
            if family:
                authors.append({"family": family, **({"given": given} if given else {})})
        elif cleaned := _clean(name):
            authors.append(cleaned)
    return authors


def _parse_bibtex(text: str) -> list[dict[str, Any]]:
    results = []
    for entry_type, citation_key, body in _split_bibtex_entries(text):
        if entry_type in {"comment", "preamble", "string"}:
            continue
        fields = _parse_bibtex_fields(body)
        record = {key.upper(): value for key, value in fields.items()}
        identifiers = _identifier_values(record)
        if not identifiers:
            identifiers = normalize_identifiers(
                [{"scheme": "bibtex-key", "value": citation_key}]
            )
        authors = _bibtex_authors(fields.get("author"))
        results.append(
            {
                "work_type": _BIBTEX_TYPE_MAP.get(entry_type, entry_type),
                "title": _clean(fields.get("title")),
                "identifiers": identifiers,
                "metadata": {
                    **({"authors": authors} if authors else {}),
                    **(
                        {"container_title": container}
                        if (container := _clean(fields.get("journal") or fields.get("booktitle")))
                        else {}
                    ),
                    **(
                        {"publisher": publisher}
                        if (publisher := _clean(fields.get("publisher")))
                        else {}
                    ),
                    **(
                        {"issued": issued}
                        if (issued := _issued_year(fields.get("year"))) is not None
                        else {}
                    ),
                },
            }
        )
    return results


def _parse_ris(text: str) -> list[dict[str, Any]]:
    raw_records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_tag: str | None = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = re.match(r"^([A-Z0-9]{2})  - ?(.*)$", line)
        if match:
            tag, value = match.groups()
            if tag == "ER":
                if current:
                    raw_records.append(current)
                current = {}
                last_tag = None
            else:
                current.setdefault(tag, []).append(value.strip())
                last_tag = tag
        elif line[:1].isspace() and last_tag:
            current[last_tag][-1] += " " + line.strip()
        else:
            raise StorageError(
                f"RIS line {line_number} is malformed",
                code="bibliography_invalid",
            )
    if current:
        raw_records.append(current)
    results = []
    for record in raw_records:
        single = {key: values[0] for key, values in record.items() if values}
        identifiers = _identifier_values(
            {
                "DOI": single.get("DO"),
                "ISBN": single.get("SN"),
                "URL": single.get("UR"),
            }
        )
        if not identifiers and single.get("ID"):
            identifiers = normalize_identifiers(
                [{"scheme": "ris-id", "value": single["ID"]}]
            )
        authors = [_clean(value) for key in ("AU", "A1") for value in record.get(key, [])]
        authors = [value for value in authors if value]
        title = _clean(single.get("TI") or single.get("T1"))
        container = _clean(single.get("JO") or single.get("JF") or single.get("T2"))
        results.append(
            {
                "work_type": _RIS_TYPE_MAP.get(single.get("TY", ""), "other"),
                "title": title,
                "identifiers": identifiers,
                "metadata": {
                    **({"authors": authors} if authors else {}),
                    **({"container_title": container} if container else {}),
                    **({"publisher": publisher} if (publisher := _clean(single.get("PB"))) else {}),
                    **(
                        {"issued": issued}
                        if (issued := _issued_year(single.get("PY") or single.get("Y1")))
                        is not None
                        else {}
                    ),
                },
            }
        )
    return results


def parse_bibliography(data: str | bytes, *, format: str) -> list[dict[str, Any]]:
    """Parse an interchange payload into normalized work-create arguments."""

    normalized_format = format.casefold()
    if normalized_format not in _FORMATS:
        raise StorageError(
            "Bibliography format must be bibtex, csl-json, or ris",
            code="bibliography_format_unsupported",
            path=format,
        )
    try:
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    except UnicodeDecodeError as error:
        raise StorageError(
            "Bibliography must be UTF-8",
            code="bibliography_encoding_invalid",
        ) from error
    if normalized_format == "bibtex":
        return _parse_bibtex(text)
    if normalized_format == "ris":
        return _parse_ris(text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise StorageError(
            f"CSL JSON is invalid: {error}",
            code="bibliography_invalid",
        ) from error
    records = value if isinstance(value, list) else [value]
    return [_from_csl(record) for record in records]


def import_bibliography(
    library: str | Path,
    path: str | Path,
    *,
    format: str | None = None,
) -> dict[str, Any]:
    """Import bibliographic metadata, skipping exact identifiers already present."""

    root, _ = open_library(library)
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise StorageError(
            "Bibliography source does not exist",
            code="bibliography_not_found",
            path=str(source),
        )
    selected = format.casefold() if format else {
        ".bib": "bibtex",
        ".json": "csl-json",
        ".ris": "ris",
    }.get(source.suffix.casefold())
    if selected not in _FORMATS:
        raise StorageError(
            "Could not determine bibliography format",
            code="bibliography_format_unsupported",
            path=str(source),
        )
    payload = source.read_bytes()
    records = parse_bibliography(payload, format=selected)
    source_sha256 = hashlib.sha256(payload).hexdigest()
    results = []
    for index, record in enumerate(records):
        existing = next(
            (
                match
                for identifier in record["identifiers"]
                if (
                    match := resolve_identifier(
                        root,
                        identifier["scheme"],
                        identifier["value"],
                        normalized=identifier["normalized"],
                    )
                )
                is not None
            ),
            None,
        )
        if existing is not None:
            results.append(
                {
                    "index": index,
                    "status": "existing",
                    "work_id": existing["id"],
                }
            )
            continue
        imported_at = utc_now()
        metadata = {
            **record["metadata"],
            "metadata_status": "verified",
            "metadata_imports": [
                {
                    "format": selected,
                    "source_sha256": source_sha256,
                    "record_index": index,
                    "imported_at": imported_at,
                }
            ],
        }
        created = create_work(
            root,
            work_type=record["work_type"],
            title=record["title"],
            identifiers=record["identifiers"],
            metadata=metadata,
        )
        results.append({"index": index, "status": "created", "work_id": created["id"]})
    return {
        "format": selected,
        "source_sha256": source_sha256,
        "records": len(records),
        "created": sum(item["status"] == "created" for item in results),
        "existing": sum(item["status"] == "existing" for item in results),
        "results": results,
    }
