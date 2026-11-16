# OCHAsk landing-page scraper

The scraper extracts structured metadata from ReliefWeb landing pages. Its PDF download mode selects one English attachment per landing page and uses text extraction only when the language label is unclear.

## Run

The pipeline reads unique landing URLs from the original `data/ocha_situation_reports_urls.csv` and scrapes all of them by default:

```sh
uv run python -m ochask.scraper
```

The pipeline writes successful records to `data/scraped/landing_pages.jsonl` and reports failed URLs. Each run replaces the output file. Use `--limit 10` for a deterministic 10-page sample, or `--csv` and `--output` to choose different files.

## Download English PDFs

After scraping, run:

```sh
uv run python -m ochask.scraper --download-pdfs --workers 2 --request-interval 2
```

This selects one English PDF per landing page from `data/scraped/landing_pages.jsonl`, downloads it into `data/pdfs/`, and writes `data/scraped/pdf_download_report.csv`. If attachment labels and filenames do not identify a language, the downloader extracts text from up to five pages with PyMuPDF and classifies English, Hebrew, or Arabic by script. It does not use OCR. The report records other-language PDFs, pages with no English PDF or no PDF, cases where extracted text was insufficient or mixed, and download failures. Downloads default to two workers and a minimum of two seconds between request starts. On HTTP 429 or 503, all workers pause according to `Retry-After` (or exponential backoff) before retrying. A rerun reuses valid PDFs already on disk, so it resumes the previous run. The worker count can be set up to 50. Use `--landing-pages`, `--pdf-dir`, and `--report` to choose different paths.

For one page from Python:

```python
from ochask import get_landing_page

report = get_landing_page("https://reliefweb.int/report/...")
print(report.model_dump(mode="json"))
```

## Chunk PDFs and extract images

The OCHA chunker uses PyMuPDF to produce one JSON document per PDF, organized as pages containing ordered text and image chunks. It can process individual files or all PDFs directly inside a directory:

```sh
uv run python -m ochask.chunker data/pdfs --output-dir data/chunked
uv run python -m ochask.chunker path/to/report.pdf --output-dir data/chunked --max-chars 1800 --image-scale 2
```

The command writes JSON files and an `images/` directory under the output directory. It continues after individual failures and exits with a nonzero status if any inputs fail. Names include a hash of the input's absolute path to avoid collisions; moving a PDF changes its output name. Image paths in JSON are relative to the output directory. Image files are PNG crops rendered from each image placement on the page, so they reflect the page's appearance, including any overlapping content. The same embedded image used multiple times is recorded for each placement.

From Python:

```python
from ochask.chunker import chunk_pdf

report = chunk_pdf("path/to/report.pdf", "data/chunked")
for page in report["pages"]:
    for chunk in page["chunks"]:
        print(page["page_number"], chunk["type"], chunk.get("text", chunk.get("image_path")))
```

Each page has a one-based `page_number`, dimensions in PDF points, `text_characters`, `unusable_text_characters`, `skipped_small_images`, `low_extractable_text`, and `chunks`. Each chunk has a page-local ID, `type`, bounding box in PDF points, and detected `section`. Text chunks have `text` and a `role` (`heading`, `text`, `caption`, or `running_header`). Image chunks have `image_path`, and may link to a nearby caption with `caption_chunk_id`. Split text chunks share their original block's bounding box. Headings and captions are heuristic, and `section` carries the most recently detected heading across pages.

### Observed report patterns and limitations

Many flash and situation updates are web exports with a large title, section headings, indented paragraphs or bullets, a first-page photo, and sometimes a printed browser header. Response updates also use sector headings and short labels such as Response and Challenges. Font size, short known labels, and position guide the chunker; browser-style headers are marked rather than discarded. Consecutive line blocks are joined conservatively, while longer blocks are split at whitespace.

These PDFs are not uniform: multi-column pages, tables, infographics, vector maps, and images with overlays can produce imperfect reading order or heading detection. Raster placements smaller than 40 PDF points in either displayed dimension are skipped by default to avoid extracting individual letter images and other tiny graphics. Use `--min-image-size 0` (or `min_image_size=0` in Python) to retain all placements, or set a different threshold. This size heuristic can also omit meaningful small graphics, and larger logos or decorative images may remain. Each page reports `skipped_small_images`. Vector graphics are not separately detected. A page with no text or retained raster images gets a full-page rendering, which may also be blank. Pages are flagged for review when they have fewer than 50 extracted characters excluding control and replacement characters, or when at least three such characters make up 5% or more of extracted text. The flag does not by itself identify a scan and does not catch every kind of garbled text. Existing JSON files must be regenerated to apply this check. **No OCR is performed**, and text inside images is not extracted. The chunker preserves PyMuPDF's text as extracted, including any garbled characters.

## Prepare chunks for embeddings

After extracting PDFs, create a separate set of compact documents and copy their image assets with:

```sh
uv run python -m ochask.embedding_chunks data/chunked --output-dir data/embedding_chunks
```

This leaves `data/chunked` intact. Each output document has a `document_id`, `source_file`, and `pages`. Every chunk includes a document-qualified `id`, one-based `page_number`, `type`, `bbox`, `text`, and `embedding_ready`; image chunks also have an `image_path` relative to the output directory. IDs derive from the input JSON filename and original chunk ID, so rerunning this step on the same extraction produces the same IDs. Re-extracting a changed PDF or moving it before extraction can change IDs.

Detected headings are added to text chunks as context. When headings and their following text are on different pages, the text remains on its own page and `heading_pages` identifies where the attached headings occurred. Context detected as a section also carries forward to later text. Detected captions are combined with the corresponding image, and printed browser headers are omitted. An unattached heading is retained as a text chunk. These are layout heuristics and may associate captions or headings incorrectly on complex pages.

Only use a chunk's `text` for a text embedding when `embedding_ready` is true. Images with captions can be embedded using their caption text; captionless images have `text: null` and need a multimodal embedding model or a separately generated description. Text containing many control or replacement characters is retained for review with `embedding_ready: false`; this step does not repair it or perform OCR. A page can contain both eligible and ineligible chunks. The output contains no vectors and makes no embedding API calls.
