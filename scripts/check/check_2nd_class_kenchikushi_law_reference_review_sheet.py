#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA_VERSION = "2nd-class-kenchikushi-law-reference-review/v1"
VALID_DECISIONS = {"ok", "needs_fix", "hold"}


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
    line = row.get("_lineNumber")
    prefix = f"line {line} reviewId={row.get('reviewId')}:"

    if row.get("schemaVersion") != EXPECTED_SCHEMA_VERSION:
        errors.append(f"{prefix} schemaVersion must be {EXPECTED_SCHEMA_VERSION}")

    required_strings = [
        "reviewId",
        "workflow",
        "promptSourcePath",
        "qualificationPolicyPath",
        "qualificationScopePath",
        "qualification",
        "listGroupId",
        "originalQuestionId",
        "sourceFile",
        "patchFile",
        "questionBodyText",
    ]
    for key in required_strings:
        if not isinstance(row.get(key), str) or not row.get(key).strip():
            errors.append(f"{prefix} {key} must be a non-empty string")

    decision = row.get("reviewDecision")
    if decision == "pending" and allow_pending:
        return errors
    if decision not in VALID_DECISIONS:
        errors.append(f"{prefix} reviewDecision must be one of {sorted(VALID_DECISIONS)}")

    if decision in {"needs_fix", "hold"} and not str(row.get("reviewNotes") or "").strip():
        errors.append(f"{prefix} reviewNotes is required for {decision}")
    if decision == "needs_fix" and not str(row.get("fixInstructions") or "").strip():
        errors.append(f"{prefix} fixInstructions is required for needs_fix")

    refs = row.get("lawReferenceSummary")
    if decision == "ok":
        if not isinstance(refs, list) or not refs:
            errors.append(f"{prefix} lawReferenceSummary must be a non-empty list")
        else:
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    errors.append(f"{prefix} lawReferenceSummary[{ref_index}] must be an object")
                    continue
                for key in ("lawTitle", "lawId", "article", "verificationStatus"):
                    if not isinstance(ref.get(key), str) or not ref.get(key).strip():
                        errors.append(f"{prefix} lawReferenceSummary[{ref_index}].{key} must be non-empty")
                if ref.get("verificationStatus") != "verified":
                    errors.append(f"{prefix} lawReferenceSummary[{ref_index}].verificationStatus must be verified")
    else:
        if isinstance(refs, list):
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    errors.append(f"{prefix} lawReferenceSummary[{ref_index}] must be an object")
                    continue
                for key in ("lawTitle", "lawId", "article", "verificationStatus"):
                    if not isinstance(ref.get(key), str) or not ref.get(key).strip():
                        errors.append(f"{prefix} lawReferenceSummary[{ref_index}].{key} must be non-empty")
                if ref.get("verificationStatus") != "verified":
                    errors.append(f"{prefix} lawReferenceSummary[{ref_index}].verificationStatus must be verified")

    return errors


def validate(rows: list[dict[str, Any]], *, allow_pending: bool) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    decisions = Counter(str(row.get("reviewDecision") or "") for row in rows)
    review_ids = [str(row.get("reviewId") or "") for row in rows]
    duplicated = sorted(review_id for review_id, count in Counter(review_ids).items() if count > 1)
    if duplicated:
        errors.append(f"duplicated reviewId: {duplicated[:20]}")
    for row in rows:
        errors.extend(validate_row(row, allow_pending=allow_pending))
    summary = {
        "rowCount": len(rows),
        "decisionCounts": dict(decisions),
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
        help="validate a freshly exported sheet before manual review is complete",
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
