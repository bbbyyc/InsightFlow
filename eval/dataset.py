from __future__ import annotations

import hashlib
import json
from pathlib import Path


REQUIRED_CASE_FIELDS = {
    "id", "query", "relevant_document_ids", "relevant_chunk_ids",
    "question_type", "data_source", "label_review_status",
}
APPROVED_STATUSES = {"approved", "approved_human"}


class DatasetError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_dataset(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        dataset = json.load(handle)
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset: dict) -> None:
    for field in ("schema_version", "dataset_id", "dataset_version", "dataset_purpose", "cases"):
        if field not in dataset:
            raise DatasetError(f"dataset missing field: {field}")
    if not isinstance(dataset["cases"], list) or not dataset["cases"]:
        raise DatasetError("dataset cases must be a non-empty list")

    seen = set()
    for index, case in enumerate(dataset["cases"]):
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            raise DatasetError(f"case[{index}] missing fields: {sorted(missing)}")
        if case["id"] in seen:
            raise DatasetError(f"duplicate case id: {case['id']}")
        seen.add(case["id"])
        if not isinstance(case["relevant_document_ids"], list) or not isinstance(case["relevant_chunk_ids"], list):
            raise DatasetError(f"case {case['id']} gold IDs must be lists")
        if case["label_review_status"] not in APPROVED_STATUSES | {"pending_review", "rejected"}:
            raise DatasetError(f"case {case['id']} has invalid review status")


def formal_readiness_errors(dataset: dict) -> list[str]:
    errors = []
    if dataset["dataset_purpose"] != "project_evaluation":
        errors.append("dataset_purpose must be project_evaluation")
    for case in dataset["cases"]:
        if case["label_review_status"] not in APPROVED_STATUSES:
            errors.append(f"{case['id']}: label_review_status={case['label_review_status']}")
        if not case["relevant_document_ids"]:
            errors.append(f"{case['id']}: relevant_document_ids is empty")
        if not case["relevant_chunk_ids"]:
            errors.append(f"{case['id']}: relevant_chunk_ids is empty")
        review = case.get("label_review") or {}
        if not review.get("reviewer") or not review.get("reviewed_at"):
            errors.append(f"{case['id']}: human review provenance is incomplete")
    return errors
