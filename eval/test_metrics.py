import unittest

from metrics import (
    citation_accuracy,
    latency_summary,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


class MetricFormulaTests(unittest.TestCase):
    def test_retrieval_metrics(self):
        ranked = ["noise", "target", "other"]
        relevant = {"target"}
        self.assertEqual(recall_at_k(ranked, relevant, 2), 1.0)
        self.assertEqual(reciprocal_rank(ranked, relevant), 0.5)
        self.assertGreater(ndcg_at_k(ranked, relevant, 3), 0)
        self.assertLess(ndcg_at_k(ranked, relevant, 3), 1)

    def test_metrics_are_unavailable_without_gold(self):
        self.assertIsNone(recall_at_k(["x"], set(), 5))
        self.assertIsNone(reciprocal_rank(["x"], set()))
        self.assertIsNone(ndcg_at_k(["x"], set(), 5))

    def test_citation_accuracy_requires_valid_relevant_mapping(self):
        self.assertEqual(citation_accuracy("Facts [1], noise [2], bad [9].", ["gold", "noise"], {"gold"}), 1 / 3)
        self.assertIsNone(citation_accuracy("No citations", ["gold"], {"gold"}))

    def test_latency_summary_uses_observed_values(self):
        summary = latency_summary([10, 20, 30, 40, 50])
        self.assertEqual(summary["average_ms"], 30)
        self.assertEqual(summary["p50_ms"], 30)
        self.assertEqual(summary["p95_ms"], 48)


if __name__ == "__main__":
    unittest.main()
