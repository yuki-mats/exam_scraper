#!/usr/bin/env python3
"""
Validate pre-explanation law context patch coverage and format.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check.check_explanation_patch_coverage import (  # noqa: E402
    get_patch_entries,
    get_question_identity,
    get_source_questions,
    has_non_empty_law_references,
    load_json,
    validate_law_references_shape,
)


REQUIRED_FIELDS = [
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "original_question_id",
    "question_url",
]


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

    source_ids = [get_question_identity(q) for q in source_questions]
    patch_ids = [q.get("original_question_id") for q in patch_entries]
    missing_ids = sorted({sid for sid in source_ids if sid} - {pid for pid in patch_ids if pid})
    extra_ids = sorted({pid for pid in patch_ids if pid} - {sid for sid in source_ids if sid})
    if missing_ids:
        errors.append(f"missing original_question_id: {missing_ids}")
    if extra_ids:
        errors.append(f"extra original_question_id: {extra_ids}")

    for idx, (src, patch) in enumerate(zip(source_questions, patch_entries), start=1):
        missing_fields = [key for key in REQUIRED_FIELDS if key not in patch]
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

        is_law_related = patch.get("isLawRelated")
        if not isinstance(is_law_related, bool):
            errors.append(f"index {idx}: isLawRelated must be bool")

        law_grounded_not_needed = patch.get("lawGroundedExplanationNotNeeded")
        if not isinstance(law_grounded_not_needed, bool):
            errors.append(f"index {idx}: lawGroundedExplanationNotNeeded must be bool")
        elif isinstance(is_law_related, bool) and law_grounded_not_needed == is_law_related:
            errors.append(
                f"index {idx}: lawGroundedExplanationNotNeeded must be the inverse of isLawRelated"
            )

        law_references = patch.get("lawReferences")
        has_law_references = has_non_empty_law_references(law_references)
        if "lawReferences" in patch:
            choices = src.get("choiceTextList") or []
            validate_law_references_shape(
                law_references=law_references,
                choice_count=len(choices) if isinstance(choices, list) else 0,
                index=idx,
                errors=errors,
            )
        if is_law_related is False and has_law_references:
            errors.append(
                f"index {idx}: isLawRelated cannot be false when lawReferences is non-empty"
            )
        if law_grounded_not_needed is True and has_law_references:
            errors.append(
                f"index {idx}: lawGroundedExplanationNotNeeded cannot be true when lawReferences is non-empty"
            )

        context_note = patch.get("lawContextForExplanation")
        if context_note is not None and (
            not isinstance(context_note, str) or not context_note.strip()
        ):
            errors.append(
                f"index {idx}: lawContextForExplanation must be non-empty string when present"
            )

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

    source_questions = get_source_questions(load_json(source_path))
    patch_entries = get_patch_entries(load_json(patch_path))
    errors, warnings = compare_entries(source_questions, patch_entries)
    for warning in warnings:
        print(f"[WARN] {warning}")
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print(f"[OK] law context patch valid: {patch_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate law context patch coverage and format."
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--patch", required=True, help="Path to law context patch JSON.")
    args = parser.parse_args()
    return check_pair(Path(args.source), Path(args.patch))


if __name__ == "__main__":
    raise SystemExit(main())
