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

## Index PDFs with Docling

The indexing pipeline uses Granite Docling with the MLX VLM engine and contextualized Docling HybridChunker chunks (target: 450 tokens). It requires Apple Silicon, sufficient memory for the model, and the model download on first use. It replaces the previous PyMuPDF chunking workflow; downloading PDFs still uses PyMuPDF for language detection.

Set `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `SUPABASE_URL`, and the server-only `SUPABASE_SECRET_KEY` in the environment or an ignored `.env` file. For example, set `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`. The selected model must produce 1536-dimensional vectors, and indexing and search must use the same model. Use a Supabase secret API key (`sb_secret_...`) and never expose it to a browser. Alternatively, `SUPABASE_SECRET_KEYS` can contain a JSON object with a `default` key. Apply the Supabase migrations to the intended database before indexing. With scraped metadata and downloaded PDFs in their default locations, run:

```sh
uv run python -m ochask.ingest data/pdfs --batch-size 64
# Or index a single PDF:
uv run python -m ochask.ingest data/pdfs/<report-uuid>_report.pdf --reports data/scraped/landing_pages.jsonl
```

PDF filenames must begin with the report UUID followed by `_`, and matching metadata must be present in the JSONL. The command batches requests to the configured OpenAI embedding model and commits each report atomically. Rerunning a report replaces its previous chunks for that model only after conversion and embedding succeed. Failed documents are reported and the command exits nonzero. Embedding requests incur API costs. Chunks retain all known one-based source page numbers; an empty page list means Docling supplied no provenance.

### Run the full local PDF corpus with live progress

To index all downloaded PDFs and upload them to the private `pdfs` bucket, run:

```sh
uv run python -u -m ochask.batch_ingest data/pdfs \
  --reports data/scraped/landing_pages.jsonl
```

This command checks Supabase first, skips reports that already have chunks for `OPENAI_EMBEDDING_MODEL`, and skips PDF uploads whose stored hash and private Storage object both match the local file. It processes the rest one report at a time, prints live per-report status and elapsed time, and continues after individual failures. It writes a timestamped JSONL run log under `data/ingestion_runs/` (ignored by Git). Rerun the same command to resume: successfully indexed reports and verified PDFs are skipped. The command exits nonzero if any reports fail and prints a final count and elapsed time. It does not print credential values or third-party error bodies.

## Hybrid search

```python
from ochask.search import search

results = search("178 UN", limit=10, semantic_weight=1.0, keyword_weight=1.0)
for hit in results:
    print(hit["title"], hit["landing_url"], hit["pages"], hit["content"], hit["fusion_score"])
```

Search embeds the query once and fuses pgvector cosine similarity and English PostgreSQL full-text rankings using weighted reciprocal rank fusion (k=60). Each candidate list contains up to ten times the requested result count. Weights are non-negative and at least one must be positive; the default weights are equal. Search requires the same server-side credentials and returns source chunks, not generated answers. Existing rows without new chunk IDs are excluded until their reports are reindexed.

## Chat with indexed reports

Set `OPENAI_CHAT_MODEL` in `.env` (for example, `openai:gpt-6-luna`) and run:

```sh
uv run uvicorn ochask.web.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The Pydantic AI chat searches the indexed reports and can use up to 30 chunks per search. Click a citation to open its PDF page and highlight matching evidence. It uses only the reports currently indexed in Supabase. Chat and search make OpenAI API requests. The ArchiveLens-inspired interface is intended for local development. PDFs are stored in the private Supabase `pdfs` bucket.

To add another already-indexed report's local PDF to the viewer, set `SUPABASE_PDF_BUCKET` (default: `pdfs`) and run:

```sh
uv run python -c 'from pathlib import Path; from ochask.config import Settings; from ochask.storage import upload_pdf; upload_pdf(Path("data/pdfs/<report-uuid>_report.pdf"), Settings.from_environment())'
```

Uploads use a content-derived object name and verify the downloaded file before linking it to its report. The bucket remains private; the local server retrieves PDFs without exposing its secret key. The viewer opens a cited page even when its text layer cannot match the quoted passage.
