#!/usr/bin/env python3
"""
Validate questionSetId patch coverage and category integrity.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# `python scripts/check/...` でも `python -m scripts.check...` でも動くようにする。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check.question_set_validation import collect_category_ids
from scripts.common.question_identity import review_question_id


REQUIRED_FIELDS = [
    "questionSetId",
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


def get_question_identity(question: Dict[str, Any]) -> Any:
    return review_question_id(question)


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
    category_ids: Set[str],
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if len(source_questions) != len(patch_entries):
        errors.append(
            f"count mismatch: source={len(source_questions)} patch={len(patch_entries)}"
        )

    source_ids = [get_question_identity(q) for q in source_questions]
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

        source_question_id = get_question_identity(src)
        if patch.get("original_question_id") != source_question_id:
            errors.append(
                "index {}: original_question_id mismatch (source={} patch={})".format(
                    idx, source_question_id, patch.get("original_question_id")
                )
            )

        if patch.get("question_url") != src.get("question_url"):
            errors.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, src.get("question_url"), patch.get("question_url")
                )
            )

        qsid = patch.get("questionSetId")
        if qsid in (None,):
            errors.append(f"index {idx}: questionSetId must be '' or valid id")
        elif qsid != "" and str(qsid) not in category_ids:
            errors.append(f"index {idx}: questionSetId not in category: {qsid}")

    if len(set(patch_ids)) != len(patch_ids):
        warnings.append("duplicate original_question_id detected in patch")

    return errors, warnings


def check_pair(
    source_path: Path,
    patch_path: Path,
    category_path: Path,
    questionset_only: bool = False,
) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2
    if not category_path.exists():
        print(f"[ERROR] category not found: {category_path}")
        return 2

    source_data = load_json(source_path)
    patch_data = load_json(patch_path)
    category_data = load_json(category_path)

    source_questions = get_source_questions(source_data)
    patch_entries = get_patch_entries(patch_data)
    category_ids = collect_category_ids(
        category_data,
        questionset_only=questionset_only,
    )

    errors, warnings = compare_entries(source_questions, patch_entries, category_ids)
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
        description=(
            "Validate questionSetId patch coverage and category integrity. "
            "Run from the repo root or any cwd."
        )
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to *_questionSetId_linked_YYYYMMDD_HHMM.json (旧形式 *_questionSetId_linked.json も可)",
    )
    parser.add_argument(
        "--category", required=True, help="Path to category.json"
    )
    parser.add_argument(
        "--questionset-only",
        action="store_true",
        help="category.json の questionSets[].questionSetId のみを有効IDとして扱う",
    )
    args = parser.parse_args()
    return check_pair(
        Path(args.source),
        Path(args.patch),
        Path(args.category),
        questionset_only=args.questionset_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
