"""Build section-aware passages from page-level chunk JSON for retrieval review.

This is a local, non-destructive experiment. It reads data/chunked/*.json and
writes separate grouped JSON files plus an HTML preview under
 data/chunked_grouped_test/.
"""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path
import re
import unicodedata

DEFAULT_INPUT = Path("data/chunked")
DEFAULT_OUTPUT = Path("data/chunked_grouped_test")
DEFAULT_MAX_CHARS = 1800
TRAILING_HEADING = re.compile(
    r"^(?P<body>.*?[.!?:])\s+(?P<heading>[A-Z][A-Z0-9&/(), -]{2,})$"
)


def _text(chunk: dict) -> str:
    return " ".join(str(chunk.get("text") or "").split())


def _is_garbled(text: str) -> bool:
    if len(text) < 20:
        return False
    controls = sum(
        unicodedata.category(char) == "Cc" and char not in "\n\r\t"
        for char in text
    )
    return controls / len(text) > 0.05


def _bbox_list(value):
    return list(value) if isinstance(value, (list, tuple)) else value


def group_document(source_path: Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    document = json.loads(source_path.read_text(encoding="utf-8"))
    passages = []
    excluded_garbled = 0
    excluded_chunks = []
    page_summaries = []

    for page in document.get("pages", []):
        page_number = page.get("page_number")
        chunks = page.get("chunks", [])
        passage_index = 0
        current = None
        pending_image = None
        active_section = None

        def emit() -> None:
            nonlocal current, passage_index
            if current is None:
                return
            bodies = current["body"]
            heading = current["heading"]
            if bodies:
                body = "\n\n".join(bodies)
                section = current["section"]
                text = f"{section}\n\n{body}" if section and section not in body else body
            else:
                text = heading or current["section"] or ""
            text = text.strip()
            if text:
                passage_index += 1
                passages.append({
                    "id": f"{source_path.stem}:p{int(page_number or 0):04d}:g{passage_index:04d}",
                    "page": page_number,
                    "section": current["section"],
                    "text": text,
                    "character_count": len(text),
                    "word_count": len(text.split()),
                    "source_chunk_ids": current["chunk_ids"],
                    "source_roles": current["roles"],
                    "bboxes": current["bboxes"],
                    "related_image_path": current["related_image_path"],
                    "inferred_heading_chunk_ids": current["inferred_heading_chunk_ids"],
                })
            current = None

        def start(section, *, heading="", related_image_path=None):
            return {
                "section": section,
                "heading": heading,
                "body": [],
                "chunk_ids": [],
                "roles": [],
                "bboxes": [],
                "related_image_path": related_image_path,
                "inferred_heading_chunk_ids": [],
            }

        def start_new_section(section):
            nonlocal current, active_section
            emit()
            active_section = section
            current = start(section)

        for chunk in chunks:
            kind = chunk.get("type")
            role = chunk.get("role") or kind or "text"
            value = _text(chunk)

            if kind == "image":
                emit()
                pending_image = chunk.get("image_path")
                continue
            if role == "running_header":
                continue
            if not value:
                continue
            if _is_garbled(value):
                emit()
                excluded_garbled += 1
                excluded_chunks.append({
                    "page": page_number,
                    "chunk_id": chunk.get("id"),
                    "reason": "text contains a high proportion of control characters",
                    "sample": value[:120],
                })
                continue

            section = active_section or chunk.get("section")
            if role == "heading":
                section = chunk.get("section") or value
                start_new_section(section)
                current["heading"] = value
                current["chunk_ids"].append(chunk.get("id"))
                current["roles"].append(role)
                if chunk.get("bbox") is not None:
                    current["bboxes"].append(_bbox_list(chunk["bbox"]))
                pending_image = None
                continue

            if role == "caption":
                emit()
                active_section = section
                current = start(section, related_image_path=pending_image)
                current["body"].append(value)
                current["chunk_ids"].append(chunk.get("id"))
                current["roles"].append(role)
                if chunk.get("bbox") is not None:
                    current["bboxes"].append(_bbox_list(chunk["bbox"]))
                emit()
                pending_image = None
                continue

            trailing_heading = TRAILING_HEADING.match(value)
            if trailing_heading:
                value = trailing_heading.group("body").strip()
                inferred_heading = trailing_heading.group("heading").strip()

            if current is not None and section != current["section"]:
                emit()
            if current is None:
                current = start(section, related_image_path=pending_image)
            existing_chars = sum(map(len, current["body"]))
            if current["body"] and existing_chars + len(value) + 2 > max_chars:
                old_section = current["section"]
                emit()
                current = start(old_section)
            current["body"].append(value)
            if chunk.get("id") is not None:
                current["chunk_ids"].append(chunk["id"])
            current["roles"].append(role)
            if chunk.get("bbox") is not None:
                current["bboxes"].append(_bbox_list(chunk["bbox"]))
            pending_image = None
            if trailing_heading:
                emit()
                active_section = inferred_heading.title()
                current = start(active_section, heading=inferred_heading)
                current["inferred_heading_chunk_ids"].append(chunk.get("id"))

        emit()
        page_summaries.append({
            "page": page_number,
            "passage_count": passage_index,
            "low_extractable_text": bool(page.get("low_extractable_text")),
            "text_characters": page.get("text_characters"),
            "unusable_text_characters": page.get("unusable_text_characters"),
        })

    return {
        "document_id": source_path.stem,
        "source_file": document.get("source_file"),
        "source_json": source_path.name,
        "page_count": document.get("page_count"),
        "grouping": {
            "method": "adjacent text chunks grouped by page and section",
            "max_chars": max_chars,
            "running_headers": "excluded",
            "image_chunks": "used as boundaries; captions kept as standalone passages",
            "garbled_text_chunks_excluded": excluded_garbled,
        },
        "excluded_chunks": excluded_chunks,
        "pages": page_summaries,
        "passage_count": len(passages),
        "passages": passages,
    }


def _preview_html(grouped: dict, original: dict) -> str:
    originals = {}
    for page in original.get("pages", []):
        for chunk in page.get("chunks", []):
            if chunk.get("id"):
                originals[chunk["id"]] = chunk

    cards = []
    for passage in grouped["passages"]:
        raw = []
        for chunk_id in passage["source_chunk_ids"]:
            chunk = originals.get(chunk_id)
            if chunk and _text(chunk):
                raw.append(
                    f'<li><b>{html.escape(chunk.get("role") or chunk.get("type") or "text")}</b> '
                    f'<code>{html.escape(str(chunk_id))}</code><br>{html.escape(_text(chunk))}</li>'
                )
        section = passage.get("section") or "Unsectioned"
        image = passage.get("related_image_path")
        image_info = f'<span class="image">caption linked to {html.escape(Path(image).name)}</span>' if image else ""
        cards.append(f'''<article class="passage">
  <div class="meta"><span class="section">{html.escape(section)}</span><span>p. {passage.get('page')}</span><span>{passage['word_count']} words</span><span>merged {len(passage['source_chunk_ids'])} blocks</span>{image_info}</div>
  <div class="text">{html.escape(passage['text'])}</div>
  <details><summary>Source chunks</summary><ul>{''.join(raw)}</ul></details>
</article>''')

    title = grouped.get("source_file") or grouped["document_id"]
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grouped passage preview</title>
<style>
:root{{--bg:#f4f6f8;--card:#fff;--ink:#17212b;--muted:#65717e;--accent:#126a68;--line:#dce3e8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,sans-serif}}
header{{background:#102b35;color:white;padding:28px max(24px,calc((100vw - 1000px)/2))}}header h1{{margin:0 0 8px;font-size:22px}}header p{{margin:4px 0;color:#d5e4e7}}
main{{max-width:1000px;margin:22px auto;padding:0 18px}}.summary{{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0 24px}}.pill{{background:#e0eeec;color:#155b59;border-radius:999px;padding:6px 12px;font-size:13px}}
.passage{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0;box-shadow:0 2px 5px #102b3508}}
.meta{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;color:var(--muted);font-size:12px;margin-bottom:12px}}.section{{background:#eaf2f5;color:#24566a;padding:3px 9px;border-radius:999px;font-weight:650}}.image{{color:#7a5a1c}}
.text{{white-space:pre-wrap;font-size:15px}}details{{margin-top:14px;border-top:1px solid var(--line);padding-top:10px;color:var(--muted);font-size:13px}}summary{{cursor:pointer;font-weight:600}}ul{{padding-left:20px}}li{{margin:12px 0;color:var(--ink)}}code{{color:#687784}}
</style>
<header><h1>Section-aware passage preview</h1><p>{html.escape(title)}</p><p>{grouped['page_count']} pages → {grouped['passage_count']} grouped passages</p></header>
<main><div class="summary"><span class="pill">Adjacent text merged by section</span><span class="pill">Page boundaries preserved</span><span class="pill">Images excluded from text; captions retained</span><span class="pill">Running headers excluded</span><span class="pill">Max {grouped['grouping']['max_chars']} characters per passage</span></div>
{''.join(cards)}
</main></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args()
    if args.max_chars < 200:
        parser.error("--max-chars must be at least 200")
    files = sorted(args.input.glob("*.json"))
    if not files:
        parser.error(f"no JSON files found in {args.input}")

    args.output.mkdir(parents=True, exist_ok=True)
    totals = Counter()
    grouped_by_source = {}
    for source_path in files:
        original = json.loads(source_path.read_text(encoding="utf-8"))
        grouped = group_document(source_path, max_chars=args.max_chars)
        target = args.output / source_path.name
        target.write_text(json.dumps(grouped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        grouped_by_source[source_path.name] = (grouped, original)
        totals["documents"] += 1
        totals["passages"] += grouped["passage_count"]
        totals["source_chunks"] += sum(len(page.get("chunks", [])) for page in original.get("pages", []))
        totals["excluded_garbled"] += grouped["grouping"]["garbled_text_chunks_excluded"]
        totals["low_extractable_text_pages"] += sum(
            page["low_extractable_text"] for page in grouped["pages"]
        )
        totals["documents_with_garbled_chunks"] += bool(grouped["excluded_chunks"])

    preferred = next(
        (name for name, (doc, _) in grouped_by_source.items()
         if "Situation_Report_No._68" in (doc.get("source_file") or "")),
        None,
    )
    if preferred is None:
        preferred = max(
            grouped_by_source,
            key=lambda name: grouped_by_source[name][0]["passage_count"],
        )
    grouped, original = grouped_by_source[preferred]
    preview_path = args.output / "preview.html"
    preview_path.write_text(_preview_html(grouped, original), encoding="utf-8")
    summary = {
        **dict(totals),
        "preview_source": grouped["source_file"],
        "preview_file": preview_path.name,
        "max_chars": args.max_chars,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
