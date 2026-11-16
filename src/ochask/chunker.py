"""Page-preserving extraction for OCHA's PDF and web-export report layouts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf


_SUBHEADINGS = {"response", "responses", "challenges", "needs", "funding", "overview"}


def _asset_key(source: Path) -> str:
    digest = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]
    return source.stem[:36] + "-" + digest


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _unusable_characters(text: str) -> int:
    """Count control and replacement characters that cannot represent report text."""
    return sum(unicodedata.category(char) == "Cc" or char == "\ufffd" for char in text)


def _bbox(rect: Any) -> list[float]:
    return [round(float(x), 2) for x in rect]


def _split_text(text: str, limit: int) -> list[str]:
    """Split on whitespace, with a hard fallback for a single oversized word."""
    parts = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def _text_block(block: dict[str, Any]) -> dict[str, Any] | None:
    lines = []
    spans = []
    for line in block.get("lines", []):
        line_text = _clean("".join(span["text"] for span in line["spans"]))
        if line_text:
            lines.append(line_text)
            spans.extend(span for span in line["spans"] if span["text"].strip())
    text = _clean(" ".join(lines))
    if not text or not spans:
        return None
    size = max(span["size"] for span in spans)
    bold = sum(len(s["text"]) for s in spans if s["flags"] & 16) / max(1, sum(len(s["text"]) for s in spans))
    return {"text": text, "bbox": _bbox(block["bbox"]), "size": size, "bold": bold}


def _body_size(blocks: list[dict[str, Any]]) -> float:
    # Weight font sizes by characters, so headings do not define body size.
    sizes: dict[float, int] = {}
    for block in blocks:
        for line in block.get("lines", []):
            for span in line["spans"]:
                size = round(span["size"], 1)
                sizes[size] = sizes.get(size, 0) + len(span["text"].strip())
    return max(sizes, key=sizes.get) if sizes else 12.0


def _is_heading(block: dict[str, Any], body_size: float) -> bool:
    text = block["text"]
    if len(text) > 160 or len(text.split()) > 18:
        return False
    if text.casefold().rstrip(": ") in _SUBHEADINGS:
        return True
    return block["size"] >= body_size * 1.23 and len(text) > 2


def _is_running_header(text: str, box: list[float], height: float) -> bool:
    # Browser print headers have a timestamp followed by the page title.
    return box[1] < height * .04 and bool(re.match(r"\d{1,2}/\d{1,2}/\d{2,4},?\s+\d", text))


def chunk_pdf(pdf_path: str | Path, output_dir: str | Path, *, max_chars: int = 1800,
              image_scale: float = 2.0, min_image_size: float = 40.0) -> dict[str, Any]:
    """Extract one PDF and save visual assets under output_dir.

    Image paths in the returned document are relative to output_dir. Text is
    extracted without OCR; pages with little extractable text are flagged.
    """
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    if not math.isfinite(image_scale) or image_scale <= 0:
        raise ValueError("image_scale must be finite and positive")
    if not math.isfinite(min_image_size) or min_image_size < 0:
        raise ValueError("min_image_size must be finite and non-negative")
    source = Path(pdf_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    # Filename hash avoids collisions even for very long report names.
    asset_dir = Path("images") / _asset_key(source)
    pages = []
    section: str | None = None
    with pymupdf.open(source) as doc:
        if doc.needs_pass:
            raise ValueError(f"PDF requires a password: {source}")
        for page in doc:
            blocks = page.get_text("dict", sort=True)["blocks"]
            body_size = _body_size([b for b in blocks if b["type"] == 0])
            chunks: list[dict[str, Any]] = []
            extracted_chars = 0
            unusable_chars = 0
            image_count = 0
            skipped_images = 0
            for raw in blocks:
                if raw["type"] == 0:
                    block = _text_block(raw)
                    if not block:
                        continue
                    text, box = block["text"], block["bbox"]
                    extracted_chars += len(text)
                    unusable_chars += _unusable_characters(text)
                    if _is_running_header(text, box, page.rect.height):
                        role = "running_header"
                    elif _is_heading(block, body_size):
                        role = "heading"
                        section = text
                    else:
                        role = "text"
                    previous = chunks[-1] if chunks else None
                    if (role == "text" and previous and previous.get("role") == "text"
                            and previous.get("section") == section
                            and abs(previous["bbox"][0] - box[0]) < 8
                            and 0 <= box[1] - previous["bbox"][3] <= body_size * .85
                            and not re.search(r"[.!?][\"’”)]?$", previous["text"])
                            and len(previous["text"]) + len(text) + 1 <= max_chars):
                        previous["text"] += " " + text
                        previous["bbox"] = _bbox(pymupdf.Rect(previous["bbox"]) | pymupdf.Rect(box))
                    else:
                        for part in _split_text(text, max_chars):
                            chunks.append({"type": "text", "role": role, "text": part,
                                           "section": section, "bbox": box})
                elif raw["type"] == 1:
                    rect = pymupdf.Rect(raw["bbox"]) & page.rect
                    if rect.is_empty or rect.is_infinite:
                        continue
                    if rect.width < min_image_size or rect.height < min_image_size:
                        skipped_images += 1
                        continue
                    image_count += 1
                    relative = asset_dir / f"page-{page.number + 1:04d}-image-{image_count:03d}.png"
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    page.get_pixmap(matrix=pymupdf.Matrix(image_scale, image_scale), clip=rect, alpha=False).save(target)
                    chunks.append({"type": "image", "image_path": relative.as_posix(),
                                   "section": section, "bbox": _bbox(rect),
                                   "source_width": raw.get("width"), "source_height": raw.get("height")})
            # A page with no text or raster placements may consist of vector art.
            if not chunks:
                relative = asset_dir / f"page-{page.number + 1:04d}-render.png"
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(matrix=pymupdf.Matrix(image_scale, image_scale), alpha=False).save(target)
                chunks.append({"type": "image", "image_path": relative.as_posix(),
                               "section": section, "bbox": _bbox(page.rect), "role": "page_render"})
            for index, chunk in enumerate(chunks, 1):
                chunk["id"] = f"p{page.number + 1:04d}-c{index:04d}"
            # Captions in these exports normally sit immediately under a photo.
            for image, following in zip(chunks, chunks[1:]):
                if (image["type"] == "image" and following.get("role") == "text"
                        and len(following["text"]) <= 300
                        and 0 <= following["bbox"][1] - image["bbox"][3] <= 12
                        and following["bbox"][0] < image["bbox"][2]
                        and following["bbox"][2] > image["bbox"][0]):
                    following["role"] = "caption"
                    image["caption_chunk_id"] = following["id"]
            pages.append({"page_number": page.number + 1, "width": round(page.rect.width, 2),
                          "height": round(page.rect.height, 2),
                          "text_characters": extracted_chars,
                          "unusable_text_characters": unusable_chars,
                          "skipped_small_images": skipped_images,
                          "low_extractable_text": (extracted_chars - unusable_chars < 50
                                                   or (unusable_chars >= 3 and unusable_chars / extracted_chars >= .05)),
                          "chunks": chunks})
        return {"source_file": source.name, "page_count": len(doc), "pages": pages}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract page-based OCHA PDF chunks and images")
    parser.add_argument("inputs", nargs="+", type=Path, help="PDF files or directories containing PDFs")
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--image-scale", type=float, default=2.0)
    parser.add_argument("--min-image-size", type=float, default=40.0,
                        help="minimum displayed width and height in PDF points (default: 40; 0 keeps all)")
    args = parser.parse_args(argv)
    if args.max_chars < 100 or not math.isfinite(args.image_scale) or args.image_scale <= 0:
        parser.error("--max-chars must be at least 100 and --image-scale must be finite and positive")
    if not math.isfinite(args.min_image_size) or args.min_image_size < 0:
        parser.error("--min-image-size must be finite and non-negative")
    files = []
    missing = []
    for path in args.inputs:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"))
        elif path.is_file():
            files.append(path)
        else:
            missing.append(path)
    failures = 0
    for path in missing:
        print(f"ERROR {path}: input does not exist", file=sys.stderr)
        failures += 1
    seen = set()
    for path in files:
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        try:
            result = chunk_pdf(path, args.output_dir, max_chars=args.max_chars, image_scale=args.image_scale,
                               min_image_size=args.min_image_size)
            output = args.output_dir / f"{_asset_key(path)}.json"
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{path} -> {output}")
        except Exception as exc:
            print(f"ERROR {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
    if not files:
        print("No PDFs found", file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
