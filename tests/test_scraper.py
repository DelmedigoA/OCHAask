from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from uuid import NAMESPACE_URL, uuid5

import pymupdf

from ochask.models import ReportAttachment, ReportLanding
from ochask.scraper import (
    classify_language_text,
    detect_pdf_language,
    download_english_pdfs,
    parse_landing_page,
    read_unique_landing_urls,
    scrape_csv,
    select_english_pdf,
)


ROOT = Path(__file__).parent
FIXTURE = (ROOT / "fixtures" / "landing_page.html").read_text(encoding="utf-8")
LANDING_URL = "https://reliefweb.int/report/example"


class ParseLandingPageTests(unittest.TestCase):
    def test_parses_metadata_and_attachment_description(self):
        report = parse_landing_page(FIXTURE, LANDING_URL)

        self.assertIsInstance(report, ReportLanding)
        self.assertEqual(report.id, uuid5(NAMESPACE_URL, LANDING_URL))
        self.assertEqual(report.title, "Example situation report")
        self.assertEqual(report.posted.isoformat(), "2023-10-15")
        self.assertEqual(report.originally_published.isoformat(), "2023-10-14")
        self.assertEqual(report.report_type, "Situation Report")
        self.assertEqual(report.key_points, ["First highlight", "Second highlight"])
        self.assertEqual(report.primary_country, "occupied Palestinian territory")
        self.assertEqual(report.themes, ["Health", "Food and Nutrition"])
        self.assertEqual(str(report.origin_url), "https://ochaopt.org/content/example")
        self.assertEqual(len(report.attachments), 1)
        attachment = report.attachments[0]
        self.assertEqual(attachment.filename, "Report Arabic.pdf")
        self.assertEqual(attachment.file_type, "pdf")
        self.assertEqual(attachment.size, "638.16 KB")
        self.assertEqual(attachment.language, "Arabic")
        self.assertEqual(str(attachment.preview_url), "https://reliefweb.int/preview/report.png")

    def test_key_section_aliases_and_absence(self):
        for label in ("Key Points", "Highlights"):
            with self.subTest(label=label):
                html = FIXTURE.replace("Key Highlights:", f"{label}:")
                report = parse_landing_page(html, LANDING_URL)
                self.assertEqual(report.key_points, ["First highlight", "Second highlight"])

        html_without_section = FIXTURE.replace(
            '<p><strong>Key Highlights:</strong></p>\n    <ul><li>First highlight</li><li>Second highlight</li></ul>',
            "",
        )
        report = parse_landing_page(html_without_section, LANDING_URL)
        self.assertEqual(report.key_points, [])

    def test_missing_optional_fields_default_cleanly(self):
        report = parse_landing_page(
            '<h1 class="rw-article__title">Minimal</h1>',
            LANDING_URL,
        )
        self.assertIsNone(report.posted)
        self.assertIsNone(report.main_image)
        self.assertIsNone(report.origin_url)
        self.assertEqual(report.key_points, [])
        self.assertEqual(report.attachments, [])


class PipelineTests(unittest.TestCase):
    def test_reads_unique_urls_from_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "reports.csv"
            csv_path.write_text(
                'id,landing_url\n1,"https://example.com/one"\n2,"https://example.com/one"\n3,""\n4,"https://example.com/two"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                read_unique_landing_urls(csv_path),
                ["https://example.com/one", "https://example.com/two"],
            )

    def test_scrapes_all_unique_urls_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "reports.csv"
            output_path = root / "out.jsonl"
            csv_path.write_text(
                'id,landing_url\n1,"https://example.com/one"\n2,"https://example.com/one"\n3,"https://example.com/two"\n',
                encoding="utf-8",
            )
            seen = []

            def fetch(url: str) -> ReportLanding:
                seen.append(url)
                return parse_landing_page(FIXTURE, url)

            result = scrape_csv(csv_path, output_path, fetch_page=fetch)
            self.assertEqual(len(result.records), 2)
            self.assertEqual(seen, ["https://example.com/one", "https://example.com/two"])

    def test_sample_is_limited_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "reports.csv"
            csv_path.write_text(
                "id,landing_url\n" + "".join(
                    f'{index},"https://example.com/{index}"\n' for index in range(8)
                ),
                encoding="utf-8",
            )
            samples = []
            for run in range(2):
                output_path = root / f"out-{run}.jsonl"
                seen = []

                def fetch(url: str) -> ReportLanding:
                    seen.append(url)
                    return parse_landing_page(FIXTURE, url)

                scrape_csv(
                    csv_path,
                    output_path,
                    limit=3,
                    seed=2026,
                    fetch_page=fetch,
                )
                samples.append(seen)
            self.assertEqual(len(samples[0]), 3)
            self.assertEqual(len(set(samples[0])), 3)
            self.assertEqual(samples[0], samples[1])

    def test_continues_after_failure_and_replaces_jsonl_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "reports.csv"
            output_path = root / "out.jsonl"
            csv_path.write_text(
                'id,landing_url\n1,"https://example.com/one"\n2,"https://example.com/fail"\n3,"https://example.com/three"\n',
                encoding="utf-8",
            )
            output_path.write_text("old record\n", encoding="utf-8")
            progress = []
            def fetch(url: str) -> ReportLanding:
                if url.endswith("/fail"):
                    raise RuntimeError("temporary fetch error")
                return parse_landing_page(FIXTURE, url)

            result = scrape_csv(
                csv_path,
                output_path,
                limit=3,
                fetch_page=fetch,
                seed=4,
                progress=progress.append,
            )

            lines = output_path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            self.assertEqual(len(result.records), 2)
            self.assertEqual(len(result.failures), 1)
            self.assertIn("temporary fetch error", result.failures[0].error)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["title"], "Example situation report")
            self.assertEqual(len(progress), 3)
            self.assertEqual(progress[-1], "[3/3] scraped=2 failed=1")


class PDFDownloadTests(unittest.TestCase):
    def setUp(self):
        self.base_report = parse_landing_page(FIXTURE, LANDING_URL)

    @staticmethod
    def attachment(filename: str, language: str | None) -> ReportAttachment:
        return ReportAttachment(
            filename=filename,
            url=f"https://example.com/{filename.replace(' ', '%20')}",
            file_type="pdf",
            language=language,
        )

    def test_selects_explicit_english_and_preserves_other_languages(self):
        english = self.attachment("Main report.pdf", "English")
        arabic = self.attachment("AR.pdf", "Arabic")
        selected, basis, pdfs, other = select_english_pdf([arabic, english])
        self.assertEqual(selected, english)
        self.assertEqual(basis, "attachment language metadata")
        self.assertEqual(len(pdfs), 2)
        self.assertEqual(other, [arabic])

    def test_unlabeled_attachment_is_left_for_text_detection(self):
        unknown = self.attachment("OPT report.pdf", None)
        selected, basis, _, _ = select_english_pdf([unknown])
        self.assertIsNone(selected)
        self.assertIsNone(basis)

    def test_classifies_english_hebrew_and_arabic_text(self):
        self.assertEqual(classify_language_text("The humanitarian report describes the situation. " * 10), "English")
        self.assertEqual(classify_language_text("הדוח ההומניטרי מתאר את המצב בשטח. " * 10), "Hebrew")
        self.assertEqual(classify_language_text("يصف التقرير الإنساني الوضع على الأرض. " * 10), "Arabic")
        self.assertIsNone(classify_language_text("short mixed English عربي עברית"))

    def test_detects_language_from_embedded_pdf_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "text.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_textbox(
                    pymupdf.Rect(40, 40, 550, 780),
                    "The humanitarian report describes the situation. " * 15,
                    fontsize=12,
                )
                document.save(pdf_path)
            self.assertEqual(detect_pdf_language(pdf_path), "English")

    def test_download_report_records_no_english_and_no_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            english = self.attachment("English.pdf", "English")
            arabic = self.attachment("AR.pdf", "Arabic")
            unknown = self.attachment("OPT.pdf", None)
            reports = [
                self.base_report.model_copy(update={"attachments": [english, arabic]}),
                self.base_report.model_copy(update={"id": uuid5(NAMESPACE_URL, "only-arabic"), "attachments": [arabic]}),
                self.base_report.model_copy(update={"id": uuid5(NAMESPACE_URL, "no-pdf"), "attachments": []}),
                self.base_report.model_copy(update={"id": uuid5(NAMESPACE_URL, "unknown"), "attachments": [unknown]}),
                self.base_report.model_copy(update={
                    "id": uuid5(NAMESPACE_URL, "ambiguous"),
                    "attachments": [self.attachment("blank.pdf", None)],
                }),
            ]
            input_path = root / "landings.jsonl"
            input_path.write_text("".join(report.model_dump_json() + "\n" for report in reports), encoding="utf-8")
            progress = []

            def fake_download(report, attachment, directory):
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"{report.id}_{attachment.filename.replace(' ', '_')}"
                if attachment.filename == "OPT.pdf":
                    with pymupdf.open() as document:
                        page = document.new_page()
                        page.insert_textbox(
                            pymupdf.Rect(40, 40, 550, 780),
                            "The humanitarian report describes the situation. " * 15,
                            fontsize=12,
                        )
                        document.save(path)
                elif attachment.filename == "blank.pdf":
                    with pymupdf.open() as document:
                        document.new_page()
                        document.save(path)
                else:
                    path.write_bytes(b"%PDF-fake")
                return path

            result = download_english_pdfs(
                input_path,
                root / "pdfs",
                root / "report.csv",
                workers=2,
                downloader=fake_download,
                progress=progress.append,
            )
            with result.report_path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(result.total, 5)
            self.assertEqual(result.downloaded, 2)
            self.assertEqual(result.no_english_pdf, 1)
            self.assertEqual(result.no_pdf, 1)
            self.assertEqual(result.language_ambiguous, 1)
            self.assertEqual([row["status"] for row in rows].count("no_english_pdf"), 1)
            self.assertEqual([row["status"] for row in rows].count("no_pdf"), 1)
            self.assertEqual([row["status"] for row in rows].count("language_ambiguous"), 1)
            self.assertTrue(any(row["other_language_pdfs"] for row in rows))
            self.assertEqual(len(progress), 5)
            inferred_row = next(row for row in rows if row["landing_id"] == str(uuid5(NAMESPACE_URL, "unknown")))
            self.assertEqual(inferred_row["selection_basis"], "PyMuPDF text extraction: English")

    def test_worker_limit_cannot_exceed_fifty(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "empty.jsonl"
            input_path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                download_english_pdfs(input_path, workers=51)


if __name__ == "__main__":
    unittest.main()
