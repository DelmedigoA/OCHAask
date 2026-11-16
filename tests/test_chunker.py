import json
from pathlib import Path

import pymupdf
import pytest

from ochask.chunker import chunk_pdf, main


def make_pdf(path: Path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((30, 45), "KEY HIGHLIGHTS", fontsize=20)
    page.insert_text((30, 80), "A humanitarian update with useful information.", fontsize=11)
    # Create a real embedded raster image without requiring Pillow.
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(180)
    page.insert_image(pymupdf.Rect(30, 100, 130, 200), pixmap=pix)
    page = doc.new_page(width=400, height=500)
    page.insert_text((30, 45), "The section continues on the next page.", fontsize=11)
    doc.save(path)
    doc.close()


def test_pages_sections_and_images(tmp_path):
    pdf = tmp_path / "report.pdf"
    make_pdf(pdf)
    result = chunk_pdf(pdf, tmp_path / "output")
    assert result["page_count"] == 2
    first, second = result["pages"]
    assert [p["page_number"] for p in result["pages"]] == [1, 2]
    heading = first["chunks"][0]
    assert heading["role"] == "heading"
    assert heading["text"] == "KEY HIGHLIGHTS"
    image = next(c for c in first["chunks"] if c["type"] == "image")
    assert (tmp_path / "output" / image["image_path"]).is_file()
    assert image["bbox"] == [30, 100, 130, 200]
    assert second["chunks"][0]["section"] == "KEY HIGHLIGHTS"
    assert len({c["id"] for p in result["pages"] for c in p["chunks"]}) == sum(len(p["chunks"]) for p in result["pages"])


def test_low_text_page_and_validation(tmp_path):
    pdf = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    page = chunk_pdf(pdf, tmp_path / "out")["pages"][0]
    assert page["low_extractable_text"]
    assert page["chunks"][0]["role"] == "page_render"
    with pytest.raises(ValueError):
        chunk_pdf(pdf, tmp_path / "out", max_chars=20)


def test_cli_continues_after_bad_pdf_and_avoids_basename_collisions(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    make_pdf(a / "same.pdf")
    make_pdf(b / "same.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a PDF")
    out = tmp_path / "out"
    assert main([str(bad), str(a / "same.pdf"), str(b / "same.pdf"), "-o", str(out)]) == 1
    documents = list(out.glob("*.json"))
    assert len(documents) == 2
    for path in documents:
        result = json.loads(path.read_text())
        assert (out / next(c["image_path"] for c in result["pages"][0]["chunks"] if c["type"] == "image")).exists()


def test_control_characters_mark_page_as_low_extractable_text(tmp_path, monkeypatch):
    import ochask.chunker as chunker

    pdf = tmp_path / "bad-text.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((30, 45), "A normal placeholder for the extraction test.")
    doc.save(pdf)
    doc.close()
    original = chunker._text_block

    def corrupted(block):
        result = original(block)
        if result:
            # There are more than 50 printable characters, but most text is corrupt.
            result["text"] = ("a" * 80) + ("\x00\x02\x03" * 50)
        return result

    monkeypatch.setattr(chunker, "_text_block", corrupted)
    page = chunk_pdf(pdf, tmp_path / "out")["pages"][0]
    assert page["text_characters"] == 230
    assert page["unusable_text_characters"] == 150
    assert page["low_extractable_text"]


def test_small_image_filter_is_configurable_and_preserves_empty_page(tmp_path):
    pdf = tmp_path / "small-images.pdf"
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(180)
    page = doc.new_page(width=300, height=300)
    page.insert_text((30, 30), "Some text on the page.")
    page.insert_image(pymupdf.Rect(30, 50, 45, 65), pixmap=pix)
    page.insert_image(pymupdf.Rect(30, 100, 130, 200), pixmap=pix)
    page = doc.new_page(width=300, height=300)
    page.insert_image(pymupdf.Rect(30, 50, 45, 65), pixmap=pix)
    doc.save(pdf)
    doc.close()

    pages = chunk_pdf(pdf, tmp_path / "filtered")["pages"]
    assert pages[0]["skipped_small_images"] == 1
    assert sum(c["type"] == "image" for c in pages[0]["chunks"]) == 1
    assert pages[1]["skipped_small_images"] == 1
    assert pages[1]["chunks"][0]["role"] == "page_render"
    unfiltered = chunk_pdf(pdf, tmp_path / "all", min_image_size=0)
    assert sum(c["type"] == "image" for c in unfiltered["pages"][0]["chunks"]) == 2
    with pytest.raises(ValueError):
        chunk_pdf(pdf, tmp_path / "bad", min_image_size=-1)
