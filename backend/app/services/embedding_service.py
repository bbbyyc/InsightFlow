import hashlib
import asyncio
from functools import lru_cache
from typing import List

from app.config import settings


@lru_cache(maxsize=2)
def _load_model(model_name: str, cache_dir: str):
    """Load one model per process; workers reuse it across jobs and requests."""
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


class EmbeddingService:

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.embedding_model

    def _encode(self, texts: List[str]) -> List[List[float]]:
        model = _load_model(self.model_name, settings.embedding_cache_dir)
        vectors = [vector.tolist() for vector in model.embed(texts, batch_size=settings.embedding_batch_size)]
        dimensions = len(vectors[0]) if vectors else settings.embedding_dimensions
        if dimensions != settings.embedding_dimensions:
            raise ValueError(
                f"Embedding model {self.model_name} returned {dimensions} dimensions; "
                f"EMBEDDING_DIMENSIONS is {settings.embedding_dimensions}"
            )
        return vectors

    async def embed(self, texts: List[str]) -> List[List[float]]:
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None, self._encode, texts
        )
        return embeddings

    async def embed_single(self, text: str) -> List[float]:
        results = await self.embed([text])
        return results[0]

    async def embed_batch(self, texts: List[str], batch_size: int | None = None) -> List[List[float]]:
        batch_size = batch_size or settings.embedding_batch_size
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            embeddings.extend(await self.embed(batch))
        return embeddings

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()
