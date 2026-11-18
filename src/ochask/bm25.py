"""Build and serve an optional local BM25 snapshot of the Supabase chunk index."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

from .config import Settings
from .database import rest_rows

if TYPE_CHECKING:
    import bm25s

    from .search import SearchResult, SourceChunk

DEFAULT_INDEX = "data/ochask-bm25.zip"
INDEX_VERSION = 1
TOKEN_PATTERN = r"(?u)\d+(?:[,.]\d+)+|\b\w+\b"


def _library():
    try:
        import bm25s
    except ModuleNotFoundError as exc:
        if exc.name != "bm25s":
            raise
        raise RuntimeError("Install local BM25 with uv sync --group bm25") from exc
    return bm25s


@dataclass(frozen=True)
class LocalIndex:
    retriever: bm25s.BM25

    def search(self, query: str, limit: int) -> list[SourceChunk]:
        tokens = _library().tokenize(
            query, token_pattern=TOKEN_PATTERN, return_ids=False, show_progress=False
        )[0]
        if not tokens:
            return []
        scores = self.retriever.get_scores(list(dict.fromkeys(tokens)))
        # Stable sorting uses the same chunk order across repeated loads of a snapshot.
        ranked = (-scores).argsort(kind="stable")
        return [
            self.retriever.corpus[int(position)]
            for position in ranked[:limit]
            if scores[position] > 0
        ]


def fuse(
    semantic: Sequence[SourceChunk],
    keywords: Sequence[SourceChunk],
    limit: int,
    semantic_weight: float,
    keyword_weight: float,
) -> list[SearchResult]:
    """Merge candidate lists by chunk ID with the existing SQL RRF constant."""
    rows = {}
    scores = {}
    for results, weight in ((semantic, semantic_weight), (keywords, keyword_weight)):
        if weight == 0:
            continue
        for rank, row in enumerate(results, 1):
            chunk_id = row["chunk_id"]
            rows.setdefault(chunk_id, row)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (60 + rank)
    ranked = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [{**rows[key], "fusion_score": scores[key]} for key in ranked[:limit]]


def _source(settings: Settings) -> tuple[str, str]:
    return settings.supabase_url.rstrip("/"), settings.embedding_model


def build_index(settings: Settings, path: Path) -> int:
    """Read the current corpus and atomically publish a local archive."""
    bm25s = _library()
    url, model = _source(settings)
    reports = rest_rows(
        url,
        settings.supabase_secret_key,
        "report_landings",
        select="id,title,landing_url",
        filters={"order": "id.asc"},
    )
    chunks = rest_rows(
        url,
        settings.supabase_secret_key,
        "report_embeddings",
        select="chunk_id,report_id,pages,content",
        filters={
            "embedding_model": f"eq.{model}",
            "chunk_id": "not.is.null",
            "order": "id.asc",
        },
    )
    metadata = {report["id"]: report for report in reports}
    if any(chunk["report_id"] not in metadata for chunk in chunks):
        raise RuntimeError("Some chunks lack report metadata. Retry the BM25 build.")
    if not chunks or len({chunk["chunk_id"] for chunk in chunks}) != len(chunks):
        raise RuntimeError("Expected a non-empty corpus with unique chunk IDs.")
    corpus = [
        {
            **chunk,
            "title": metadata[chunk["report_id"]]["title"],
            "landing_url": metadata[chunk["report_id"]]["landing_url"],
        }
        for chunk in chunks
    ]
    tokens = bm25s.tokenize(
        [f"{row['title']}\n{row['content']}" for row in corpus],
        token_pattern=TOKEN_PATTERN,
        show_progress=False,
    )
    retriever = bm25s.BM25(method="lucene")
    retriever.index(tokens, show_progress=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as staging:
        directory = Path(staging) / "index"
        retriever.save(
            directory,
            corpus=corpus,
            show_progress=False,
            raise_on_serialization_error=True,
        )
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "version": INDEX_VERSION,
                    "source": [url, model],
                    "chunks": len(corpus),
                }
            ),
            encoding="utf-8",
        )
        archive = Path(staging) / "index.zip"
        with ZipFile(archive, "w") as bundle:
            for file in directory.iterdir():
                bundle.write(file, file.name)
        archive.chmod(0o600)
        archive.replace(path)
    return len(corpus)


def load_index(settings: Settings) -> LocalIndex:
    """Load once per process and refresh after a successful local rebuild."""
    path = Path(settings.bm25_index_path or DEFAULT_INDEX).expanduser().resolve()
    try:
        stat = path.stat()
        return _load_archive(path, stat.st_ino, stat.st_mtime_ns, _source(settings))
    except (FileNotFoundError, BadZipFile) as exc:
        raise RuntimeError(
            f"Missing or invalid BM25 index at {path}. Rebuild with python -m ochask.bm25."
        ) from exc


@lru_cache(maxsize=1)
def _load_archive(
    path: Path, inode: int, modified_ns: int, source: tuple[str, str]
) -> LocalIndex:
    bm25s = _library()
    with ZipFile(path) as bundle, TemporaryDirectory() as directory:
        manifest = json.loads(bundle.read("manifest.json"))
        if manifest["version"] != INDEX_VERSION or tuple(manifest["source"]) != source:
            raise RuntimeError(
                "BM25 index does not match this Supabase project or embedding model. Rebuild it."
            )
        bundle.extractall(directory)
        retriever = bm25s.BM25.load(directory, load_corpus=True, show_progress=False)
    if len(retriever.corpus) != manifest["chunks"]:
        raise RuntimeError("BM25 index has incomplete chunk metadata. Rebuild it.")
    return LocalIndex(retriever)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the local BM25 index from Supabase."
    )
    parser.add_argument("--output", type=Path, help="Index archive path")
    args = parser.parse_args()
    settings = Settings.from_environment()
    path = (args.output or Path(settings.bm25_index_path or DEFAULT_INDEX)).expanduser()
    count = build_index(settings, path)
    print(f"Indexed {count:,} chunks in {path}")


if __name__ == "__main__":
    main()
