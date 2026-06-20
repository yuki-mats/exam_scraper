#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.question_identity import review_question_id  # noqa: E402


DEFAULT_PLAN_ROOT = ROOT_DIR / "docs" / "goals" / "gas-shunin-01-04-full-pass" / "notes" / "question-plan"
TSV_FIELDS = [
    "planSequence",
    "executionPhase",
    "qualification",
    "examYear",
    "sourceCategory",
    "questionLabel",
    "questionType",
    "questionIntent",
    "choiceCount",
    "correctChoiceTextCount",
    "explanationTextCount",
    "sourceOrigin",
    "isSiteSourced",
    "firestoreQuestionIdCount",
    "sourceQuestionKey",
    "sourceConflictStatus",
    "sourceContentConflictCount",
    "sourceContentConflictFields",
    "sourceUniqueKeys",
    "reviewQuestionId",
    "qualifiedReviewQuestionId",
    "sourceFile",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def tsv_value(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\n", " ")


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = ["\t".join(TSV_FIELDS)]
    for row in rows:
        lines.append("\t".join(tsv_value(row.get(field)) for field in TSV_FIELDS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def canonical_question_key(value: Any) -> str:
    text = str(value or "")
    return text.replace(":hourei:", ":law:")


def source_file_question_map(qualifications: list[str]) -> dict[tuple[str, str], dict[str, Any]]:
    mapping: dict[tuple[str, str], dict[str, Any]] = {}
    for qualification in qualifications:
        root = ROOT_DIR / "output" / qualification / "questions_json"
        for path in sorted(root.glob("*/00_source/question_*.json")):
            if "99_archived" in path.parts:
                continue
            payload = load_json(path)
            bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
            if not isinstance(bodies, list):
                continue
            for question in bodies:
                if isinstance(question, dict):
                    mapping[(rel(path), review_question_id(question))] = question
    return mapping


def conflict_groups(ledger_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        key = canonical_question_key(row.get("canonicalQuestionKey"))
        group = grouped.setdefault(
            key,
            {
                "count": 0,
                "fields": Counter(),
                "severities": Counter(),
                "statementKeys": set(),
                "conflictIds": [],
            },
        )
        group["count"] += 1
        group["fields"][str(row.get("field"))] += 1
        group["severities"][str(row.get("severity"))] += 1
        group["statementKeys"].add(str(row.get("canonicalStatementKey")))
        group["conflictIds"].append(str(row.get("conflictId")))
    return grouped


def update_row_from_source(row: dict[str, Any], source_question: dict[str, Any] | None) -> None:
    if source_question is None:
        return
    for field in (
        "sourceQuestionKey",
        "sourceUniqueKeys",
        "sourceNaturalQuestionKey",
        "sourceNaturalUniqueKeys",
        "sourceKeyConflict",
        "statementSourceStatuses",
        "sourceOrigin",
        "sourceProvider",
        "sourceAcquisitionMethod",
        "sourcePriority",
        "sourceStatementCount",
        "sourceUrl",
        "sourceKeyParts",
        "isSiteSourced",
    ):
        if field in source_question:
            row[field] = source_question.get(field)
    firestore_ids = source_question.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        row["firestoreQuestionIds"] = firestore_ids
        row["firestoreQuestionIdCount"] = len(firestore_ids)


def apply_conflicts(
    rows: list[dict[str, Any]],
    *,
    ledger_rows: list[dict[str, Any]],
    ledger_path: Path,
    source_map: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups = conflict_groups(ledger_rows)
    status_counts: Counter[str] = Counter()
    conflict_question_keys: set[str] = set()
    changed_rows = 0
    for row in rows:
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        source_question = source_map.get((str(row.get("sourceFile")), str(row.get("reviewQuestionId"))))
        update_row_from_source(row, source_question)
        row_key = canonical_question_key(row.get("sourceNaturalQuestionKey") or row.get("sourceQuestionKey"))
        group = groups.get(row_key)
        if group:
            row["sourceConflictStatus"] = "needs_source_review"
            row["sourceContentConflictCount"] = group["count"]
            row["sourceContentConflictFields"] = sorted(group["fields"])
            row["sourceContentConflictStatementKeys"] = sorted(group["statementKeys"])
            row["sourceContentConflictLedgerPath"] = rel(ledger_path)
            row["sourceContentConflictPolicy"] = "preserve_firestore_until_pdf_or_source_review"
            conflict_question_keys.add(row_key)
            stop_if = list(row.get("stopIf") or [])
            if "source_content_conflict_unresolved" not in stop_if:
                stop_if.append("source_content_conflict_unresolved")
            row["stopIf"] = stop_if
            fix = str(row.get("fixInstructions") or "")
            note = "source_conflict_ledgerを確認し、Firestore/site/PDFの根拠確認後に進める"
            if note not in fix:
                row["fixInstructions"] = (fix + " / " + note).strip(" /")
        elif row.get("sourceKeyConflict"):
            row["sourceConflictStatus"] = "metadata_resolved"
            row["sourceContentConflictCount"] = 0
            row["sourceContentConflictFields"] = []
        else:
            row["sourceConflictStatus"] = "none"
            row["sourceContentConflictCount"] = 0
            row["sourceContentConflictFields"] = []

        status_counts[str(row.get("sourceConflictStatus"))] += 1
        if json.dumps(row, ensure_ascii=False, sort_keys=True) != before:
            changed_rows += 1

    summary = {
        "changedPlanRows": changed_rows,
        "sourceContentConflictQuestionCount": len(conflict_question_keys),
        "sourceContentConflictCount": len(ledger_rows),
        "sourceConflictStatusCounts": dict(sorted(status_counts.items())),
        "sourceContentConflictLedgerPath": rel(ledger_path),
    }
    return rows, summary


def update_summary(summary_path: Path, rows: list[dict[str, Any]], conflict_summary: dict[str, Any]) -> dict[str, Any]:
    summary = load_json(summary_path) if summary_path.exists() else {}
    summary["generatedAt"] = utc_now()
    summary["sourceConflictStatusCounts"] = conflict_summary["sourceConflictStatusCounts"]
    summary["sourceContentConflictQuestionCount"] = conflict_summary["sourceContentConflictQuestionCount"]
    summary["sourceContentConflictCount"] = conflict_summary["sourceContentConflictCount"]
    summary["sourceContentConflictLedgerPath"] = conflict_summary["sourceContentConflictLedgerPath"]
    summary["sourceKeyConflictQuestionCount"] = sum(1 for row in rows if row.get("sourceKeyConflict"))
    summary["sourceConflictPolicy"] = {
        "needs_source_review": "01-04 work must verify source conflict before changing upload-facing fields",
        "questionId": "existing Firestore document IDs are preserved",
    }
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply gas-shunin source conflict flags to all-question plan.")
    parser.add_argument("--plan-root", type=Path, default=DEFAULT_PLAN_ROOT)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT_DIR / "output" / "gas-shunin-kou" / "review" / "source_conflicts" / "firestore_site_conflicts.jsonl",
    )
    parser.add_argument(
        "--qualifications",
        nargs="+",
        default=["gas-shunin-kou", "gas-shunin-otsu"],
        choices=["gas-shunin-kou", "gas-shunin-otsu"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    jsonl_path = args.plan_root / "all_questions_plan.jsonl"
    tsv_path = args.plan_root / "all_questions_plan.tsv"
    summary_path = args.plan_root / "summary.json"
    rows = load_jsonl(jsonl_path)
    ledger_rows = load_jsonl(args.ledger)
    source_map = source_file_question_map(args.qualifications)
    updated_rows, conflict_summary = apply_conflicts(
        rows,
        ledger_rows=ledger_rows,
        ledger_path=args.ledger,
        source_map=source_map,
    )
    updated_rows = sorted(updated_rows, key=lambda row: int(row.get("planSequence") or 0))
    write_jsonl(jsonl_path, updated_rows)
    write_tsv(tsv_path, updated_rows)
    summary = update_summary(summary_path, updated_rows, conflict_summary)
    write_json(summary_path, summary)
    print(json.dumps(conflict_summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
