#!/usr/bin/env python3
"""
Validate correctChoiceText patch coverage and format against source questions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id
from scripts.merge.patch_views import apply_question_type
from scripts.check.patch_entry_matching import (
    bind_source_records,
    match_patch_entries,
)


REQUIRED_FIELDS = [
    "correctChoiceText",
    "original_question_id",
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


def normalize_snippet_list(source_snippets: Any) -> List[List[str]] | None:
    if not isinstance(source_snippets, list):
        return None
    normalized: List[List[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list) and entry:
            first = entry[0]
            normalized.append([first] if isinstance(first, str) else [])
        elif isinstance(entry, str):
            normalized.append([entry] if entry else [])
        else:
            normalized.append([])
    return normalized


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
    require_full: bool,
    require_snippets: bool,
    require_change_meta: bool,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not patch_entries:
        if require_full:
            errors.append("patch is empty but full output is required")
        else:
            warnings.append("patch is empty")
        return errors, warnings

    matches, identity_errors, identity_warnings = match_patch_entries(
        source_questions,
        patch_entries,
        require_full=require_full,
    )
    errors.extend(identity_errors)
    warnings.extend(identity_warnings)

    for match in matches:
        idx = match.patch_index + 1
        entry = match.patch
        source = match.source
        missing_fields = [k for k in REQUIRED_FIELDS if k not in entry]
        if missing_fields:
            errors.append(f"index {idx}: missing fields {missing_fields}")
            continue

        pid = entry.get("original_question_id")
        if not pid:
            errors.append(f"index {idx}: original_question_id is missing")
            continue

        if (
            "question_url" in entry
            and entry.get("question_url") != source.get("question_url")
        ):
            errors.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, source.get("question_url"), entry.get("question_url")
                )
            )

        if require_change_meta:
            changed_flag = entry.get("correctChoiceText_changed")
            detail = entry.get("correctChoiceText_change_detail")
            reason = entry.get("correctChoiceText_change_reason")

            if "correctChoiceText_changed" not in entry:
                errors.append(f"index {idx}: correctChoiceText_changed is missing")
            elif not isinstance(changed_flag, bool):
                errors.append(f"index {idx}: correctChoiceText_changed must be a bool")

            if "correctChoiceText_change_detail" not in entry:
                errors.append(f"index {idx}: correctChoiceText_change_detail is missing")
            elif not isinstance(detail, str):
                errors.append(
                    f"index {idx}: correctChoiceText_change_detail must be a string"
                )

            if "correctChoiceText_change_reason" not in entry:
                errors.append(f"index {idx}: correctChoiceText_change_reason is missing")
            elif not isinstance(reason, str):
                errors.append(
                    f"index {idx}: correctChoiceText_change_reason must be a string"
                )

        if require_snippets:
            if "explanation_choice_snippets" not in entry:
                errors.append(f"index {idx}: explanation_choice_snippets is missing")
            else:
                patch_snippets = entry.get("explanation_choice_snippets")
                source_snippets = source.get("explanation_choice_snippets")
                if not isinstance(patch_snippets, list):
                    errors.append(
                        f"index {idx}: explanation_choice_snippets must be a list"
                    )
                else:
                    normalized_source = normalize_snippet_list(source_snippets)
                    if normalized_source is None:
                        errors.append(
                            f"index {idx}: source explanation_choice_snippets is missing"
                        )
                    elif len(patch_snippets) != len(normalized_source):
                        errors.append(
                            "index {}: explanation_choice_snippets length mismatch "
                            "(source={} patch={})".format(
                                idx, len(normalized_source), len(patch_snippets)
                            )
                        )
                    elif patch_snippets != normalized_source:
                        errors.append(
                            f"index {idx}: explanation_choice_snippets mismatch"
                        )

        correct_choices = entry.get("correctChoiceText")
        if not isinstance(correct_choices, list):
            errors.append(f"index {idx}: correctChoiceText must be a list")
        else:
            if any(value not in {"正しい", "間違い"} for value in correct_choices):
                errors.append(
                    f"index {idx}: correctChoiceText must contain only 正しい/間違い"
                )
            source_choices = source.get("choiceTextList") or []
            if isinstance(source_choices, list) and len(correct_choices) != len(source_choices):
                errors.append(
                    "index {}: correctChoiceText length mismatch "
                    "(source={} patch={})".format(
                        idx, len(source_choices), len(correct_choices)
                    )
                )

            source_cct = source.get("correctChoiceText")
            if require_change_meta and isinstance(source_cct, list):
                changed_flag = entry.get("correctChoiceText_changed")
                detail = entry.get("correctChoiceText_change_detail")
                reason = entry.get("correctChoiceText_change_reason")
                if changed_flag is True and correct_choices == source_cct:
                    errors.append(
                        f"index {idx}: correctChoiceText_changed is true but no diff"
                    )
                if changed_flag is False and correct_choices != source_cct:
                    errors.append(
                        f"index {idx}: correctChoiceText_changed is false but differs"
                    )
                if changed_flag is True:
                    if isinstance(detail, str) and not detail.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_detail is empty"
                        )
                    if isinstance(reason, str) and not reason.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_reason is empty"
                        )
                if changed_flag is False:
                    if isinstance(detail, str) and detail.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_detail must be empty"
                        )
                    if isinstance(reason, str) and reason.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_reason must be empty"
                        )
            if (
                not require_full
                and isinstance(source_cct, list)
                and correct_choices == source_cct
            ):
                errors.append(f"index {idx}: correctChoiceText is unchanged from source")

    return errors, warnings


def check_pair(
    source_path: Path,
    patch_path: Path,
    require_full: bool,
    require_snippets: bool,
    require_change_meta: bool,
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
        question_type_matches, identity_errors, _ = match_patch_entries(
            source_questions,
            question_type_entries,
        )
        if identity_errors:
            for error in identity_errors:
                print(f"[ERROR] questionType patch: {error}")
            return 1
        question_type_map = {
            str(review_question_id(match.source)): match.patch
            for match in question_type_matches
            if review_question_id(match.source)
        }
        apply_question_type(source_data, question_type_map)
    patch_data = load_json(patch_path)

    source_questions = bind_source_records(
        get_source_questions(source_data),
        source_path,
    )
    patch_entries = get_patch_entries(patch_data)

    errors, warnings = compare_entries(
        source_questions,
        patch_entries,
        require_full,
        require_snippets,
        require_change_meta,
    )
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
        description="Validate correctChoiceText patch coverage and format."
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to *_correctChoiceText_fixed_YYYYMMDD_HHMM.json (旧形式 *_correctChoiceText_fixed.json も可)",
    )
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="Require patch to include all questions in source order.",
    )
    parser.add_argument(
        "--require-snippets",
        action="store_true",
        help="Require explanation_choice_snippets to match the source.",
    )
    parser.add_argument(
        "--require-change-meta",
        action="store_true",
        help="Require correctChoiceText change metadata to be present and consistent.",
    )
    parser.add_argument(
        "--question-type-patch",
        help="Apply the corresponding 10_questionType_fixed patch before validating choice counts.",
    )
    args = parser.parse_args()
    return check_pair(
        Path(args.source),
        Path(args.patch),
        args.require_full,
        args.require_snippets,
        args.require_change_meta,
        Path(args.question_type_patch) if args.question_type_patch else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
