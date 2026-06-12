#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA_VERSION = "tsukanshi-01-04-manual-review/v1"
VALID_STEP_DECISIONS = {"pending", "ok", "needs_fix", "hold"}
VALID_REVIEW_DECISIONS = {"pending", "ok", "needs_fix", "hold"}
STEP_FIELDS = [
    "review01QuestionType",
    "review02QuestionIntent",
    "review02CorrectChoiceText",
    "review03ExplanationText",
    "review04QuestionSetId",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            row["_lineNumber"] = line_number
            rows.append(row)
    return rows


def validate_row(row: dict[str, Any], *, allow_pending: bool) -> list[str]:
    errors: list[str] = []
    prefix = f"line {row.get('_lineNumber')} reviewId={row.get('reviewId')}:"

    if row.get("schemaVersion") != EXPECTED_SCHEMA_VERSION:
        errors.append(f"{prefix} schemaVersion must be {EXPECTED_SCHEMA_VERSION}")

    required_strings = [
        "reviewId",
        "workflow",
        "qualification",
        "prompt01Path",
        "prompt02Path",
        "prompt03Path",
        "prompt04Path",
        "listGroupId",
        "examYear",
        "examLabel",
        "originalQuestionId",
        "questionUrl",
        "sourceFile",
        "questionTypePatchFile",
        "correctChoicePatchFile",
        "explanationPatchFile",
        "questionSetPatchFile",
        "questionBodyText",
        "questionType",
        "questionIntent",
        "questionSetId",
        "expectedQuestionSetId",
    ]
    for key in required_strings:
        if not isinstance(row.get(key), str) or not row.get(key).strip():
            errors.append(f"{prefix} {key} must be a non-empty string")

    required_lists = [
        "choiceTextList",
        "correctChoiceText",
        "explanationText",
        "suggestedQuestions",
        "suggestedQuestionDetails",
        "expectedCorrectChoiceText",
        "requiredManualChecks",
    ]
    for key in required_lists:
        if not isinstance(row.get(key), list):
            errors.append(f"{prefix} {key} must be a list")

    auto_audit = row.get("autoAudit")
    if not isinstance(auto_audit, dict):
        errors.append(f"{prefix} autoAudit must be an object")

    for key in STEP_FIELDS:
        value = row.get(key)
        if value not in VALID_STEP_DECISIONS:
            errors.append(f"{prefix} {key} must be one of {sorted(VALID_STEP_DECISIONS)}")

    decision = row.get("reviewDecision")
    if decision == "pending" and allow_pending:
        return errors
    if decision not in VALID_REVIEW_DECISIONS:
        errors.append(f"{prefix} reviewDecision must be one of {sorted(VALID_REVIEW_DECISIONS)}")
        return errors

    if decision != "pending":
        if not str(row.get("reviewer") or "").strip():
            errors.append(f"{prefix} reviewer is required when reviewDecision is not pending")
        if not str(row.get("reviewedAt") or "").strip():
            errors.append(f"{prefix} reviewedAt is required when reviewDecision is not pending")

    if decision == "ok":
        for key in STEP_FIELDS:
            if row.get(key) != "ok":
                errors.append(f"{prefix} {key} must be ok when reviewDecision=ok")
        if str(row.get("reviewNotes") or "").strip():
            errors.append(f"{prefix} reviewNotes must be empty when reviewDecision=ok")
        if str(row.get("fixInstructions") or "").strip():
            errors.append(f"{prefix} fixInstructions must be empty when reviewDecision=ok")

    if decision in {"needs_fix", "hold"} and not str(row.get("reviewNotes") or "").strip():
        errors.append(f"{prefix} reviewNotes is required for {decision}")
    if decision == "needs_fix" and not str(row.get("fixInstructions") or "").strip():
        errors.append(f"{prefix} fixInstructions is required for needs_fix")

    return errors


def validate(rows: list[dict[str, Any]], *, allow_pending: bool) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    review_ids = [str(row.get("reviewId") or "") for row in rows]
    duplicated = sorted(review_id for review_id, count in Counter(review_ids).items() if count > 1)
    if duplicated:
        errors.append(f"duplicated reviewId: {duplicated[:20]}")

    review_decisions = Counter(str(row.get("reviewDecision") or "") for row in rows)
    step_counts = {
        field: dict(Counter(str(row.get(field) or "") for row in rows))
        for field in STEP_FIELDS
    }

    for row in rows:
        errors.extend(validate_row(row, allow_pending=allow_pending))

    summary = {
        "rowCount": len(rows),
        "reviewDecisionCounts": dict(review_decisions),
        "stepDecisionCounts": step_counts,
        "duplicatedReviewIds": duplicated,
        "errorCount": len(errors),
    }
    return summary, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("review_jsonl", type=Path)
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="freshly exported pending sheet を検証する",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_jsonl(args.review_jsonl)
    summary, errors = validate(rows, allow_pending=args.allow_pending)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
