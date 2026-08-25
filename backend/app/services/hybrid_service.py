from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from app.services.retrieval_service import RetrievalService
    from app.services.bm25_service import BM25Service, BM25Result


EVIDENCE_MISSING_MESSAGE = "未检索到足够证据"
MIN_VECTOR_EVIDENCE_SCORE = 0.2


def insufficient_evidence_answer(diagnostics: dict) -> str:
    missing_ids = ", ".join(
        item["document_id"] for item in diagnostics.get("missing_documents", [])
    )
    return f"{EVIDENCE_MISSING_MESSAGE}：{missing_ids}" if missing_ids else EVIDENCE_MISSING_MESSAGE


def _unique_document_ids(document_ids: Optional[List[str]]) -> list[str]:
    return list(dict.fromkeys(str(document_id) for document_id in (document_ids or []) if document_id))


def recall_quota(top_k: int, document_count: int) -> int | None:
    """Return a per-document candidate quota only for an explicit multi-document scope."""
    if document_count <= 1:
        return None
    return max(2, math.ceil(top_k / document_count))


def _bm25_to_dict(result: Any) -> dict:
    return {
        "chunk_id": result.chunk_id,
        "content": result.content,
        "document_id": result.document_id,
        "document_title": result.document_title,
        "document_type": result.document_type,
        "chunk_index": result.chunk_index,
        "page_number": result.page_number,
        "section_title": result.section_title,
        "score": result.score,
    }


def annotate_single_channel(results: list[dict], channel: str) -> list[dict]:
    annotated = []
    seen: set[str] = set()
    for raw_rank, result in enumerate(results, start=1):
        chunk_id = str(result["chunk_id"])
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        item = dict(result)
        item["retrieval_channels"] = [channel]
        item["channel_ranks"] = {channel: raw_rank}
        item["channel_scores"] = {channel: float(result.get("score", 0.0))}
        item["rrf_rank"] = len(annotated) + 1
        item["fused_rank"] = len(annotated) + 1
        annotated.append(item)
    return annotated


def rrf_fusion(
    vector_results: List[dict],
    bm25_results: List[Any],
    k: int = 60,
    top_k: int | None = 10,
) -> List[dict]:
    """Fuse two ranked lists and retain stable provenance for every chunk."""
    scores: dict[str, dict] = {}
    seen_by_channel = {"vector": set(), "bm25": set()}

    def add(result: dict, channel: str, raw_rank: int) -> None:
        chunk_id = str(result["chunk_id"])
        if chunk_id in seen_by_channel[channel]:
            return
        seen_by_channel[channel].add(chunk_id)

        if chunk_id not in scores:
            scores[chunk_id] = {
                **result,
                "chunk_id": chunk_id,
                "rrf_score": 0.0,
                "retrieval_channels": [],
                "channel_ranks": {},
                "channel_scores": {},
            }
        item = scores[chunk_id]
        item["retrieval_channels"].append(channel)
        item["channel_ranks"][channel] = raw_rank
        item["channel_scores"][channel] = float(result.get("score", 0.0))
        item["rrf_score"] += 1.0 / (k + raw_rank)

    for rank, result in enumerate(vector_results, start=1):
        add(dict(result), "vector", rank)

    for rank, result in enumerate(bm25_results, start=1):
        add(_bm25_to_dict(result), "bm25", rank)

    merged = sorted(scores.values(), key=lambda item: (-item["rrf_score"], item["chunk_id"]))
    for rank, item in enumerate(merged, start=1):
        item["score"] = round(item["rrf_score"], 6)
        item["rrf_rank"] = rank

    selected = merged if top_k is None else merged[:top_k]
    for rank, item in enumerate(selected, start=1):
        item["fused_rank"] = rank
    return selected


def diversify_explicit_scope(candidates: list[dict], top_k: int) -> list[dict]:
    """Apply a soft dominance cap without importing weak candidates solely for coverage."""
    if top_k <= 0 or not candidates:
        return []
    natural = candidates[:top_k]
    cutoff = natural[-1]["rrf_score"]
    eligible = [candidate for candidate in candidates if candidate["rrf_score"] >= cutoff * 0.9]
    soft_cap = max(1, math.ceil(top_k * 0.6))
    selected: list[dict] = []
    deferred: list[dict] = []
    doc_counts: dict[str, int] = {}

    for candidate in eligible:
        document_id = str(candidate.get("document_id", ""))
        if doc_counts.get(document_id, 0) >= soft_cap:
            deferred.append(candidate)
            continue
        selected.append(candidate)
        doc_counts[document_id] = doc_counts.get(document_id, 0) + 1
        if len(selected) >= top_k:
            break

    if len(selected) < top_k:
        selected_ids = {item["chunk_id"] for item in selected}
        for candidate in candidates:
            if candidate["chunk_id"] in selected_ids:
                continue
            selected.append(candidate)
            selected_ids.add(candidate["chunk_id"])
            if len(selected) >= top_k:
                break

    for rank, item in enumerate(selected, start=1):
        item["fused_rank"] = rank
    return selected


def build_scope_diagnostics(
    document_ids: Optional[List[str]],
    results: list[dict],
    candidate_document_ids: Optional[List[str]] = None,
) -> dict:
    selected = _unique_document_ids(document_ids)
    result_docs = {str(result.get("document_id", "")) for result in results}
    candidate_docs = set(candidate_document_ids or result_docs)
    missing = []
    for document_id in selected:
        if document_id in candidate_docs:
            continue
        missing.append({
            "document_id": document_id,
            "reason": "no_candidates_above_evidence_threshold",
            "message": EVIDENCE_MISSING_MESSAGE,
        })

    return {
        "explicit_document_scope": bool(selected),
        "coverage_strategy": "quota_with_soft_diversity" if len(selected) > 1 else "filtered_global_rank",
        "selected_document_ids": selected,
        "documents_with_evidence": [document_id for document_id in selected if document_id in candidate_docs],
        "documents_in_results": [document_id for document_id in selected if document_id in result_docs],
        "documents_below_fused_cutoff": [
            document_id for document_id in selected
            if document_id in candidate_docs and document_id not in result_docs
        ],
        "candidate_document_ids": sorted(candidate_docs),
        "missing_documents": missing,
        "evidence_status": "insufficient" if missing else "sufficient",
        "evidence_message": EVIDENCE_MISSING_MESSAGE if missing else None,
    }


class HybridSearchService:

    def __init__(self, retrieval: "RetrievalService", bm25: "BM25Service"):
        self.retrieval = retrieval
        self.bm25 = bm25

    async def search_with_diagnostics(
        self,
        query: str,
        query_embedding: List[float],
        vector_top_k: int = 20,
        bm25_top_k: int = 20,
        final_top_k: int = 10,
        threshold: float = 0.0,
        document_ids: Optional[List[str]] = None,
    ) -> tuple[List[dict], dict]:
        selected_documents = _unique_document_ids(document_ids)
        explicit_scope = bool(selected_documents)
        vector_quota = recall_quota(vector_top_k, len(selected_documents))
        bm25_quota = recall_quota(bm25_top_k, len(selected_documents))

        vector_results = await self.retrieval.vector_search(
            query_embedding,
            top_k=vector_top_k,
            threshold=threshold,
            document_ids=selected_documents or None,
            per_document_limit=vector_quota,
        )
        bm25_results = await self.bm25.search(
            query,
            top_k=bm25_top_k,
            document_ids=selected_documents or None,
            per_document_limit=bm25_quota,
        )

        fused_candidates = rrf_fusion(vector_results, bm25_results, top_k=None)
        evidence_candidates = [
            item for item in fused_candidates
            if "bm25" in item.get("retrieval_channels", [])
            or item.get("channel_scores", {}).get("vector", 0.0) >= max(threshold, MIN_VECTOR_EVIDENCE_SCORE)
        ]
        candidate_document_ids = sorted({str(item.get("document_id", "")) for item in evidence_candidates})
        ranked_candidates = evidence_candidates if explicit_scope else fused_candidates
        if explicit_scope and len(selected_documents) > 1:
            results = diversify_explicit_scope(ranked_candidates, final_top_k)
        else:
            results = ranked_candidates[:final_top_k]
            for rank, item in enumerate(results, start=1):
                item["fused_rank"] = rank

        diagnostics = build_scope_diagnostics(selected_documents, results, candidate_document_ids)
        return results, diagnostics

    async def search(self, *args, **kwargs) -> List[dict]:
        results, _ = await self.search_with_diagnostics(*args, **kwargs)
        return results
