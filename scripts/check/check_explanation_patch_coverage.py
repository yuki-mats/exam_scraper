#!/usr/bin/env python3
"""
Validate explanationText patch coverage and format against source questions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


REQUIRED_FIELDS = [
    "explanationText",
    "suggestedQuestions",
    "original_question_id",
    "question_url",
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_source_questions(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("source JSON must be an object")
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    return [q for q in questions if isinstance(q, dict)]


def get_patch_entries(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        raise ValueError("patch JSON must be an array")
    return [q for q in data if isinstance(q, dict)]


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if len(source_questions) != len(patch_entries):
        errors.append(
            f"count mismatch: source={len(source_questions)} patch={len(patch_entries)}"
        )

    source_ids = [q.get("original_question_id") for q in source_questions]
    patch_ids = [q.get("original_question_id") for q in patch_entries]
    missing_ids = sorted({sid for sid in source_ids if sid} - {pid for pid in patch_ids if pid})
    extra_ids = sorted({pid for pid in patch_ids if pid} - {sid for sid in source_ids if sid})
    if missing_ids:
        errors.append(f"missing original_question_id: {missing_ids}")
    if extra_ids:
        errors.append(f"extra original_question_id: {extra_ids}")

    for idx, (src, patch) in enumerate(zip(source_questions, patch_entries), start=1):
        missing_fields = [k for k in REQUIRED_FIELDS if k not in patch]
        if missing_fields:
            errors.append(f"index {idx}: missing fields {missing_fields}")
            continue

        if patch.get("original_question_id") != src.get("original_question_id"):
            errors.append(
                "index {}: original_question_id mismatch (source={} patch={})".format(
                    idx, src.get("original_question_id"), patch.get("original_question_id")
                )
            )

        if patch.get("question_url") != src.get("question_url"):
            errors.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, src.get("question_url"), patch.get("question_url")
                )
            )

        explanations = patch.get("explanationText")
        if not isinstance(explanations, list):
            errors.append(f"index {idx}: explanationText must be a list")
        else:
            choices = src.get("choiceTextList") or []
            if isinstance(choices, list) and len(explanations) != len(choices):
                errors.append(
                    "index {}: explanationText length mismatch "
                    "(source={} patch={})".format(idx, len(choices), len(explanations))
                )

        suggested_questions = patch.get("suggestedQuestions")
        if not isinstance(suggested_questions, list) or any(
            not isinstance(question, str) or not question.strip()
            for question in suggested_questions
        ):
            errors.append(f"index {idx}: suggestedQuestions must be non-empty list[str]")

    if len(set(patch_ids)) != len(patch_ids):
        warnings.append("duplicate original_question_id detected in patch")

    return errors, warnings


def check_pair(source_path: Path, patch_path: Path) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_data = load_json(source_path)
    patch_data = load_json(patch_path)

    source_questions = get_source_questions(source_data)
    patch_entries = get_patch_entries(patch_data)

    errors, warnings = compare_entries(source_questions, patch_entries)
    for warn in warnings:
        print(f"[WARN] {warn}")
    if errors:
        for err in errors:
            print(f"[ERROR] {err}")
        return 1

    print("[OK] coverage check passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate explanationText patch coverage and format."
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to *_explanationText_added_YYYYMMDD_HHMM.json (旧形式 *_explanationText_added.json も可)",
    )
    args = parser.parse_args()
    return check_pair(Path(args.source), Path(args.patch))


if __name__ == "__main__":
    raise SystemExit(main())
