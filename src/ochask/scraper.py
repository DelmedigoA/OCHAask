from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import csv
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import sys
import tempfile
from threading import Lock
from time import monotonic, sleep
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

import pymupdf

from .models import MainImage, ReportAttachment, ReportLanding


class _Node:
    def __init__(self, tag: str, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Node | str] = []
        self.parent: _Node | None = None

    def text(self) -> str:
        return "".join(child if isinstance(child, str) else child.text() for child in self.children)

    def find_all(self, tag: str | None = None, class_name: str | None = None) -> list[_Node]:
        found: list[_Node] = []
        for child in self.children:
            if not isinstance(child, _Node):
                continue
            classes = child.attrs.get("class", "").split()
            if (tag is None or child.tag == tag) and (class_name is None or class_name in classes):
                found.append(child)
            found.extend(child.find_all(tag, class_name))
        return found

    def first(self, tag: str | None = None, class_name: str | None = None) -> _Node | None:
        matches = self.find_all(tag, class_name)
        return matches[0] if matches else None


class _PageParser(HTMLParser):
    _void_tags = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        node.parent = self.stack[-1]
        node.parent.children.append(node)
        if tag not in self._void_tags:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].children.append(data)


def _clean(node: _Node | None) -> str:
    return " ".join(node.text().split()) if node else ""


def _labels(container: _Node | None) -> list[str]:
    if container is None:
        return []
    return [
        _clean(link)
        for link in container.find_all("a")
        if link.attrs.get("href") and _clean(link)
    ]


def _details(dl: _Node | None) -> dict[str, _Node]:
    values: dict[str, _Node] = {}
    if dl is None:
        return values
    children = [child for child in dl.children if isinstance(child, _Node)]
    for index, child in enumerate(children[:-1]):
        if child.tag == "dt" and children[index + 1].tag == "dd":
            values[_clean(child).lower()] = children[index + 1]
    return values


def _date_from_time(node: _Node | None) -> date | None:
    time_node = node.first("time") if node else None
    value = time_node.attrs.get("datetime", "") if time_node else ""
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _main_image(root: _Node, page_url: str) -> MainImage | None:
    image_node = root.first("figure", "rw-entity-image")
    if image_node is None:
        return None
    image_url = None
    for source in image_node.find_all("source"):
        srcset = source.attrs.get("srcset", "")
        if "/large/" in srcset and ".webp" not in srcset:
            image_url = srcset.split()[0]
            break
    if image_url is None:
        img = image_node.first("img")
        image_url = img.attrs.get("src") if img else None
    if not image_url:
        return None
    credit = _clean(image_node.first("small")) or None
    return MainImage(url=urljoin(page_url, image_url), credit=credit)


def _key_points(root: _Node) -> list[str]:
    content = root.first("div", "rw-report__content")
    if content is None:
        return []
    aliases = {"key points", "key highlights", "highlights"}
    for heading in (node for node in content.find_all() if node.tag in {"strong", "h2", "h3"}):
        label = _clean(heading).casefold().rstrip(":* ").strip()
        if label not in aliases:
            continue
        wrapper = heading.parent if heading.tag == "strong" else heading
        siblings = wrapper.parent.children if wrapper and wrapper.parent else []
        start = siblings.index(wrapper) + 1 if wrapper in siblings else len(siblings)
        listing = next(
            (node for node in siblings[start:] if isinstance(node, _Node) and node.tag == "ul"),
            None,
        )
        return [_clean(item) for item in listing.find_all("li") if _clean(item)] if listing else []
    return []


def _attachments(root: _Node, page_url: str) -> list[ReportAttachment]:
    section = root.first("section", "rw-attachment--report")
    if section is None:
        return []
    attachments = []
    description_pattern = re.compile(
        r"\(([^|)]+)\|\s*([0-9.]+\s*(?:KB|MB|GB|B))(?:\s*\|\s*([^)]*))?\)",
        flags=re.IGNORECASE,
    )
    for item in section.find_all("li"):
        link = item.first("a")
        if link is None or not link.attrs.get("href"):
            continue
        preview = item.first("img", "rw-file-preview")
        description = _clean(item.first("span", "rw-file__description")) or None
        parts = description_pattern.search(description or "")
        filename = unquote(PurePosixPath(link.attrs["href"]).name)
        language = parts.group(3).strip() if parts and parts.group(3) else None
        if language:
            language = re.sub(r"\s+version\s*$", "", language, flags=re.IGNORECASE).strip() or None
        attachments.append(ReportAttachment(
            filename=filename,
            url=urljoin(page_url, link.attrs["href"]),
            description=description,
            file_type=parts.group(1).strip().lower() if parts else None,
            size=parts.group(2).strip() if parts else None,
            language=language,
            preview_url=urljoin(page_url, preview.attrs["src"])
            if preview and preview.attrs.get("src") else None,
        ))
    return attachments


def parse_landing_page(html: str, landing_url: str) -> ReportLanding:
    """Parse ReliefWeb landing-page HTML into its structured metadata."""
    parser = _PageParser()
    parser.feed(html)
    root = parser.root
    page_url = landing_url

    title_node = root.first("h1", "rw-article__title")
    if title_node is None:
        raise ValueError("Could not find ReliefWeb report title")

    header = root.first("dl", "rw-entity-meta--core")
    header_fields = _details(header)
    details_section = root.first("section", "rw-entity-details")
    detail_fields = _details(details_section.first("dl") if details_section else None)

    def one_link(fields: dict[str, _Node], label: str) -> str | None:
        values = _labels(fields.get(label))
        return values[0] if values else None

    def linked(fields: dict[str, _Node], label: str) -> list[str]:
        return _labels(fields.get(label))

    origin_field = header_fields.get("origin")
    origin_anchor = origin_field.first("a") if origin_field else None
    origin_url = urljoin(page_url, origin_anchor.attrs["href"]) if origin_anchor else None

    return ReportLanding(
        id=uuid5(NAMESPACE_URL, page_url),
        landing_url=page_url,
        title=_clean(title_node),
        report_type=_clean(header_fields.get("format")) or None,
        posted=_date_from_time(header_fields.get("posted")),
        originally_published=_date_from_time(header_fields.get("originally published")),
        origin_url=origin_url,
        main_image=_main_image(root, page_url),
        key_points=_key_points(root),
        primary_country=one_link(detail_fields, "primary country"),
        other_countries=linked(detail_fields, "other country"),
        sources=linked(detail_fields, "source"),
        formats=linked(detail_fields, "format"),
        themes=linked(detail_fields, "themes"),
        attachments=_attachments(root, page_url),
    )


def _fetch_page(url: str) -> ReportLanding:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ochask/0.1)"})
    with urlopen(request, timeout=30) as response:
        page_url = response.geturl()
        html = response.read().decode("utf-8", errors="replace")
    return parse_landing_page(html, page_url)


def get_landing_page(url: str) -> ReportLanding:
    """Fetch and parse one ReliefWeb landing page."""
    return _fetch_page(url)


DEFAULT_CSV_PATH = Path("data/ocha_situation_reports_urls.csv")
DEFAULT_OUTPUT_PATH = Path("data/scraped/landing_pages.jsonl")
DEFAULT_LANDING_PAGES_PATH = DEFAULT_OUTPUT_PATH
DEFAULT_PDF_DIRECTORY = Path("data/pdfs")
DEFAULT_DOWNLOAD_REPORT = Path("data/scraped/pdf_download_report.csv")
DEFAULT_DOWNLOAD_WORKERS = 2
DEFAULT_REQUEST_INTERVAL_SECONDS = 2.0
MAX_DOWNLOAD_WORKERS = 50
MAX_RATE_LIMIT_RETRIES = 2
SAMPLE_SEED = 2026


class _DownloadPacer:
    """Space PDF request starts and pause all workers after a rate-limit response."""

    def __init__(self, interval: float):
        self._interval = interval
        self._lock = Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0

    def set_interval(self, interval: float) -> None:
        with self._lock:
            self._interval = interval

    def wait(self) -> None:
        with self._lock:
            now = monotonic()
            wait_until = max(self._next_request_at, self._blocked_until)
            if wait_until > now:
                sleep(wait_until - now)
            self._next_request_at = monotonic() + self._interval

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, monotonic() + seconds)


_DOWNLOAD_PACER = _DownloadPacer(DEFAULT_REQUEST_INTERVAL_SECONDS)


@dataclass(frozen=True)
class ScrapeFailure:
    url: str
    error: str


@dataclass(frozen=True)
class PipelineResult:
    records: tuple[ReportLanding, ...]
    failures: tuple[ScrapeFailure, ...]
    output_path: Path


def read_unique_landing_urls(csv_path: str | Path = DEFAULT_CSV_PATH) -> list[str]:
    """Read unique, non-empty landing URLs from the original reports CSV."""
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
        urls = []
        seen = set()
        for row in csv.DictReader(csv_file):
            url = (row.get("landing_url") or "").strip()
            if url and url not in seen:
                urls.append(url)
                seen.add(url)
    return urls


def scrape_csv(
    csv_path: str | Path = DEFAULT_CSV_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    limit: int | None = None,
    seed: int = SAMPLE_SEED,
    fetch_page: Callable[[str], ReportLanding] | None = None,
    progress: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Scrape a deterministic sample of unique CSV landing URLs to JSONL.

    Fetch or parse failures are collected while the remaining URLs are processed.
    The fetcher and progress callback can be injected for deterministic tests.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1 when provided")
    unique_urls = read_unique_landing_urls(csv_path)
    urls = (
        random.Random(seed).sample(unique_urls, k=min(limit, len(unique_urls)))
        if limit is not None
        else unique_urls
    )
    fetch = fetch_page or get_landing_page
    records: list[ReportLanding] = []
    failures: list[ScrapeFailure] = []

    for index, url in enumerate(urls, start=1):
        try:
            records.append(fetch(url))
        except Exception as exc:
            failures.append(ScrapeFailure(url=url, error=f"{type(exc).__name__}: {exc}"))
        if progress:
            progress(f"[{index}/{len(urls)}] scraped={len(records)} failed={len(failures)}")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = "".join(record.model_dump_json() + "\n" for record in records)
    destination.write_text(serialized, encoding="utf-8")
    return PipelineResult(tuple(records), tuple(failures), destination)


def _is_pdf(attachment: ReportAttachment) -> bool:
    return (
        (attachment.file_type or "").casefold() == "pdf"
        or PurePosixPath(attachment.filename).suffix.casefold() == ".pdf"
        or urlsplit(str(attachment.url)).path.casefold().endswith(".pdf")
    )


def _filename_language(attachment: ReportAttachment) -> str | None:
    stem = PurePosixPath(attachment.filename).stem
    if re.search(r"(?<![a-z])(?:english|eng|en)(?![a-z])", stem, re.IGNORECASE):
        return "english"
    if re.search(r"(?<![a-z])(?:arabic|ara|ar|hebrew|heb|he)(?![a-z])", stem, re.IGNORECASE):
        return "other"
    return None


def _language_class(attachment: ReportAttachment) -> tuple[str, str]:
    language = (attachment.language or "").strip().casefold()
    if language:
        if "english" in language or language in {"en", "eng"}:
            return "english", "attachment language metadata"
        return "other", "attachment language metadata"
    filename_language = _filename_language(attachment)
    if filename_language == "english":
        return "english", "filename language label"
    if filename_language == "other":
        return "other", "filename language label"
    return "unknown", "language not specified"


def classify_language_text(text: str, *, minimum_letters: int = 100) -> str | None:
    """Classify English, Hebrew, or Arabic from Unicode letters in extracted text."""
    counts = {"English": 0, "Hebrew": 0, "Arabic": 0}
    for char in text:
        codepoint = ord(char)
        if "A" <= char <= "Z" or "a" <= char <= "z":
            counts["English"] += 1
        elif char.isalpha() and (
            0x0590 <= codepoint <= 0x05FF or 0xFB1D <= codepoint <= 0xFB4F
        ):
            counts["Hebrew"] += 1
        elif char.isalpha() and (
            0x0600 <= codepoint <= 0x06FF
            or 0x0750 <= codepoint <= 0x077F
            or 0x08A0 <= codepoint <= 0x08FF
            or 0xFB50 <= codepoint <= 0xFDFF
            or 0xFE70 <= codepoint <= 0xFEFF
        ):
            counts["Arabic"] += 1
    total = sum(counts.values())
    if total < minimum_letters:
        return None
    language, count = max(counts.items(), key=lambda item: item[1])
    return language if count / total >= 0.65 else None


def detect_pdf_language(pdf_path: str | Path, *, max_pages: int = 5) -> str | None:
    """Classify PDF text without OCR; return None for short, mixed, or unextractable text."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    with pymupdf.open(pdf_path) as document:
        text = "\n".join(
            document.load_page(page_number).get_text("text")
            for page_number in range(min(document.page_count, max_pages))
        )
    return classify_language_text(text)


def select_english_pdf(
    attachments: tuple[ReportAttachment, ...] | list[ReportAttachment],
) -> tuple[ReportAttachment | None, str | None, list[ReportAttachment], list[ReportAttachment]]:
    """Pick an English PDF identified by attachment metadata or its filename."""
    pdfs = [attachment for attachment in attachments if _is_pdf(attachment)]
    classified = [(attachment, *_language_class(attachment)) for attachment in pdfs]
    english = [(attachment, basis) for attachment, kind, basis in classified if kind == "english"]
    selected: ReportAttachment | None = None
    selection_basis: str | None = None
    if english:
        selected, selection_basis = english[0]
    other_pdfs = [attachment for attachment, kind, _ in classified if kind == "other"]
    return selected, selection_basis, pdfs, other_pdfs


def _pdf_destination(landing: ReportLanding, attachment: ReportAttachment, directory: Path) -> Path:
    original_name = PurePosixPath(attachment.filename).name or "report.pdf"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "report.pdf"
    if not safe_name.casefold().endswith(".pdf"):
        safe_name += ".pdf"
    return directory / f"{landing.id}_{safe_name}"


def _is_valid_pdf(path: str | Path) -> bool:
    try:
        with Path(path).open("rb") as pdf_file:
            return pdf_file.read(5) == b"%PDF-"
    except OSError:
        return False


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _download_pdf(landing: ReportLanding, attachment: ReportAttachment, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = _pdf_destination(landing, attachment, directory)
    if _is_valid_pdf(destination):
        return destination
    temporary = destination.with_name(destination.name + ".part")
    request = Request(str(attachment.url), headers={"User-Agent": "Mozilla/5.0 (compatible; ochask/0.1)"})
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        _DOWNLOAD_PACER.wait()
        try:
            with urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                signature = response.read(5)
                if signature != b"%PDF-":
                    raise ValueError("response does not begin with a PDF signature")
                output.write(signature)
                shutil.copyfileobj(response, output)
            temporary.replace(destination)
            return destination
        except HTTPError as exc:
            temporary.unlink(missing_ok=True)
            if exc.code not in {429, 503}:
                raise
            delay = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
            if delay is None:
                delay = min(60.0 * (2 ** attempt), 300.0)
            _DOWNLOAD_PACER.defer(delay)
            print(
                f"HTTP {exc.code} downloading {attachment.url}; pausing all workers for "
                f"{delay:.1f}s (retry {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})",
                file=sys.stderr,
                flush=True,
            )
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _prepare_landing_record(
    landing: ReportLanding,
    directory: Path,
    temporary_root: Path,
    downloader: Callable[[ReportLanding, ReportAttachment, Path], Path],
) -> tuple[dict[str, str], list[tuple[ReportAttachment, Path | None, str]]]:
    selected, basis, pdfs, other_pdfs = select_english_pdf(landing.attachments)
    row = {
        "landing_id": str(landing.id),
        "title": landing.title,
        "landing_url": str(landing.landing_url),
        "status": "",
        "pdf_count": str(len(pdfs)),
        "selected_filename": selected.filename if selected else "",
        "selected_language": "English" if selected else "",
        "selection_basis": basis or "",
        "selected_url": str(selected.url) if selected else "",
        "local_path": "",
        "other_language_pdfs": "; ".join(
            f"{attachment.filename} ({attachment.language or 'language inferred from filename'})"
            for attachment in other_pdfs
        ),
        "ambiguous_pdfs": "",
        "error": "",
    }
    if not pdfs:
        row["status"] = "no_pdf"
        row["selection_basis"] = "no PDF attachment listed"
        return row, []
    if selected:
        try:
            row["local_path"] = str(downloader(landing, selected, directory))
            row["status"] = "downloaded"
        except Exception as exc:
            row["status"] = "download_failed"
            row["error"] = f"{type(exc).__name__}: {exc}"

    unknown_pdfs = [attachment for attachment in pdfs if _language_class(attachment)[0] == "unknown"]
    if not unknown_pdfs:
        if not selected:
            row["status"] = "no_english_pdf"
        return row, []
    candidates: list[tuple[ReportAttachment, Path | None, str]] = []
    for index, attachment in enumerate(unknown_pdfs):
        candidate_directory = temporary_root / str(landing.id) / str(index)
        try:
            candidate_path = downloader(landing, attachment, candidate_directory)
            candidates.append((attachment, candidate_path, ""))
        except Exception as exc:
            candidates.append((attachment, None, f"{type(exc).__name__}: {exc}"))
    if not selected:
        row["status"] = "pending_language_check"
    return row, candidates


def _resolve_ambiguous_landing_record(
    landing: ReportLanding,
    row: dict[str, str],
    candidates: list[tuple[ReportAttachment, Path | None, str]],
    directory: Path,
) -> dict[str, str]:
    other_languages: list[str] = []
    ambiguous: list[str] = []
    errors: list[str] = []
    inferred_selection: ReportAttachment | None = None
    errors.extend(filter(None, [row["error"]]))

    # PyMuPDF access stays in this coordinator thread; PyMuPDF is not thread-safe.
    for attachment, candidate_path, download_error in candidates:
        if candidate_path is None:
            errors.append(f"{attachment.filename}: {download_error}")
            continue
        try:
            language = detect_pdf_language(candidate_path)
        except Exception as exc:
            errors.append(f"{attachment.filename}: {type(exc).__name__}: {exc}")
            continue
        if language == "English":
            if not row["local_path"] and inferred_selection is None:
                inferred_selection = attachment
                destination = _pdf_destination(landing, attachment, directory)
                directory.mkdir(parents=True, exist_ok=True)
                candidate_path.replace(destination)
                row["local_path"] = str(destination)
        elif language in {"Hebrew", "Arabic"}:
            other_languages.append(f"{attachment.filename} (PyMuPDF text: {language})")
        else:
            ambiguous.append(f"{attachment.filename} (text insufficient, mixed, or unavailable)")

    row["other_language_pdfs"] = "; ".join(
        filter(None, [row["other_language_pdfs"], *other_languages])
    )
    row["ambiguous_pdfs"] = "; ".join(ambiguous)
    row["error"] = "; ".join(errors)
    if inferred_selection:
        row["selected_filename"] = inferred_selection.filename
        row["selected_language"] = "English"
        row["selection_basis"] = "PyMuPDF text extraction: English"
        row["selected_url"] = str(inferred_selection.url)
    if row["local_path"]:
        row["status"] = "downloaded"
    elif errors:
        row["status"] = "download_failed"
    elif ambiguous:
        row["status"] = "language_ambiguous"
    else:
        row["status"] = "no_english_pdf"
    return row


@dataclass(frozen=True)
class PDFDownloadResult:
    report_path: Path
    pdf_directory: Path
    total: int
    downloaded: int
    reused: int
    no_english_pdf: int
    no_pdf: int
    language_ambiguous: int
    failed: int


def download_english_pdfs(
    landing_pages_path: str | Path = DEFAULT_LANDING_PAGES_PATH,
    pdf_directory: str | Path = DEFAULT_PDF_DIRECTORY,
    report_path: str | Path = DEFAULT_DOWNLOAD_REPORT,
    *,
    workers: int = DEFAULT_DOWNLOAD_WORKERS,
    request_interval: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    downloader: Callable[[ReportLanding, ReportAttachment, Path], Path] = _download_pdf,
    progress: Callable[[str], None] | None = None,
) -> PDFDownloadResult:
    """Download one English PDF per landing page using at most 50 concurrent workers."""
    if not 1 <= workers <= MAX_DOWNLOAD_WORKERS:
        raise ValueError("workers must be between 1 and 50")
    if request_interval <= 0:
        raise ValueError("request_interval must be greater than zero")
    _DOWNLOAD_PACER.set_interval(request_interval)
    landing_pages = [
        ReportLanding.model_validate_json(line)
        for line in Path(landing_pages_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    directory = Path(pdf_directory)
    rows: list[dict[str, str] | None] = [None] * len(landing_pages)
    counts = {
        "downloaded": 0,
        "no_english_pdf": 0,
        "no_pdf": 0,
        "language_ambiguous": 0,
        "download_failed": 0,
    }
    reused = 0
    destination_report = Path(report_path)
    previous_rows: dict[str, dict[str, str]] = {}
    if destination_report.exists():
        try:
            with destination_report.open(encoding="utf-8", newline="") as previous_file:
                previous_rows = {
                    row.get("landing_id", ""): row
                    for row in csv.DictReader(previous_file)
                    if row.get("landing_id")
                }
        except (OSError, csv.Error):
            previous_rows = {}

    with tempfile.TemporaryDirectory(prefix="ochask-language-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            completed = 0
            for index, landing in enumerate(landing_pages):
                previous = previous_rows.get(str(landing.id))
                previous_path = previous.get("local_path", "") if previous else ""
                if previous and previous.get("status") == "downloaded" and _is_valid_pdf(previous_path):
                    rows[index] = previous
                    counts["downloaded"] += 1
                    reused += 1
                    completed += 1
                    continue
                future = pool.submit(
                    _prepare_landing_record,
                    landing,
                    directory,
                    temporary_root,
                    downloader,
                )
                futures[future] = index
            if progress and completed:
                progress(
                    f"[{completed}/{len(landing_pages)}] downloaded={counts['downloaded']} reused={reused} "
                    f"no_english_pdf={counts['no_english_pdf']} no_pdf={counts['no_pdf']} "
                    f"language_ambiguous={counts['language_ambiguous']} failed={counts['download_failed']}"
                )
            for future in as_completed(futures):
                index = futures[future]
                row, candidates = future.result()
                if candidates:
                    row = _resolve_ambiguous_landing_record(
                        landing_pages[index], row, candidates, directory
                    )
                rows[index] = row
                counts[row["status"]] += 1
                completed += 1
                if progress:
                    progress(
                        f"[{completed}/{len(landing_pages)}] downloaded={counts['downloaded']} "
                        f"reused={reused} no_english_pdf={counts['no_english_pdf']} no_pdf={counts['no_pdf']} "
                        f"language_ambiguous={counts['language_ambiguous']} failed={counts['download_failed']}"
                    )

    destination_report.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "landing_id", "title", "landing_url", "status", "pdf_count",
        "selected_filename", "selected_language", "selection_basis", "selected_url",
        "local_path", "other_language_pdfs", "ambiguous_pdfs", "error",
    ]
    with destination_report.open("w", encoding="utf-8", newline="") as report_file:
        writer = csv.DictWriter(report_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(row for row in rows if row is not None)
    return PDFDownloadResult(
        report_path=destination_report,
        pdf_directory=directory,
        total=len(landing_pages),
        downloaded=counts["downloaded"],
        reused=reused,
        no_english_pdf=counts["no_english_pdf"],
        no_pdf=counts["no_pdf"],
        language_ambiguous=counts["language_ambiguous"],
        failed=counts["download_failed"],
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Scrape unique ReliefWeb landing pages to JSONL.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH, help="original reports CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSONL output file (replaced)")
    parser.add_argument("--limit", type=int, help="optionally limit the run to a deterministic random sample")
    parser.add_argument("--download-pdfs", action="store_true", help="download English PDFs from an existing landing-page JSONL file")
    parser.add_argument("--landing-pages", type=Path, default=DEFAULT_LANDING_PAGES_PATH, help="scraped landing-page JSONL input")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIRECTORY, help="directory for downloaded PDFs")
    parser.add_argument("--report", type=Path, default=DEFAULT_DOWNLOAD_REPORT, help="CSV report for PDF selection and download status")
    parser.add_argument("--workers", type=int, default=DEFAULT_DOWNLOAD_WORKERS, help="concurrent PDF downloads (1-50; default: 2)")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
        help="minimum seconds between PDF request starts (default: 2.0)",
    )
    args = parser.parse_args(argv)

    if args.download_pdfs:
        result = download_english_pdfs(
            args.landing_pages,
            args.pdf_dir,
            args.report,
            workers=args.workers,
            request_interval=args.request_interval,
            progress=lambda message: print(message, flush=True),
        )
        print(
            f"PDFs available: {result.downloaded}/{result.total} ({result.reused} reused); "
            f"no English PDF: {result.no_english_pdf}; no PDF: {result.no_pdf}; "
            f"language ambiguous: {result.language_ambiguous}; download failed: {result.failed}"
        )
        print(f"PDF directory: {result.pdf_directory}")
        print(f"Report: {result.report_path}")
        return 1 if result.failed else 0

    result = scrape_csv(
        args.csv,
        args.output,
        limit=args.limit,
        progress=lambda message: print(message, flush=True),
    )
    print(f"Wrote {len(result.records)} records to {result.output_path}")
    for failure in result.failures:
        print(f"Failed: {failure.url}: {failure.error}", file=sys.stderr)
    print(f"Failures: {len(result.failures)}")
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
