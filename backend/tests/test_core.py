import asyncio
import unittest
from dataclasses import dataclass

from app.services.citation_service import extract_citations
from app.services.hybrid_service import (
    EVIDENCE_MISSING_MESSAGE,
    HybridSearchService,
    build_scope_diagnostics,
    diversify_explicit_scope,
    insufficient_evidence_answer,
    rrf_fusion,
)
from app.parsers.pdf_parser import PDFParser


@dataclass
class LexicalResult:
    chunk_id: str
    content: str
    document_id: str
    score: float
    chunk_index: int = 0
    page_number: int | None = None
    section_title: str | None = None
    document_title: str = ""
    document_type: str = "md"


def chunk(chunk_id: str, document_id: str, score: float, section: str = "S") -> dict:
    return {
        "chunk_id": chunk_id,
        "content": f"content-{chunk_id}",
        "document_id": document_id,
        "document_title": f"Document {document_id}",
        "document_type": "md",
        "chunk_index": 0,
        "page_number": None,
        "section_title": section,
        "score": score,
    }


class FakeVectorRetrieval:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def vector_search(self, query_embedding, **kwargs):
        self.calls.append(kwargs)
        allowed = set(kwargs.get("document_ids") or [])
        results = [item for item in self.results if not allowed or item["document_id"] in allowed]
        limit = kwargs.get("per_document_limit")
        if limit:
            counts = {}
            limited = []
            for item in results:
                did = item["document_id"]
                if counts.get(did, 0) >= limit:
                    continue
                counts[did] = counts.get(did, 0) + 1
                limited.append(item)
            results = limited
        return results[:kwargs["top_k"]]


class FakeBM25:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def search(self, query, **kwargs):
        self.calls.append(kwargs)
        allowed = set(kwargs.get("document_ids") or [])
        results = [item for item in self.results if not allowed or item.document_id in allowed]
        limit = kwargs.get("per_document_limit")
        if limit:
            counts = {}
            limited = []
            for item in results:
                if counts.get(item.document_id, 0) >= limit:
                    continue
                counts[item.document_id] = counts.get(item.document_id, 0) + 1
                limited.append(item)
            results = limited
        return results[:kwargs["top_k"]]


class RetrievalContractTests(unittest.TestCase):
    def run_search(self, vector, lexical, **kwargs):
        retrieval = FakeVectorRetrieval(vector)
        bm25 = FakeBM25(lexical)
        service = HybridSearchService(retrieval, bm25)
        results, diagnostics = asyncio.run(service.search_with_diagnostics(
            query="question", query_embedding=[0.1], **kwargs,
        ))
        return results, diagnostics, retrieval, bm25

    def test_original_rrf_regression_shared_result_ranks_first(self):
        vector = [chunk("shared", "a", 0.9), chunk("vector", "a", 0.8)]
        lexical = [LexicalResult("shared", "a", "a", 2.0), LexicalResult("bm25", "c", "b", 1.0)]
        result = rrf_fusion(vector, lexical, top_k=3)
        self.assertEqual(result[0]["chunk_id"], "shared")

    def test_single_document_question_filters_without_coverage_quota(self):
        results, diagnostics, retrieval, bm25 = self.run_search(
            [chunk("a1", "a", 0.9), chunk("b1", "b", 0.8)],
            [LexicalResult("a1", "a", "a", 2.0)],
            document_ids=["a"], final_top_k=5,
        )
        self.assertEqual({item["document_id"] for item in results}, {"a"})
        self.assertIsNone(retrieval.calls[0]["per_document_limit"])
        self.assertEqual(diagnostics["evidence_status"], "sufficient")

    def test_explicit_multiple_documents_receive_recall_quota(self):
        results, diagnostics, retrieval, bm25 = self.run_search(
            [chunk("a1", "a", 0.95), chunk("a2", "a", 0.94), chunk("b1", "b", 0.8)],
            [LexicalResult("b1", "b", "b", 3.0)],
            document_ids=["a", "b"], vector_top_k=6, bm25_top_k=6, final_top_k=4,
        )
        self.assertEqual(retrieval.calls[0]["per_document_limit"], 3)
        self.assertEqual(bm25.calls[0]["per_document_limit"], 3)
        self.assertTrue(diagnostics["explicit_document_scope"])
        self.assertEqual({item["document_id"] for item in results}, {"a", "b"})

    def test_unspecified_scope_uses_global_hybrid_search(self):
        results, diagnostics, retrieval, bm25 = self.run_search(
            [chunk("a1", "a", 0.9), chunk("b1", "b", 0.8)], [],
            document_ids=None, final_top_k=2,
        )
        self.assertIsNone(retrieval.calls[0]["document_ids"])
        self.assertIsNone(retrieval.calls[0]["per_document_limit"])
        self.assertFalse(diagnostics["explicit_document_scope"])
        self.assertEqual(len(results), 2)

    def test_selected_document_without_evidence_is_reported_not_forced(self):
        results, diagnostics, _, _ = self.run_search(
            [chunk("a1", "a", 0.91), chunk("b-low", "b", 0.05)], [],
            document_ids=["a", "b"], final_top_k=3,
        )
        self.assertEqual(diagnostics["evidence_status"], "insufficient")
        self.assertEqual(diagnostics["missing_documents"][0]["document_id"], "b")
        self.assertEqual(diagnostics["evidence_message"], EVIDENCE_MISSING_MESSAGE)
        self.assertNotIn("b-low", {item["chunk_id"] for item in results})
        self.assertEqual(insufficient_evidence_answer(diagnostics), "未检索到足够证据：b")

    def test_soft_diversity_reduces_single_document_dominance(self):
        candidates = rrf_fusion(
            [chunk("a1", "a", .99), chunk("a2", "a", .98), chunk("a3", "a", .97), chunk("b1", "b", .96)],
            [], top_k=None,
        )
        results = diversify_explicit_scope(candidates, top_k=3)
        self.assertEqual([item["chunk_id"] for item in results], ["a1", "a2", "b1"])

    def test_weak_other_document_is_not_promoted_for_coverage(self):
        candidates = rrf_fusion(
            [chunk("a1", "a", .99), chunk("a2", "a", .98), chunk("a3", "a", .97), chunk("b1", "b", .01)],
            [], top_k=None,
        )
        candidates[-1]["rrf_score"] = candidates[0]["rrf_score"] * 0.1
        results = diversify_explicit_scope(candidates, top_k=3)
        self.assertEqual([item["chunk_id"] for item in results], ["a1", "a2", "a3"])

    def test_duplicate_chunk_is_merged_with_channel_provenance(self):
        result = rrf_fusion(
            [chunk("same", "a", .9), chunk("same", "a", .8)],
            [LexicalResult("same", "body", "a", 4.0)], top_k=5,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["retrieval_channels"], ["vector", "bm25"])
        self.assertEqual(result[0]["channel_ranks"], {"vector": 1, "bm25": 1})
        self.assertEqual(result[0]["fused_rank"], 1)

    def test_citation_number_maps_to_original_chunk(self):
        chunks = [chunk("one", "a", .9, "Intro"), chunk("two", "b", .8, "Facts")]
        citations = extract_citations("Only the second claim [2]; invalid [9].", chunks)
        self.assertEqual(citations, [{
            "citation_number": 2,
            "document_title": "Document b",
            "document_id": "b",
            "section_title": "Facts",
            "content_snippet": "content-two",
            "chunk_id": "two",
        }])

    def test_pdf_oversized_single_paragraph_is_split_and_keeps_page(self):
        chunks = PDFParser().chunk("a" * 1423, chunk_size=1000, chunk_overlap=200)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([item.chunk_index for item in chunks], [0, 1])
        self.assertEqual([item.page_number for item in chunks], [1, 1])
        self.assertTrue(all(len(item.content) <= 1000 for item in chunks))


if __name__ == "__main__":
    unittest.main()
