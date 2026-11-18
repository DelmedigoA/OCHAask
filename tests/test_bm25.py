from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("bm25s")

from ochask import bm25
from ochask.config import Settings
from ochask import search as retrieval


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    settings = Settings(
        "https://example.test",
        "key",
        "model",
        bm25_index_path=str(tmp_path / "index.zip"),
    )
    reports = [
        {
            "id": "r1",
            "title": "Flash Update #1",
            "landing_url": "https://example.test/1",
        },
        {
            "id": "r2",
            "title": "Flash Update #12",
            "landing_url": "https://example.test/12",
        },
    ]
    chunks = [
        {
            "chunk_id": "c1",
            "report_id": "r1",
            "pages": [2],
            "content": "Desalination for 1,200 families.",
        },
        {
            "chunk_id": "c2",
            "report_id": "r2",
            "pages": [5],
            "content": "Emergency shelters and showers.",
        },
        {
            "chunk_id": "c3",
            "report_id": "r2",
            "pages": [7],
            "content": "Additional sanitation facilities.",
        },
    ]

    def read(_, __, table, *, select, filters):
        assert filters["order"] == "id.asc"
        if table == "report_landings":
            return reports
        assert table == "report_embeddings"
        assert "embedding" not in select.split(",")
        assert filters["embedding_model"] == "eq.model"
        return chunks

    monkeypatch.setattr(bm25, "rest_rows", read)
    path = Path(settings.bm25_index_path)
    assert bm25.build_index(settings, path) == 3
    return settings, path, chunks


def test_persisted_index_and_rebuild(snapshot):
    settings, path, chunks = snapshot
    index = bm25.load_index(settings)
    assert bm25.load_index(settings) is index
    assert index.search("unseenword", 100) == []
    assert index.search("a the", 100) == []
    hits = index.search("desalination in Flash Update #1 for 1,200 families", 100)
    assert hits[0] == {
        **chunks[0],
        "title": "Flash Update #1",
        "landing_url": "https://example.test/1",
    }
    assert [hit["chunk_id"] for hit in index.search("1", 100)] == ["c1"]
    with pytest.raises(RuntimeError, match="does not match"):
        bm25.load_index(replace(settings, embedding_model="different"))

    chunks[0]["content"] = "New well infrastructure."
    bm25.build_index(settings, path)
    rebuilt = bm25.load_index(settings)
    assert rebuilt is not index
    assert rebuilt.search("desalination", 100) == []
    assert rebuilt.search("well", 100)[0]["chunk_id"] == "c1"

    chunks.append(chunks[0])
    with pytest.raises(RuntimeError, match="unique"):
        bm25.build_index(settings, path)
    assert bm25.load_index(settings) is rebuilt


def test_opt_in_fuses_vector_rpc_and_bm25(snapshot, monkeypatch):
    settings, _, _ = snapshot
    index = bm25.load_index(settings)
    semantic = [index.search("showers", 1)[0], index.search("desalination", 1)[0]]
    calls = []

    def rpc(_, __, name, payload):
        assert name == "hybrid_search_report_chunks"
        calls.append(payload)
        return semantic

    monkeypatch.setattr(retrieval, "rpc", rpc)
    monkeypatch.setattr(retrieval, "OpenAI", lambda: None)
    monkeypatch.setattr(retrieval, "embed_texts", lambda *args: [[0.5]])
    monkeypatch.setattr(Settings, "from_environment", lambda: settings)

    hits = retrieval.search("desalination", limit=30, keyword_weight=2)
    assert calls[-1]["keyword_weight"] == 0
    assert calls[-1]["match_count"] == 100
    assert [row["chunk_id"] for row in hits] == ["c1", "c2"]
    assert hits[0]["fusion_score"] == pytest.approx(1 / 62 + 2 / 61)
    assert hits[1]["fusion_score"] == pytest.approx(1 / 61)

    assert retrieval.search("desalination", semantic_weight=0)[0]["chunk_id"] == "c1"
    assert len(calls) == 1

    path_missing = replace(settings, bm25_index_path="missing.zip")
    monkeypatch.setattr(Settings, "from_environment", lambda: path_missing)
    assert retrieval.search("desalination", keyword_weight=0) == semantic
    with pytest.raises(RuntimeError, match="Rebuild"):
        retrieval.search("desalination")

    monkeypatch.setattr(retrieval, "_filtered_search", lambda *args: semantic)
    assert retrieval.search("desalination", date_from=date(2024, 1, 1)) == semantic

    monkeypatch.setattr(
        Settings, "from_environment", lambda: replace(settings, bm25_index_path=None)
    )
    assert retrieval.search("desalination") == semantic
    assert calls[-1]["keyword_weight"] == 1
