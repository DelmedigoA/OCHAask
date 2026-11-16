"""Turn extracted page chunks into self-contained, embedding-ready records."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any

from .chunker import _unusable_characters


def _readable(text: str) -> bool:
    if not text.strip():
        return False
    bad = _unusable_characters(text)
    return not (bad >= 3 and bad / len(text) >= .05)


def _context_text(headings: list[dict[str, Any]], section: str | None, text: str) -> str:
    labels = [h["text"] for h in headings if _readable(h["text"])]
    if section and _readable(section) and section not in labels:
        labels.append(section)
    # Repeated headings make poor embedding input, but keep their original wording.
    labels = list(dict.fromkeys(label for label in labels if label != text))
    return "\n".join([*labels, text])


def prepare_document(document: dict[str, Any], document_id: str) -> dict[str, Any]:
    """Combine extracted headings and captions without crossing body page boundaries.

    document_id must identify the source PDF and is included in every chunk ID.
    Broken text is retained for review but explicitly marked ineligible for text
    embedding. Heading context may originate on a preceding page.
    """
    pages = []
    pending: list[dict[str, Any]] = []
    for page in document["pages"]:
        number = page["page_number"]
        raw_chunks = page["chunks"]
        chunks = []
        captions = {c["id"]: c for c in raw_chunks if c.get("role") == "caption"}
        attached = {c.get("caption_chunk_id") for c in raw_chunks if c.get("type") == "image"}
        for chunk in raw_chunks:
            role = chunk.get("role")
            if role == "running_header":
                continue
            if role == "heading":
                pending.append({"text": chunk.get("text", ""), "page": number,
                                "id": chunk["id"], "bbox": chunk["bbox"]})
                continue
            if role == "caption" and chunk["id"] in attached:
                continue
            section = chunk.get("section")
            if chunk["type"] == "text":
                text = chunk.get("text", "")
                content = _context_text(pending, section, text)
                result = {"id": f"{document_id}:{chunk['id']}", "type": "text",
                          "page_number": number, "bbox": chunk["bbox"],
                          "text": content, "embedding_ready": _readable(content)}
                if pending:
                    result["heading_pages"] = sorted({h["page"] for h in pending})
                chunks.append(result)
                pending = []
            elif chunk["type"] == "image":
                caption = captions.get(chunk.get("caption_chunk_id"))
                caption_text = caption.get("text", "") if caption else ""
                result = {"id": f"{document_id}:{chunk['id']}", "type": "image",
                          "page_number": number, "bbox": chunk["bbox"],
                          "image_path": chunk["image_path"],
                          "text": _context_text([], section, caption_text) if caption_text else None,
                          "embedding_ready": bool(caption_text and _readable(caption_text))}
                if chunk.get("role") == "page_render":
                    result["role"] = "page_render"
                chunks.append(result)
        pages.append({"page_number": number, "low_extractable_text": page.get("low_extractable_text", False),
                      "chunks": chunks})
    # Preserve headings that were never followed by body text.
    if pending:
        for heading in pending:
            target = next(page for page in pages if page["page_number"] == heading["page"])
            text = heading["text"]
            target["chunks"].append({"id": f"{document_id}:{heading['id']}",
                                     "type": "text", "page_number": heading["page"],
                                     "bbox": heading["bbox"], "text": text, "embedding_ready": _readable(text),
                                     "role": "unattached_heading"})
    return {"document_id": document_id, "source_file": document["source_file"],
            "page_count": document["page_count"], "pages": pages}


def _copy_images(document: dict[str, Any], source: Path, destination: Path) -> None:
    for page in document["pages"]:
        for chunk in page["chunks"]:
            if chunk["type"] != "image":
                continue
            relative = Path(chunk["image_path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid image path: {relative}")
            image = source / relative
            if not image.is_file():
                raise FileNotFoundError(image)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare clean page chunks for embeddings")
    parser.add_argument("input_dir", type=Path, help="directory produced by ochask.chunker")
    parser.add_argument("--output-dir", "-o", type=Path, required=True)
    args = parser.parse_args(argv)
    source, target = args.input_dir.resolve(), args.output_dir.resolve()
    if not source.is_dir():
        parser.error("input directory does not exist")
    if source == target or source in target.parents or target in source.parents:
        parser.error("input and output directories must be separate")
    inputs = sorted(source.glob("*.json"))
    if not inputs:
        print("No chunked documents found", file=sys.stderr)
        return 1
    target.mkdir(parents=True, exist_ok=True)
    failures = 0
    for path in inputs:
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
            document = prepare_document(original, path.stem)
            _copy_images(document, source, target)
            (target / path.name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{path.name}: prepared")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures += 1
            print(f"ERROR {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"Prepared {len(inputs) - failures}/{len(inputs)} documents; {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
