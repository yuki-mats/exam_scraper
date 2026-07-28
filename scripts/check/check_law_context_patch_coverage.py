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
    get_source_questions,
    has_non_empty_law_references,
    load_json,
    validate_law_references_shape,
)
from scripts.check.patch_entry_matching import (  # noqa: E402
    bind_source_records,
    match_patch_entries,
)
from scripts.common.question_identity import review_question_id  # noqa: E402
from scripts.merge.patch_views import apply_question_type  # noqa: E402


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

    matches, identity_errors, identity_warnings = match_patch_entries(
        source_questions,
        patch_entries,
    )
    errors.extend(identity_errors)
    warnings.extend(identity_warnings)

    for match in matches:
        idx = match.patch_index + 1
        src = match.source
        patch = match.patch
        missing_fields = [key for key in REQUIRED_FIELDS if key not in patch]
        if missing_fields:
            errors.append(f"index {idx}: missing fields {missing_fields}")
            continue

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

    return errors, warnings


def check_pair(
    source_path: Path,
    patch_path: Path,
    question_type_patch_path: Path | None = None,
) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_data = load_json(source_path)
    source_questions = bind_source_records(
        get_source_questions(source_data),
        source_path,
    )
    if question_type_patch_path is not None:
        if not question_type_patch_path.exists():
            print(f"[ERROR] questionType patch not found: {question_type_patch_path}")
            return 2
        question_type_entries = get_patch_entries(load_json(question_type_patch_path))
        matches, identity_errors, _ = match_patch_entries(
            source_questions,
            question_type_entries,
        )
        if identity_errors:
            for error in identity_errors:
                print(f"[ERROR] questionType patch: {error}")
            return 1
        apply_question_type(
            source_data,
            {
                str(review_question_id(match.source)): match.patch
                for match in matches
                if review_question_id(match.source)
            },
        )
        source_questions = bind_source_records(
            get_source_questions(source_data),
            source_path,
        )
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
    parser.add_argument(
        "--question-type-patch",
        help="Apply the corresponding 10_questionType_fixed patch before validating choice counts.",
    )
    args = parser.parse_args()
    return check_pair(
        Path(args.source),
        Path(args.patch),
        Path(args.question_type_patch) if args.question_type_patch else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
