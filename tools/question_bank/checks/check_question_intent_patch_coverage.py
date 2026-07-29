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

from scripts.check.patch_entry_matching import (  # noqa: E402
    bind_source_records,
    match_patch_entries,
)
from scripts.common.question_answer_contract import (  # noqa: E402
    explicit_statement_question_intent,
)

VALID_QUESTION_INTENTS = {"select_correct", "select_incorrect"}
REQUIRED_FIELDS = [
    "original_question_id",
    "questionIntent",
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
    matches, identity_errors, _ = match_patch_entries(
        source_questions,
        patch_entries,
    )
    errors.extend(identity_errors)

    for match in matches:
        idx = match.patch_index + 1
        entry = match.patch
        missing = [key for key in REQUIRED_FIELDS if key not in entry]
        if missing:
            errors.append(f"index {idx}: missing fields {missing}")
            continue

        original_question_id = str(entry.get("original_question_id") or "").strip()
        if not original_question_id:
            errors.append(f"index {idx}: original_question_id is empty")
            continue

        intent = entry.get("questionIntent")
        if intent not in VALID_QUESTION_INTENTS:
            errors.append(f"index {idx}: questionIntent is invalid: {intent!r}")
            continue
        source_body = match.source.get("questionBodyText")
        if not isinstance(source_body, str) or not source_body.strip():
            source_body = match.source.get("originalQuestionBodyText")
        explicit_intent = explicit_statement_question_intent(source_body)
        if explicit_intent is not None and intent != explicit_intent:
            errors.append(
                f"index {idx}: questionIntent conflicts with the explicit "
                f"statement request: expected {explicit_intent!r}, "
                f"got {intent!r}"
            )

    return errors


def check_pair(source_path: Path, patch_path: Path) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_questions = bind_source_records(
        get_source_questions(load_json(source_path)),
        source_path,
    )
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
