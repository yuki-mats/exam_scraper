#!/usr/bin/env python3
"""
Validate questionIntent patch coverage and metadata.

The historical directory name is 15_correctChoiceText_fixed, but the current
prompt contract uses it as a questionIntent patch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id  # noqa: E402

VALID_QUESTION_INTENTS = {"select_correct", "select_incorrect"}
REQUIRED_FIELDS = [
    "original_question_id",
    "questionIntent",
    "questionIntent_changed",
    "questionIntent_change_detail",
    "questionIntent_change_reason",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def get_source_questions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("source JSON must be an object")
    questions = payload.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    return [q for q in questions if isinstance(q, dict)]


def get_patch_entries(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("patch JSON must be an array")
    return [q for q in payload if isinstance(q, dict)]


def compare_entries(
    source_questions: list[dict[str, Any]],
    patch_entries: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    source_ids = [review_question_id(question) for question in source_questions]
    patch_ids: list[str] = []

    if len(source_questions) != len(patch_entries):
        errors.append(
            f"patch entry count mismatch (source={len(source_questions)} patch={len(patch_entries)})"
        )

    for idx, entry in enumerate(patch_entries, start=1):
        missing = [key for key in REQUIRED_FIELDS if key not in entry]
        if missing:
            errors.append(f"index {idx}: missing fields {missing}")
            continue

        original_question_id = str(entry.get("original_question_id") or "").strip()
        if not original_question_id:
            errors.append(f"index {idx}: original_question_id is empty")
            continue
        patch_ids.append(original_question_id)

        if idx <= len(source_ids) and original_question_id != source_ids[idx - 1]:
            errors.append(
                "index {}: original_question_id mismatch (source={} patch={})".format(
                    idx,
                    source_ids[idx - 1],
                    original_question_id,
                )
            )

        intent = entry.get("questionIntent")
        if intent not in VALID_QUESTION_INTENTS:
            errors.append(f"index {idx}: questionIntent is invalid: {intent!r}")

        changed = entry.get("questionIntent_changed")
        detail = entry.get("questionIntent_change_detail")
        reason = entry.get("questionIntent_change_reason")

        if not isinstance(changed, bool):
            errors.append(f"index {idx}: questionIntent_changed must be bool")
            continue
        if not isinstance(detail, str):
            errors.append(f"index {idx}: questionIntent_change_detail must be string")
            continue
        if not isinstance(reason, str):
            errors.append(f"index {idx}: questionIntent_change_reason must be string")
            continue

        if changed:
            if not detail.strip():
                errors.append(f"index {idx}: questionIntent_change_detail is empty")
            if not reason.strip():
                errors.append(f"index {idx}: questionIntent_change_reason is empty")
        else:
            if detail.strip():
                errors.append(f"index {idx}: questionIntent_change_detail must be empty when unchanged")
            if reason.strip():
                errors.append(f"index {idx}: questionIntent_change_reason must be empty when unchanged")

    source_id_set = {source_id for source_id in source_ids if source_id}
    patch_id_set = {patch_id for patch_id in patch_ids if patch_id}
    missing_ids = sorted(source_id_set - patch_id_set)
    extra_ids = sorted(patch_id_set - source_id_set)
    duplicate_ids = sorted(
        patch_id for patch_id in patch_id_set if patch_ids.count(patch_id) > 1
    )
    if missing_ids:
        errors.append(f"missing original_question_id: {missing_ids}")
    if extra_ids:
        errors.append(f"extra original_question_id: {extra_ids}")
    if duplicate_ids:
        errors.append(f"duplicate original_question_id: {duplicate_ids}")

    return errors


def check_pair(source_path: Path, patch_path: Path) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_questions = get_source_questions(load_json(source_path))
    patch_entries = get_patch_entries(load_json(patch_path))
    errors = compare_entries(source_questions, patch_entries)
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print("[OK] coverage check passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate questionIntent patch coverage and metadata."
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to *_correctChoiceText_fixed.json. Directory name is historical.",
    )
    args = parser.parse_args(argv)
    return check_pair(Path(args.source), Path(args.patch))


if __name__ == "__main__":
    raise SystemExit(main())
