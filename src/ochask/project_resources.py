"""Small packaged Bearing Witness project metadata and FAQ collections."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import cached_property
from importlib.resources import files
from typing import Any


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _bm25_score(
    document: Counter[str],
    query_terms: list[str],
    document_frequencies: Counter[str],
    total_documents: int,
    average_document_length: float,
) -> float:
    k1, b = 1.5, 0.75
    document_length = sum(document.values())
    score = 0.0
    for term in query_terms:
        term_frequency = document[term]
        if not term_frequency:
            continue
        document_frequency = document_frequencies[term]
        inverse_frequency = math.log(
            1 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        denominator = term_frequency + k1 * (
            1 - b + b * document_length / max(average_document_length, 1)
        )
        score += inverse_frequency * term_frequency * (k1 + 1) / denominator
    return score


class ProjectMetadataCollection:
    """Read packaged records about Bearing Witness and its public website."""

    def __init__(self) -> None:
        path = files("ochask").joinpath(
            "data", "project-metadata", "bearing-witness.json"
        )
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.records: dict[str, dict[str, Any]] = {}
        for record in raw:
            record_id = str(record["id"])
            if record_id in self.records:
                raise ValueError(f"Duplicate Bearing Witness metadata record: {record_id}")
            self.records[record_id] = {
                "record_id": record_id,
                "kind": str(record["kind"]),
                "title": str(record["title"]),
                "description": str(record.get("description") or ""),
                "language": str(record.get("language") or ""),
                "source_url": str(record.get("source_url") or ""),
                "content": record.get("content"),
            }
        if not self.records:
            raise ValueError("No Bearing Witness project metadata records are packaged")

    def list_records(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in record.items() if key != "content"}
            for record in self.records.values()
        ]

    def read(self, record_id: str) -> dict[str, Any]:
        return self.records.get(record_id) or {"error": "Unknown project metadata record ID"}


class FaqCollection:
    """Search FAQ content first, then read the selected answer."""

    def __init__(self) -> None:
        path = files("ochask").joinpath("data", "faqs", "bearing-witness", "faq.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.faqs: dict[str, dict[str, str]] = {}
        for faq in raw:
            faq_id = str(faq["id"])
            if faq_id in self.faqs:
                raise ValueError(f"Duplicate Bearing Witness FAQ ID: {faq_id}")
            self.faqs[faq_id] = {
                "faq_id": faq_id,
                "question": str(faq["question"]),
                "answer": str(faq["answer"]),
                "url": str(faq.get("url") or ""),
            }
        if not self.faqs:
            raise ValueError("No Bearing Witness FAQ records are packaged")
        self.documents = {
            faq_id: Counter(_tokens(f"{faq['question']} {faq['answer']}"))
            for faq_id, faq in self.faqs.items()
        }
        self.document_frequencies: Counter[str] = Counter()
        for document in self.documents.values():
            self.document_frequencies.update(set(document))

    @cached_property
    def average_document_length(self) -> float:
        return sum(sum(document.values()) for document in self.documents.values()) / len(self.documents)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_terms = _tokens(query)
        if not query_terms:
            return []
        matches = []
        for faq_id, faq in self.faqs.items():
            score = _bm25_score(
                self.documents[faq_id], query_terms, self.document_frequencies,
                len(self.documents), self.average_document_length,
            )
            if score > 0:
                matches.append({
                    "faq_id": faq_id,
                    "question": faq["question"],
                    "url": faq["url"],
                    "score": score,
                })
        return sorted(matches, key=lambda item: (-item["score"], item["faq_id"]))[
            :max(0, min(limit, 10))
        ]

    def read(self, faq_id: str) -> dict[str, str]:
        return self.faqs.get(faq_id) or {"error": "Unknown FAQ ID"}


project_metadata = ProjectMetadataCollection()
bearing_witness_faqs = FaqCollection()
