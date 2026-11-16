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

Set `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `SUPABASE_URL`, and the server-only `SUPABASE_SERVICE_ROLE_KEY` in the environment or an ignored `.env` file. For example, set `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`. The selected model must produce 1536-dimensional vectors, and indexing and search must use the same model. Never expose the service role key to a browser. Apply the Supabase migrations to the intended database before indexing. With scraped metadata and downloaded PDFs in their default locations, run:

```sh
uv run python -m ochask.ingest data/pdfs --batch-size 64
# Or index a single PDF:
uv run python -m ochask.ingest data/pdfs/<report-uuid>_report.pdf --reports data/scraped/landing_pages.jsonl
```

PDF filenames must begin with the report UUID followed by `_`, and matching metadata must be present in the JSONL. The command batches requests to the configured OpenAI embedding model and commits each report atomically. Rerunning a report replaces its previous chunks for that model only after conversion and embedding succeed. Failed documents are reported and the command exits nonzero. Embedding requests incur API costs. Chunks retain all known one-based source page numbers; an empty page list means Docling supplied no provenance.

## Hybrid search

```python
from ochask.search import search

results = search("178 UN", limit=10, semantic_weight=1.0, keyword_weight=1.0)
for hit in results:
    print(hit["title"], hit["landing_url"], hit["pages"], hit["content"], hit["fusion_score"])
```

Search embeds the query once and fuses pgvector cosine similarity and English PostgreSQL full-text rankings using weighted reciprocal rank fusion (k=60). Each candidate list contains up to ten times the requested result count. Weights are non-negative and at least one must be positive; the default weights are equal. Search requires the same server-side credentials and returns source chunks, not generated answers. Existing rows without new chunk IDs are excluded until their reports are reindexed.
