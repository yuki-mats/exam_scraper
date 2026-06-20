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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_source_paths(qualifications: list[str]) -> list[Path]:
    paths: list[Path] = []
    for qualification in qualifications:
        root = ROOT_DIR / "output" / qualification / "questions_json"
        paths.extend(
            path
            for path in sorted(root.glob("*/00_source/question_*.json"))
            if "99_archived" not in path.parts
        )
    return paths


def firestore_id_index(qualifications: list[str]) -> dict[str, set[str]]:
    by_original: dict[str, set[str]] = defaultdict(set)
    for path in iter_source_paths(qualifications):
        payload = load_json(path)
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        for question in bodies:
            if not isinstance(question, dict):
                continue
            original_id = str(question.get("originalQuestionId") or question.get("original_question_id") or "").strip()
            ids = question.get("firestoreQuestionIds")
            if not original_id or not isinstance(ids, list):
                continue
            for question_id in ids:
                text = str(question_id or "").strip()
                if text:
                    by_original[original_id].add(text)
    return by_original


def upload_questions(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [item for item in payload["questions"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError(f"questions array not found: {path}")


def check_plan(
    plan_path: Path,
    allow_source_conflicts: bool,
    max_samples: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], Counter[str]]:
    rows = load_jsonl(plan_path)
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    status_counts = Counter(str(row.get("sourceConflictStatus") or "missing") for row in rows)
    conflict_rows = [row for row in rows if row.get("sourceConflictStatus") == "needs_source_review"]
    if conflict_rows and not allow_source_conflicts:
        issue_counts["unresolved_source_conflict"] = len(conflict_rows)
        for row in conflict_rows[:max_samples]:
            issues.append(
                {
                    "code": "unresolved_source_conflict",
                    "planSequence": row.get("planSequence"),
                    "qualification": row.get("qualification"),
                    "sourceQuestionKey": row.get("sourceQuestionKey"),
                    "reviewQuestionId": row.get("reviewQuestionId"),
                    "sourceContentConflictCount": row.get("sourceContentConflictCount"),
                    "ledger": row.get("sourceContentConflictLedgerPath"),
                }
            )
    return issues, {
        "planRowCount": len(rows),
        "sourceConflictStatusCounts": dict(sorted(status_counts.items())),
        "needsSourceReviewRowCount": len(conflict_rows),
    }, issue_counts


def check_upload_json(
    upload_paths: list[Path],
    by_original_id: dict[str, set[str]],
    max_samples: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], Counter[str]]:
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    checked = 0
    existing_origin_count = 0
    for path in upload_paths:
        for question in upload_questions(path):
            checked += 1
            original_id = str(question.get("originalQuestionId") or "").strip()
            question_id = str(question.get("questionId") or "").strip()
            if original_id in by_original_id:
                existing_origin_count += 1
                allowed = by_original_id[original_id]
                if question_id not in allowed:
                    issue_counts["existing_firestore_question_id_would_change"] += 1
                    if len(issues) < max_samples:
                        issues.append(
                            {
                                "code": "existing_firestore_question_id_would_change",
                                "uploadFile": rel(path),
                                "originalQuestionId": original_id,
                                "questionId": question_id,
                                "allowedFirestoreQuestionIds": sorted(allowed),
                            }
                        )
    return issues, {
        "uploadFileCount": len(upload_paths),
        "uploadQuestionCount": checked,
        "existingFirestoreOriginalQuestionCount": existing_origin_count,
    }, issue_counts


def run(args: argparse.Namespace) -> dict[str, Any]:
    issue_samples: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    plan_issues, plan_summary, plan_issue_counts = check_plan(args.plan, args.allow_source_conflicts, args.max_samples)
    issue_samples.extend(plan_issues)
    issue_counts.update(plan_issue_counts)

    upload_summary = {
        "uploadFileCount": 0,
        "uploadQuestionCount": 0,
        "existingFirestoreOriginalQuestionCount": 0,
    }
    if args.upload_json:
        by_original = firestore_id_index(args.qualifications)
        upload_issues, upload_summary, upload_issue_counts = check_upload_json(
            args.upload_json,
            by_original,
            args.max_samples,
        )
        issue_samples.extend(upload_issues)
        issue_counts.update(upload_issue_counts)

    total_issue_count = sum(issue_counts.values())

    return {
        "schemaVersion": "gas-shunin-upload-gate/v1",
        "generatedAt": utc_now(),
        "ok": total_issue_count == 0,
        "allowSourceConflicts": bool(args.allow_source_conflicts),
        "plan": plan_summary,
        "upload": upload_summary,
        "issueCount": total_issue_count,
        "issueCounts": dict(sorted(issue_counts.items())),
        "sampleIssueCount": min(len(issue_samples), args.max_samples),
        "issues": issue_samples[: args.max_samples],
        "policy": {
            "sourceConflictStatus": "needs_source_review fails unless --allow-source-conflicts is set",
            "questionId": "existing Firestore-derived originalQuestionId must upload to one of its existing firestoreQuestionIds",
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gas-shunin upload safety gate.")
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT_DIR / "docs" / "goals" / "gas-shunin-01-04-full-pass" / "notes" / "question-plan" / "all_questions_plan.jsonl",
    )
    parser.add_argument("--upload-json", type=Path, nargs="*", default=[])
    parser.add_argument(
        "--qualifications",
        nargs="+",
        default=["gas-shunin-kou", "gas-shunin-otsu"],
        choices=["gas-shunin-kou", "gas-shunin-otsu"],
    )
    parser.add_argument("--allow-source-conflicts", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT_DIR / "output" / "gas-shunin-upload-gate-report.json")
    parser.add_argument("--max-samples", type=int, default=100)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(args)
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
