import json

from ochask.embedding_chunks import main, prepare_document


def sample():
    return {
        "source_file": "report.pdf", "page_count": 2,
        "pages": [
            {"page_number": 1, "low_extractable_text": False, "chunks": [
                {"id": "p0001-c0001", "type": "text", "role": "running_header", "text": "browser header", "bbox": [0, 0, 2, 2]},
                {"id": "p0001-c0002", "type": "text", "role": "heading", "text": "Health", "section": "Health", "bbox": [0, 3, 4, 5]},
                {"id": "p0001-c0003", "type": "text", "role": "heading", "text": "Response", "section": "Response", "bbox": [0, 6, 4, 8]},
                {"id": "p0001-c0004", "type": "text", "role": "text", "text": "Care was provided.", "section": "Response", "bbox": [0, 9, 4, 12]},
                {"id": "p0001-c0005", "type": "image", "image_path": "images/photo.png", "section": "Response", "bbox": [0, 14, 100, 100], "caption_chunk_id": "p0001-c0006"},
                {"id": "p0001-c0006", "type": "text", "role": "caption", "text": "A medical team. Photo: OCHA", "section": "Response", "bbox": [0, 101, 50, 110]},
                {"id": "p0001-c0007", "type": "image", "image_path": "images/no-caption.png", "section": "Response", "bbox": [0, 111, 100, 200]},
                {"id": "p0001-c0008", "type": "text", "role": "heading", "text": "Next section", "section": "Next section", "bbox": [0, 201, 100, 210]},
            ]},
            {"page_number": 2, "low_extractable_text": True, "chunks": [
                {"id": "p0002-c0001", "type": "text", "role": "text", "text": "Continued.", "section": "Next section", "bbox": [0, 1, 100, 10]},
                {"id": "p0002-c0002", "type": "text", "role": "text", "text": "\x00\x01\x02" * 30, "section": "Next section", "bbox": [0, 11, 100, 20]},
            ]},
        ],
    }


def test_headings_captions_and_embedding_eligibility():
    result = prepare_document(sample(), "report-id")
    p1, p2 = result["pages"]
    body, image, uncaptained = p1["chunks"]
    assert body["text"] == "Health\nResponse\nCare was provided."
    assert body["id"] == "report-id:p0001-c0004"
    assert body["heading_pages"] == [1]
    assert image["text"] == "Response\nA medical team. Photo: OCHA"
    assert image["embedding_ready"]
    assert uncaptained["text"] is None and not uncaptained["embedding_ready"]
    assert p2["chunks"][0]["text"] == "Next section\nContinued."
    assert p2["chunks"][0]["heading_pages"] == [1]
    assert p2["chunks"][1]["embedding_ready"] is False
    assert all(c["page_number"] == page["page_number"] for page in result["pages"] for c in page["chunks"])


def test_cli_copies_images_and_does_not_modify_inputs(tmp_path):
    source, target = tmp_path / "input", tmp_path / "output"
    (source / "images").mkdir(parents=True)
    for name in ["photo.png", "no-caption.png"]:
        (source / "images" / name).write_bytes(b"test image")
    original = json.dumps(sample())
    (source / "doc.json").write_text(original)
    assert main([str(source), "-o", str(target)]) == 0
    assert (source / "doc.json").read_text() == original
    assert (target / "images" / "photo.png").read_bytes() == b"test image"
    assert json.loads((target / "doc.json").read_text())["document_id"] == "doc"
