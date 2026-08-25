from __future__ import annotations

import math
from statistics import mean
from typing import Iterable, Sequence


def recall_at_k(retrieved_ids: Iterable[str], relevant_ids: set[str], k: int) -> float | None:
    """|unique relevant IDs in first k| / |gold relevant IDs|; undefined without gold."""
    if not relevant_ids:
        return None
    hits = set(list(retrieved_ids)[:k]) & relevant_ids
    return len(hits) / len(relevant_ids)


def reciprocal_rank(retrieved_ids: Iterable[str], relevant_ids: set[str]) -> float | None:
    """1 / rank of the first relevant ID; 0 when no hit; undefined without gold."""
    if not relevant_ids:
        return None
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: Iterable[str], relevant_ids: set[str], k: int) -> float | None:
    """Binary relevance nDCG: DCG@k / ideal DCG@k; undefined without gold."""
    if not relevant_ids:
        return None
    ranked = list(retrieved_ids)[:k]
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item_id in enumerate(ranked, 1) if item_id in relevant_ids)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else None


def citation_accuracy(answer: str, context_chunk_ids: Sequence[str], relevant_chunk_ids: set[str]) -> float | None:
    """Fraction of cited context positions that map to a gold-relevant chunk."""
    import re

    cited_positions = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
    if not cited_positions or not relevant_chunk_ids:
        return None
    correct = 0
    for position in cited_positions:
        if 1 <= position <= len(context_chunk_ids) and context_chunk_ids[position - 1] in relevant_chunk_ids:
            correct += 1
    return correct / len(cited_positions)


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    """Linear-interpolated percentile over observed values."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_summary(values_ms: Sequence[float]) -> dict:
    return {
        "count": len(values_ms),
        "average_ms": mean(values_ms) if values_ms else None,
        "p50_ms": percentile(values_ms, 0.50),
        "p95_ms": percentile(values_ms, 0.95),
    }


def average_available(values: Iterable[float | int | None]) -> float | None:
    available = [float(value) for value in values if value is not None]
    return mean(available) if available else None
