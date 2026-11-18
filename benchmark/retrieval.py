"""Compare the live SQL hybrid and local BM25 hybrid on benchmark evidence pages."""

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path
from statistics import mean, median, quantiles
from time import perf_counter

from openai import OpenAI

from ochask.bm25 import DEFAULT_INDEX, load_index
from ochask.config import Settings
from ochask.embeddings import embed_texts
from ochask.search import _search_unfiltered

ROOT = Path(__file__).resolve().parent
SHORT_QUERIES = {
    "E01": "UNRWA schools sheltering",
    "E02": "Rafah desalination",
    "E03": "Deir Balah toilets",
    "M01": "UNRWA toilets showers",
    "M02": "Kamal Adwan fuel",
    "M03": "Gaza UNRWA schools",
    "H01": "UNRWA schools displaced",
    "H02": "Gaza desalination",
    "V01": "Gaza debris",
    "V02": "Gaza fatalities",
}


def score(rows, expected):
    def relevant(row):
        return {
            (row["landing_url"].rstrip("/"), page) for page in row["pages"]
        } & expected

    return {
        **{
            f"recall@{k}": len(set().union(*(relevant(row) for row in rows[:k])))
            / len(expected)
            for k in (5, 30)
        },
        "mrr@30": next(
            (1 / rank for rank, row in enumerate(rows[:30], 1) if relevant(row)), 0
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", help="Local BM25 archive path")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    settings = Settings.from_environment()
    variants = {
        "sql_hybrid": replace(settings, bm25_index_path=None),
        "bm25_hybrid": replace(
            settings,
            bm25_index_path=args.index or settings.bm25_index_path or DEFAULT_INDEX,
        ),
    }
    start = perf_counter()
    load_index(variants["bm25_hybrid"])
    index_load_ms = (perf_counter() - start) * 1000
    with (ROOT.parent / "data/ocha_situation_reports_urls.csv").open(
        encoding="utf-8-sig"
    ) as source:
        urls = {
            row["id"]: row["landing_url"].rstrip("/") for row in csv.DictReader(source)
        }
    questions = json.loads((ROOT / "dataset.json").read_text(encoding="utf-8"))[
        "questions"
    ]
    client = OpenAI()
    summary = {"cold_index_load_ms": round(index_load_ms, 1)}
    for cohort in ("questions", "short_queries"):
        queries = [
            q["question"] if cohort == "questions" else SHORT_QUERIES[q["id"]]
            for q in questions
        ]
        vectors = embed_texts(client, queries, settings.embedding_model)
        scores = {name: [] for name in variants}
        timings = {name: [] for name in variants}
        for i, (question, query, vector) in enumerate(
            zip(questions, queries, vectors, strict=True)
        ):
            expected = {
                (urls[e["document_id"]], e["page"]) for e in question["evidence"]
            }
            for repeat in range(args.repeats):
                order = (
                    list(variants)
                    if (repeat + i) % 2 == 0
                    else list(reversed(variants))
                )
                for name in order:
                    start = perf_counter()
                    result = _search_unfiltered(variants[name], query, vector, 30, 1, 1)
                    timings[name].append((perf_counter() - start) * 1000)
                    if repeat == 0:
                        scores[name].append(score(result, expected))
        summary[cohort] = {
            name: {
                **{
                    metric: round(mean(row[metric] for row in scores[name]), 4)
                    for metric in scores[name][0]
                },
                "median_ms": round(median(timings[name]), 1),
                "p95_ms": round(
                    quantiles(timings[name], n=20, method="inclusive")[-1], 1
                ),
                "calls": len(timings[name]),
            }
            for name in variants
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
