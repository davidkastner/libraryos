"""Deterministic, source-faithful preparation routes."""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .jobs import append_attempt, append_exception, create_job, get_job, update_job
from .library import open_library
from .storage import StorageError, resolve_library_path
from .works import get_work, register_derivative

GENERATOR_NAME = "libraryos"
GENERATOR_VERSION = "0.1.0.dev0"


class _ArticleHTML(HTMLParser):
    """Conservative HTML-to-Markdown event renderer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0
        self.list_depth = 0
        self.in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "nav", "form"}:
            self.suppressed += 1
            return
        if self.suppressed:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag in {"p", "div", "section", "article", "figure", "figcaption", "table", "tr"}:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self.list_depth += 1
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append(f"\n{'  ' * max(0, self.list_depth - 1)}- ")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag == "pre":
            self.in_pre = True
            self.parts.append("\n\n```\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "form"}:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if self.suppressed:
            return
        if tag in {"ul", "ol"}:
            self.list_depth = max(0, self.list_depth - 1)
        elif tag == "pre":
            self.in_pre = False
            self.parts.append("\n```\n")
        elif re.fullmatch(r"h[1-6]", tag) or tag in {
            "p",
            "section",
            "article",
            "figure",
            "figcaption",
        }:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.suppressed or not data:
            return
        if self.in_pre:
            self.parts.append(data)
            return
        normalized = re.sub(r"\s+", " ", data)
        if normalized.strip():
            if self.parts and not self.parts[-1].endswith((" ", "\n")):
                self.parts.append(" ")
            self.parts.append(normalized.strip())

    def markdown(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip() + "\n"


def _xml_markdown(source: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as error:
        raise StorageError(
            "XML source could not be parsed",
            code="conversion_failure",
            path=str(source),
        ) from error
    parts: list[str] = []
    locators: list[dict[str, Any]] = []
    heading_tags = {"article-title": 1, "title": 2}
    block_tags = {"p", "abstract", "caption", "table-wrap", "ref"}

    def visit(element: ET.Element) -> None:
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag == "pb":
            page = element.attrib.get("n")
            if page:
                parts.append(f"<!-- source-page: {page} -->")
                locators.append({"type": "page", "value": page})
            return
        if tag in heading_tags or tag in block_tags:
            text = " ".join("".join(element.itertext()).split())
            if text:
                if tag in heading_tags:
                    parts.append(f"{'#' * heading_tags[tag]} {text}")
                else:
                    parts.append(text)
                if tag in heading_tags:
                    locators.append({"type": "section", "value": text})
            return
        for child in element:
            visit(child)

    visit(root)
    if not parts:
        raise StorageError(
            "XML source contains no usable article text",
            code="conversion_failure",
            path=str(source),
        )
    return "\n\n".join(parts) + "\n", locators


def _text_markdown(
    source: Path, media_type: str
) -> tuple[str, list[str], list[dict[str, Any]]]:
    try:
        raw = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise StorageError(
            "Text source is not valid UTF-8",
            code="conversion_failure",
            path=str(source),
        ) from error
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _ArticleHTML()
        parser.feed(raw)
        value = parser.markdown()
        warnings = ["html_structure_conservatively_preserved"]
        locators: list[dict[str, Any]] = []
    elif media_type in {"application/xml", "text/xml"}:
        value, locators = _xml_markdown(source)
        warnings = ["xml_structure_and_source_anchors_preserved"]
    else:
        value = raw if raw.endswith("\n") else raw + "\n"
        warnings = []
        locators = []
    if not value.strip():
        raise StorageError(
            "Source contains no usable text",
            code="conversion_failure",
            path=str(source),
        )
    return value, warnings, locators


def _configuration_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _resume_derivatives(
    library: str | Path,
    work_id: str,
    source_sha256: str,
    checkpoint: dict[str, Any],
) -> list[dict[str, Any]]:
    derivative_ids = checkpoint.get("derivative_ids", [])
    if not isinstance(derivative_ids, list) or not all(
        isinstance(item, str) for item in derivative_ids
    ):
        raise StorageError(
            "Preparation checkpoint has invalid derivative identities",
            code="preparation_checkpoint_invalid",
        )
    by_id = {item["id"]: item for item in get_work(library, work_id)["derivatives"]}
    try:
        derivatives = [by_id[item] for item in derivative_ids]
    except KeyError as error:
        raise StorageError(
            "Preparation checkpoint refers to a missing derivative",
            code="preparation_checkpoint_stale",
            path=str(error.args[0]),
        ) from error
    if any(source_sha256 not in item["input_sha256"] for item in derivatives):
        raise StorageError(
            "Preparation checkpoint refers to a different source",
            code="preparation_checkpoint_stale",
        )
    return derivatives


def _page_number(derivative: dict[str, Any], source_id: str) -> int | None:
    for locator in derivative.get("locators", []):
        if locator.get("artifact_id") == source_id and locator.get("type") == "page":
            value = locator.get("value")
            return value if isinstance(value, int) else None
    return None


def _pdf_derivatives(
    library: str | Path,
    work_id: str,
    source_id: str,
    source: dict[str, Any],
    source_path: Path,
    temporary: Path,
    *,
    render_dpi: int,
    ocr_empty_pages: bool,
    job_id: str,
    identity_warnings: list[str],
    resume_checkpoint: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import pymupdf
    except ImportError as error:
        raise StorageError(
            "PDF preparation requires PyMuPDF",
            code="conversion_dependency_missing",
            path="PyMuPDF",
        ) from error

    configuration = {
        "render_dpi": render_dpi,
        "ocr_empty_pages": ocr_empty_pages,
        "max_render_pixels": 40_000_000,
    }
    fingerprint = _configuration_sha256(configuration)
    checkpoint = resume_checkpoint or {}
    completed_pages = checkpoint.get("completed_pages", 0)
    if not isinstance(completed_pages, int) or completed_pages < 0:
        raise StorageError(
            "PDF checkpoint has an invalid completed-page count",
            code="preparation_checkpoint_invalid",
        )
    if checkpoint and (
        checkpoint.get("source_sha256") != source["sha256"]
        or checkpoint.get("configuration_sha256") != fingerprint
    ):
        raise StorageError(
            "PDF checkpoint does not match the source and converter configuration",
            code="preparation_checkpoint_stale",
        )
    derivatives = (
        _resume_derivatives(library, work_id, source["sha256"], checkpoint)
        if checkpoint
        else []
    )
    markdown: list[str] = []
    ocr_pages = sorted(
        page
        for item in derivatives
        if "text_produced_by_ocr" in item.get("warnings", [])
        and (page := _page_number(item, source_id)) is not None
    )
    figure_count = sum(item["role"] == "figure" for item in derivatives)
    seen_images: set[int] = set()
    document = pymupdf.open(source_path)
    if document.needs_pass:
        document.close()
        raise StorageError(
            "Encrypted PDF requires an accessible source copy",
            code="conversion_source_encrypted",
            path=source_id,
        )
    try:
        for index, page in enumerate(document):
            page_number = index + 1
            if page_number <= completed_pages:
                page_text = next(
                    (
                        item
                        for item in derivatives
                        if item["role"]
                        in {"page_text", "supplement_page_text"}
                        and _page_number(item, source_id) == page_number
                    ),
                    None,
                )
                if page_text is None:
                    raise StorageError(
                        "PDF checkpoint lacks text for a completed page",
                        code="preparation_checkpoint_stale",
                        path=f"{source_id}:page:{page_number}",
                    )
                root, _ = open_library(library)
                prepared_text = resolve_library_path(
                    root / "works" / work_id,
                    page_text["path"],
                    must_exist=True,
                ).read_text(encoding="utf-8")
                markdown.extend(
                    [
                        f"<!-- source-page: {page_number} -->",
                        f"## PDF page {page_number}",
                        "",
                        prepared_text.rstrip(),
                        "",
                    ]
                )
                seen_images.update(image[0] for image in page.get_images(full=True))
                continue
            text = page.get_text("text", sort=True)
            page_warnings = [*identity_warnings]
            if not text.strip():
                if ocr_empty_pages:
                    try:
                        text_page = page.get_textpage_ocr(
                            dpi=max(render_dpi, 200),
                            full=True,
                        )
                        text = page.get_text("text", textpage=text_page, sort=True)
                        ocr_pages.append(page_number)
                        page_warnings.append("text_produced_by_ocr")
                    except Exception as error:
                        raise StorageError(
                            "OCR failed; verify that Tesseract is installed",
                            code="conversion_ocr_failed",
                            path=f"{source_id}:page:{page_number}",
                        ) from error
                else:
                    page_warnings.append("page_has_no_extracted_text")
            text_path = temporary / f"page-{page_number:04d}.txt"
            text_path.write_text(text, encoding="utf-8")
            derivatives.append(
                register_derivative(
                    library,
                    work_id,
                    text_path,
                    role=(
                        "supplement_page_text"
                        if source["role"] == "supplement"
                        else "page_text"
                    ),
                    input_sha256=[source["sha256"]],
                    generator_name="PyMuPDF",
                    generator_version=pymupdf.VersionBind,
                    configuration_sha256=fingerprint,
                    media_type="text/plain",
                    quality="ready" if text.strip() else "partial",
                    warnings=page_warnings,
                    locators=[
                        {
                            "artifact_id": source_id,
                            "type": "page",
                            "value": page_number,
                        }
                    ],
                )["derivative"]
            )
            if page.rect.width * page.rect.height * (render_dpi / 72) ** 2 > 40_000_000:
                raise StorageError(
                    "PDF page dimensions exceed the bounded render size",
                    code="conversion_size_limit",
                    path=f"{source_id}:page:{page_number}",
                )
            render_path = temporary / f"page-{page_number:04d}.png"
            page.get_pixmap(dpi=render_dpi, alpha=False).save(render_path)
            derivatives.append(
                register_derivative(
                    library,
                    work_id,
                    render_path,
                    role=(
                        "supplement_page_render"
                        if source["role"] == "supplement"
                        else "page_render"
                    ),
                    input_sha256=[source["sha256"]],
                    generator_name="PyMuPDF",
                    generator_version=pymupdf.VersionBind,
                    configuration_sha256=fingerprint,
                    media_type="image/png",
                    warnings=identity_warnings,
                    locators=[
                        {
                            "artifact_id": source_id,
                            "type": "page",
                            "value": page_number,
                        }
                    ],
                )["derivative"]
            )
            markdown.extend(
                [
                    f"<!-- source-page: {page_number} -->",
                    f"## PDF page {page_number}",
                    "",
                    text.rstrip(),
                    "",
                ]
            )
            for image_index, image in enumerate(page.get_images(full=True), start=1):
                xref = image[0]
                if xref in seen_images:
                    continue
                seen_images.add(xref)
                extracted = document.extract_image(xref)
                figure_path = temporary / (
                    f"page-{page_number:04d}-image-{image_index:03d}."
                    f"{extracted.get('ext', 'bin')}"
                )
                figure_path.write_bytes(extracted["image"])
                caption = next(
                    (
                        line.strip()
                        for line in text.splitlines()
                        if re.match(
                            r"^(?:fig(?:ure)?|scheme)\s*\d+",
                            line.strip(),
                            re.I,
                        )
                    ),
                    None,
                )
                derivatives.append(
                    register_derivative(
                        library,
                        work_id,
                        figure_path,
                        role="figure",
                        input_sha256=[source["sha256"]],
                        generator_name="PyMuPDF",
                        generator_version=pymupdf.VersionBind,
                        configuration_sha256=fingerprint,
                        quality="partial",
                        warnings=[
                            *identity_warnings,
                            "embedded_image_extraction_is_not_figure_segmentation",
                            *(
                                [f"caption_candidate:{caption[:300]}"]
                                if caption
                                else ["caption_not_identified"]
                            ),
                        ],
                        locators=[
                            {
                                "artifact_id": source_id,
                                "type": "page",
                                "value": page_number,
                            },
                            {
                                "artifact_id": source_id,
                                "type": "figure",
                                "value": f"embedded-image-{image_index}",
                            },
                        ],
                    )["derivative"]
                )
                figure_count += 1
            update_job(
                library,
                job_id,
                status="running",
                checkpoint={
                    "source_sha256": source["sha256"],
                    "configuration_sha256": fingerprint,
                    "completed_pages": page_number,
                    "total_pages": len(document),
                    "derivative_ids": [item["id"] for item in derivatives],
                },
            )
    finally:
        document.close()

    article_path = temporary / "article.md"
    article_path.write_text("\n".join(markdown).rstrip() + "\n", encoding="utf-8")
    derivatives.append(
        register_derivative(
            library,
            work_id,
            article_path,
            role=(
                "supplement_source_faithful_markdown"
                if source["role"] == "supplement"
                else "source_faithful_markdown"
            ),
            input_sha256=[source["sha256"]],
            generator_name="PyMuPDF",
            generator_version=pymupdf.VersionBind,
            configuration_sha256=fingerprint,
            media_type="text/markdown",
            quality="ready" if markdown else "partial",
            warnings=[
                *identity_warnings,
                "pdf_text_extraction_preserves_page_anchors_not_visual_layout",
            ],
            locators=[
                {
                    "artifact_id": source_id,
                    "type": "whole_artifact",
                    "value": "all extracted pages",
                }
            ],
        )["derivative"]
    )
    return derivatives, {
        "pages": len(markdown) // 5,
        "ocr_pages": ocr_pages,
        "figures_extracted": figure_count,
        "configuration_sha256": fingerprint,
        "generator": {"name": "PyMuPDF", "version": pymupdf.VersionBind},
    }


def _archive_derivatives(
    library: str | Path,
    work_id: str,
    source_id: str,
    source: dict[str, Any],
    source_path: Path,
    temporary: Path,
    *,
    job_id: str,
    identity_warnings: list[str],
    resume_checkpoint: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configuration = {
        "maximum_members": 200,
        "maximum_expanded_bytes": 500_000_000,
        "symlinks_allowed": False,
    }
    fingerprint = _configuration_sha256(configuration)
    checkpoint = resume_checkpoint or {}
    completed_members = checkpoint.get("completed_members", 0)
    if not isinstance(completed_members, int) or completed_members < 0:
        raise StorageError(
            "Archive checkpoint has an invalid completed-member count",
            code="preparation_checkpoint_invalid",
        )
    if checkpoint and (
        checkpoint.get("source_sha256") != source["sha256"]
        or checkpoint.get("configuration_sha256") != fingerprint
    ):
        raise StorageError(
            "Archive checkpoint does not match the source and converter configuration",
            code="preparation_checkpoint_stale",
        )
    derivatives = (
        _resume_derivatives(library, work_id, source["sha256"], checkpoint)
        if checkpoint
        else []
    )
    with zipfile.ZipFile(source_path) as archive:
        members = archive.infolist()
        if len(members) > configuration["maximum_members"]:
            raise StorageError(
                "Archive exceeds the bounded member count",
                code="conversion_size_limit",
                path=source_id,
            )
        if sum(member.file_size for member in members) > configuration[
            "maximum_expanded_bytes"
        ]:
            raise StorageError(
                "Archive exceeds the bounded expanded size",
                code="conversion_size_limit",
                path=source_id,
            )
        files = 0
        for member in members:
            relative = Path(member.filename)
            mode = member.external_attr >> 16
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in member.filename
                or (mode & 0o170000) == 0o120000
            ):
                raise StorageError(
                    "Archive contains an unsafe path or symlink",
                    code="conversion_archive_unsafe",
                    path=member.filename,
                )
            if member.is_dir():
                continue
            files += 1
            if files <= completed_members:
                continue
            suffix = relative.suffix.lower()
            output = temporary / f"member-{files:04d}{suffix}"
            output.write_bytes(archive.read(member))
            derivatives.append(
                register_derivative(
                    library,
                    work_id,
                    output,
                    role="supplement_extracted",
                    input_sha256=[source["sha256"]],
                    generator_name="Python zipfile",
                    generator_version=GENERATOR_VERSION,
                    configuration_sha256=fingerprint,
                    quality="ready",
                    warnings=[
                        *identity_warnings,
                        f"archive_member:{relative.as_posix()[:500]}",
                    ],
                    locators=[
                        {
                            "artifact_id": source_id,
                            "type": "supplement",
                            "value": relative.as_posix(),
                        }
                    ],
                )["derivative"]
            )
            update_job(
                library,
                job_id,
                status="running",
                checkpoint={
                    "source_sha256": source["sha256"],
                    "configuration_sha256": fingerprint,
                    "completed_members": files,
                    "total_members": sum(not item.is_dir() for item in members),
                    "derivative_ids": [item["id"] for item in derivatives],
                },
            )
    return derivatives, {
        "members_extracted": files,
        "configuration_sha256": fingerprint,
        "generator": {"name": "Python zipfile", "version": GENERATOR_VERSION},
    }


def _source_for_preparation(
    library: str | Path,
    work_id: str,
    source_id: str,
) -> tuple[Path, dict[str, Any]]:
    work = get_work(library, work_id)
    source = next(
        (candidate for candidate in work["sources"] if candidate["id"] == source_id),
        None,
    )
    if source is None:
        raise StorageError(
            "Source does not exist on this work",
            code="source_not_found",
            path=source_id,
        )
    root, _ = open_library(library)
    source_path = resolve_library_path(
        root / "works" / work_id,
        source["path"],
        must_exist=True,
    )
    if not source_path.is_file():
        raise StorageError(
            "Source path is not a regular file",
            code="source_not_found",
            path=str(source_path),
        )
    return source_path, source


def prepare_source(
    library: str | Path,
    work_id: str,
    source_id: str,
    *,
    render_dpi: int = 200,
    ocr_empty_pages: bool = False,
    requested_by: dict[str, str] | None = None,
    resume_job_id: str | None = None,
) -> dict[str, Any]:
    """Prepare deterministic navigation derivatives from one exact source."""

    if render_dpi < 72 or render_dpi > 600:
        raise StorageError(
            "Render DPI must be between 72 and 600",
            code="render_dpi_invalid",
        )
    source_path, source = _source_for_preparation(library, work_id, source_id)
    arguments = {
        "work_id": work_id,
        "source_id": source_id,
        "source_sha256": source["sha256"],
        "render_dpi": render_dpi,
        "ocr_empty_pages": ocr_empty_pages,
    }
    if resume_job_id is None:
        job = create_job(
            library,
            operation="libraryos.source.prepare",
            arguments=arguments,
            requested_by=requested_by or {"kind": "human", "id": "local-user"},
            capabilities=["source.prepare"],
        )
    else:
        job = get_job(library, resume_job_id)
        if (
            job["operation"] != "libraryos.source.prepare"
            or job["arguments"] != arguments
            or job["status"] != "queued"
        ):
            raise StorageError(
                "Only a retried preparation job with identical arguments can resume",
                code="preparation_resume_invalid",
                path=resume_job_id,
            )
    update_job(library, job["id"], status="running")
    resume_checkpoint = job.get("checkpoint") if resume_job_id is not None else None
    derivatives: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="libraryos-prepare-") as temporary_name:
            temporary = Path(temporary_name)
            media_type = source["media_type"].casefold()
            identity_warnings = (
                ["source_identity_unverified"]
                if source["identity_status"] != "verified"
                else []
            )
            if media_type == "application/pdf":
                derivatives, details = _pdf_derivatives(
                    library,
                    work_id,
                    source_id,
                    source,
                    source_path,
                    temporary,
                    render_dpi=render_dpi,
                    ocr_empty_pages=ocr_empty_pages,
                    job_id=job["id"],
                    identity_warnings=identity_warnings,
                    resume_checkpoint=resume_checkpoint,
                )
            elif media_type in {
                "application/zip",
                "application/x-zip-compressed",
            }:
                if not zipfile.is_zipfile(source_path):
                    raise StorageError(
                        "Source is not a valid ZIP archive",
                        code="conversion_failure",
                        path=source_id,
                    )
                derivatives, details = _archive_derivatives(
                    library,
                    work_id,
                    source_id,
                    source,
                    source_path,
                    temporary,
                    job_id=job["id"],
                    identity_warnings=identity_warnings,
                    resume_checkpoint=resume_checkpoint,
                )
            elif (
                media_type.startswith("text/")
                or media_type in {"application/xml", "application/xhtml+xml"}
            ):
                markdown, warnings, source_locators = _text_markdown(
                    source_path, media_type
                )
                output = temporary / "article.md"
                output.write_text(markdown, encoding="utf-8")
                configuration = {
                    "media_type": media_type,
                    "parser": "stdlib-conservative",
                }
                derivatives.append(
                    register_derivative(
                        library,
                        work_id,
                        output,
                        role="source_faithful_markdown",
                        input_sha256=[source["sha256"]],
                        generator_name=GENERATOR_NAME,
                        generator_version=GENERATOR_VERSION,
                        configuration_sha256=_configuration_sha256(configuration),
                        media_type="text/markdown",
                        warnings=[*warnings, *identity_warnings],
                        locators=[
                            {
                                "artifact_id": source_id,
                                "type": "whole_artifact",
                                "value": "entire source",
                            },
                            *[
                                {"artifact_id": source_id, **locator}
                                for locator in source_locators
                            ],
                        ],
                    )["derivative"]
                )
                details = {
                    "configuration_sha256": _configuration_sha256(configuration),
                    "generator": {
                        "name": GENERATOR_NAME,
                        "version": GENERATOR_VERSION,
                    },
                }
            else:
                raise StorageError(
                    "No deterministic preparation route supports this source type",
                    code="conversion_unsupported",
                    path=media_type,
                )
        result = {
            "work_id": work_id,
            "source_id": source_id,
            "source_sha256": source["sha256"],
            "derivatives": derivatives,
            "preparation": details,
            "scientific_inspection": "not_performed",
        }
        append_attempt(
            library,
            job["id"],
            status="succeeded",
            provider=GENERATOR_NAME,
        )
        update_job(library, job["id"], status="succeeded", result=result)
        return {**result, "job_id": job["id"]}
    except (OSError, StorageError) as error:
        code = getattr(error, "code", "conversion_failure")
        if not isinstance(code, str):
            code = "conversion_failure"
        append_attempt(
            library,
            job["id"],
            status="failed",
            provider=GENERATOR_NAME,
            diagnostic=f"Preparation failed: {code}",
            error_code=code,
        )
        append_exception(
            library,
            job["id"],
            category=code,
            message=f"Preparation failed: {code}",
        )
        update_job(library, job["id"], status="failed")
        raise
