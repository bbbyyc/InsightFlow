import json
import tempfile
import unittest
from pathlib import Path

from dataset import DatasetError, formal_readiness_errors, load_dataset


EVAL_DIR = Path(__file__).resolve().parent


class DatasetGateTests(unittest.TestCase):
    def test_candidate_dataset_is_valid_but_not_formal_ready(self):
        dataset = load_dataset(EVAL_DIR / "datasets" / "candidate_project_v1.json")
        errors = formal_readiness_errors(dataset)
        self.assertTrue(any("pending_review" in error for error in errors))
        self.assertTrue(any("relevant_chunk_ids is empty" in error for error in errors))

    def test_synthetic_fixture_cannot_be_project_evaluation(self):
        dataset = load_dataset(EVAL_DIR / "datasets" / "synthetic_fixture_v1.json")
        errors = formal_readiness_errors(dataset)
        self.assertIn("dataset_purpose must be project_evaluation", errors)

    def test_missing_required_case_field_is_rejected(self):
        payload = {
            "schema_version": "1.0",
            "dataset_id": "bad",
            "dataset_version": "1",
            "dataset_purpose": "project_evaluation",
            "cases": [{"id": "q"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DatasetError):
                load_dataset(path)


if __name__ == "__main__":
    unittest.main()
