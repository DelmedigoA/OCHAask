from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from ochask import ingest
from ochask.search import search

REPORT_ID = UUID("e5e91d1e-55a1-531f-8f31-456f21958cd7")


class Chunk:
    def __init__(self, text, items):
        self.text, self.items = text, items

    def model_dump(self):
        return {"meta": {"doc_items": self.items}}


class Chunker:
    def chunk(self, dl_doc):
        return iter(dl_doc)

    def contextualize(self, chunk):
        return chunk.text


def test_normalization_pages_and_stable_ids():
    chunks = [Chunk("heading\ntext", [{"prov": [{"page_no": 2}, {"page_no": 1}]}, {"prov": [{"page_no": 2}]}]),
              Chunk("no provenance", [{}])]
    result = ingest.normalize_document(chunks, REPORT_ID, Chunker(), len)
    assert result[0]["pages"] == [1, 2]
    assert result[1]["pages"] == []
    assert result[0]["token_count"] == len("heading\ntext")
    assert result == ingest.normalize_document(chunks, REPORT_ID, Chunker(), len)
    assert result[0]["chunk_id"] != result[1]["chunk_id"]


def test_filename_validation():
    assert ingest.report_id_from_path(Path(f"{REPORT_ID}_report.pdf")) == REPORT_ID
    with pytest.raises(ValueError):
        ingest.report_id_from_path(Path("report.pdf"))


def test_embedding_batches_and_response_order():
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(i)] * 1536)
                                     for i in reversed(range(len(kwargs["input"])))])
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    vectors = ingest.embed_texts(client, ["a", "b", "c"], "test-model", 2)
    assert [v[0] for v in vectors] == [0, 1, 0]
    assert [c["input"] for c in calls] == [["a", "b"], ["c"]]
    assert ingest.embed_texts(client, [], "test-model", 2) == []
    with pytest.raises(ValueError):
        ingest.embed_texts(client, ["a"], "test-model", 0)


def test_failed_embeddings_do_not_replace(monkeypatch):
    converter = SimpleNamespace(convert=lambda _: SimpleNamespace(document=[Chunk("text", [])]))
    def fail(**kwargs):
        raise RuntimeError("API failure")
    client = SimpleNamespace(embeddings=SimpleNamespace(create=fail))
    monkeypatch.setattr(ingest, "rpc", lambda *a, **k: pytest.fail("must not write"))
    with pytest.raises(RuntimeError):
        ingest.index_pdf(Path(f"{REPORT_ID}_report.pdf"), {"id": str(REPORT_ID)}, converter, Chunker(), len, client, "url", "key", "test-model")


def test_index_and_search_smoke(monkeypatch):
    import ochask.search as search_module
    converter = SimpleNamespace(convert=lambda _: SimpleNamespace(document=[Chunk("some text", [{"prov": [{"page_no": 3}]}])]))
    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
        data=[SimpleNamespace(index=i, embedding=[0.1] * 1536) for i in range(len(kwargs["input"]))])))
    calls = []
    def fake_rpc(url, key, name, payload):
        calls.append((name, payload))
        return [{"pages": [3], "content": "some text", "fusion_score": 2 / 61}]
    monkeypatch.setattr(ingest, "rpc", fake_rpc)
    report = {"id": str(REPORT_ID), "landing_url": "https://example.com", "title": "Report"}
    assert ingest.index_pdf(Path(f"{REPORT_ID}_report.pdf"), report, converter, Chunker(), len, client, "url", "key", "test-model") == 1
    assert calls[0][1]["chunks_data"][0]["pages"] == [3]
    for name in ("OPENAI_API_KEY", "OPENAI_EMBEDDING_MODEL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.setenv(name, "test")
    monkeypatch.setattr(search_module, "OpenAI", lambda: client)
    monkeypatch.setattr(search_module, "rpc", fake_rpc)
    assert search("some text", semantic_weight=2)[0]["pages"] == [3]
    assert calls[1][1]["semantic_weight"] == 2
    assert calls[1][1]["model_name"] == "test"
    assert calls[0][1]["model_name"] == "test-model"
    with pytest.raises(ValueError):
        search(" ")
    with pytest.raises(ValueError):
        search("text", semantic_weight=0, keyword_weight=0)
