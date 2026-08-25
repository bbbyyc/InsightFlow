from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk


class RetrievalService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def vector_search(
        self, query_embedding: List[float], top_k: int = 10, threshold: float = 0.0,
        document_ids: Optional[List[str]] = None,
        per_document_limit: int | None = None,
    ) -> List[dict]:
        embedding_str = f"[{','.join(str(v) for v in query_embedding)}]"

        doc_filter = ""
        params: dict = {"embedding": embedding_str, "top_k": top_k, "threshold": threshold}

        if document_ids:
            doc_filter = "AND c.document_id::text = ANY(:doc_ids)"
            params["doc_ids"] = document_ids

        if per_document_limit and document_ids:
            params["per_document_limit"] = per_document_limit
            sql = text(f"""
                WITH ranked AS (
                    SELECT
                        c.id,
                        c.content,
                        c.document_id,
                        c.chunk_index,
                        c.page_number,
                        c.section_title,
                        c.token_count,
                        1 - (c.embedding <=> :embedding) AS similarity,
                        d.title AS document_title,
                        d.file_type AS document_type,
                        ROW_NUMBER() OVER (
                            PARTITION BY c.document_id
                            ORDER BY c.embedding <=> :embedding, c.id
                        ) AS document_rank
                    FROM chunks c
                    JOIN documents d ON c.document_id = d.id
                    WHERE c.embedding IS NOT NULL
                      AND 1 - (c.embedding <=> :embedding) >= :threshold
                      {doc_filter}
                )
                SELECT id, content, document_id, chunk_index, page_number,
                       section_title, token_count, similarity, document_title,
                       document_type
                FROM ranked
                WHERE document_rank <= :per_document_limit
                ORDER BY similarity DESC, id
                LIMIT :top_k
            """)
        else:
            sql = text(f"""
                SELECT
                    c.id,
                    c.content,
                    c.document_id,
                    c.chunk_index,
                    c.page_number,
                    c.section_title,
                    c.token_count,
                    1 - (c.embedding <=> :embedding) AS similarity,
                    d.title AS document_title,
                    d.file_type AS document_type
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.embedding IS NOT NULL
                  AND 1 - (c.embedding <=> :embedding) >= :threshold
                  {doc_filter}
                ORDER BY c.embedding <=> :embedding, c.id
                LIMIT :top_k
            """)

        result = await self.db.execute(sql, params)

        rows = result.fetchall()
        return [
            {
                "chunk_id": str(row[0]),
                "content": row[1],
                "document_id": str(row[2]),
                "chunk_index": row[3],
                "page_number": row[4],
                "section_title": row[5],
                "token_count": row[6],
                "score": round(float(row[7]), 4),
                "document_title": row[8],
                "document_type": row[9],
            }
            for row in rows
        ]

    async def get_chunks_by_ids(self, chunk_ids: List[str]) -> List[dict]:
        result = await self.db.execute(
            text("""
                SELECT c.id, c.content, c.document_id, c.chunk_index,
                       c.page_number, c.section_title, d.title AS document_title
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.id = ANY(:ids)
            """),
            {"ids": chunk_ids},
        )
        rows = result.fetchall()
        return [
            {
                "chunk_id": str(row[0]),
                "content": row[1],
                "document_id": str(row[2]),
                "chunk_index": row[3],
                "page_number": row[4],
                "section_title": row[5],
                "document_title": row[6],
            }
            for row in rows
        ]
