"""Batch OpenAI embedding requests while preserving input order."""

from __future__ import annotations

from openai import OpenAI

from .models import EMBEDDING_DIMENSIONS


def embed_texts(
    client: OpenAI, texts: list[str], model: str, batch_size: int = 64
) -> list[list[float]]:
    """Embed text in batches, rejecting incomplete or incompatible responses."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = client.embeddings.create(model=model, input=batch)
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(batch))):
            raise ValueError(
                "Embedding response did not contain exactly one vector per input"
            )
        for item in ordered:
            if len(item.embedding) != EMBEDDING_DIMENSIONS:
                raise ValueError("Unexpected embedding dimensions")
            vectors.append(item.embedding)
    return vectors
