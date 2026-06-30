#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.convert.convert_merged_to_firestore import question_id_from_source_unique_key  # noqa: E402


DEFAULT_PLAN = (
    ROOT_DIR
    / "docs"
    / "goals"
    / "gas-shunin-01-04-full-pass"
    / "notes"
    / "question-plan"
    / "all_questions_plan.jsonl"
)
CONTENT_BLOCKING_STATUSES = {"hold", "pending"}


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
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def planned_question_ids(row: dict[str, Any]) -> set[str]:
    firestore_ids = row.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        ids = {str(value).strip() for value in firestore_ids if str(value or "").strip()}
        if ids:
            return ids

    source_unique_keys = row.get("sourceUniqueKeys")
    if isinstance(source_unique_keys, list):
        return {
            question_id_from_source_unique_key(str(value).strip())
            for value in source_unique_keys
            if str(value or "").strip()
        }
    return set()


def content_ready(row: dict[str, Any]) -> bool:
    for field in ("review02CorrectChoiceText", "review03ExplanationText"):
        if str(row.get(field) or "") in CONTENT_BLOCKING_STATUSES:
            return False
    return True


def question_set_ready(row: dict[str, Any]) -> bool:
    review04 = str(row.get("review04QuestionSetId") or "")
    if review04 == "ok":
        return True
    return review04 == "hold" and row.get("sourceOrigin") == "firestore_snapshot"


def source_ready(row: dict[str, Any]) -> bool:
    status = str(row.get("sourceConflictStatus") or "")
    if status in {"none", "metadata_resolved"}:
        return True
    if status == "source_choice_count_mismatch":
        return False
    if status != "needs_source_review":
        return False
    if not content_ready(row) or not question_set_ready(row):
        return False

    policy = str(row.get("sourceContentConflictPolicy") or "")
    if row.get("sourceOrigin") == "firestore_snapshot" and "preserve_firestore" in policy:
        return True

    source_key_conflict = row.get("sourceKeyConflict")
    source_unique_keys = row.get("sourceUniqueKeys")
    return (
        row.get("sourceOrigin") == "gassyunin_site"
        and isinstance(source_key_conflict, dict)
        and source_key_conflict.get("reason") == "site_record_overlaps_existing_firestore_statements"
        and isinstance(source_unique_keys, list)
        and bool(source_unique_keys)
        and all(":site-shadow:" in str(value) for value in source_unique_keys)
    )


def upload_decision(row: dict[str, Any]) -> tuple[str, str]:
    if not content_ready(row):
        return "holdout", "content_review_not_ready"
    if not question_set_ready(row):
        if row.get("sourceOrigin") == "gassyunin_site" and row.get("review04QuestionSetId") == "hold":
            return "holdout", "site_statement_level_question_set_unassigned"
        return "holdout", "question_set_not_ready"
    if not source_ready(row):
        return "holdout", f"source_not_ready:{row.get('sourceConflictStatus') or 'missing'}"

    if row.get("review04QuestionSetId") == "hold" and row.get("sourceOrigin") == "firestore_snapshot":
        return "upload", "preserve_existing_statement_question_sets"
    if row.get("sourceConflictStatus") == "needs_source_review" and row.get("sourceOrigin") == "firestore_snapshot":
        return "upload", "preserve_firestore_source_conflict"
    if row.get("sourceConflictStatus") == "needs_source_review" and row.get("sourceOrigin") == "gassyunin_site":
        return "upload", "site_shadow_new_documents"
    return "upload", "review_ok"


def upload_json_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(path)
    latest_by_group: dict[str, Path] = {}
    for candidate in sorted(path.glob("*_firestore_*.json")):
        match = re.match(r"^(?P<group>.+)_firestore_", candidate.name)
        group = match.group("group") if match else candidate.stem
        latest_by_group[group] = candidate
    return [latest_by_group[group] for group in sorted(latest_by_group)]


def archive_existing_output_files(output_dir: Path, upload_filename: str) -> Path | None:
    match = re.match(r"^(?P<group>.+)_firestore_", upload_filename)
    group = match.group("group") if match else Path(upload_filename).stem
    existing = sorted(output_dir.glob(f"{group}_firestore_*.json"))
    if not existing:
        return None

    archive_root = output_dir / "old"
    archive_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = archive_root / archive_timestamp
    suffix = 1
    while archive_dir.exists():
        archive_dir = archive_root / f"{archive_timestamp}_{suffix:02d}"
        suffix += 1
    archive_dir.mkdir(parents=True, exist_ok=False)
    for path in existing:
        shutil.move(str(path), str(archive_dir / path.name))
    return archive_dir


def build_id_decisions(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    decisions_by_id: dict[str, dict[str, Any]] = {}
    row_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    question_id_counts: Counter[str] = Counter()
    for row in rows:
        decision, reason = upload_decision(row)
        row_counts[decision] += 1
        reason_counts[f"{decision}:{reason}"] += 1
        for question_id in planned_question_ids(row):
            question_id_counts[decision] += 1
            decisions_by_id[question_id] = {
                "decision": decision,
                "reason": reason,
                "planSequence": row.get("planSequence"),
                "qualification": row.get("qualification"),
                "sourceQuestionKey": row.get("sourceQuestionKey"),
                "reviewQuestionId": row.get("reviewQuestionId"),
            }
    summary = {
        "planRowDecisionCounts": dict(sorted(row_counts.items())),
        "planReasonCounts": dict(sorted(reason_counts.items())),
        "plannedQuestionIdDecisionCounts": dict(sorted(question_id_counts.items())),
    }
    return decisions_by_id, summary


def filter_upload_file(
    upload_path: Path,
    *,
    decisions_by_id: dict[str, dict[str, Any]],
    output_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    payload = load_json(upload_path)
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, list):
        raise ValueError(f"questions array not found: {upload_path}")

    kept_questions: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    missing_plan_ids: list[str] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("questionId") or "").strip()
        decision = decisions_by_id.get(question_id)
        if decision is None:
            missing_plan_ids.append(question_id)
            excluded.append(
                {
                    "questionId": question_id,
                    "reason": "missing_plan_decision",
                }
            )
            continue
        if decision["decision"] == "upload":
            if not str(question.get("questionSetId") or "").strip():
                excluded.append(
                    {
                        "questionId": question_id,
                        "reason": "missing_question_set_id",
                        "planSequence": decision.get("planSequence"),
                        "sourceQuestionKey": decision.get("sourceQuestionKey"),
                    }
                )
                continue
            kept_questions.append(question)
        else:
            excluded.append(
                {
                    "questionId": question_id,
                    "reason": decision["reason"],
                    "planSequence": decision.get("planSequence"),
                    "sourceQuestionKey": decision.get("sourceQuestionKey"),
                }
            )

    output_payload = dict(payload)
    output_payload["questions"] = kept_questions
    output_payload["total_count"] = len(kept_questions)
    output_path = output_dir / upload_path.name
    archive_existing_output_files(output_dir, upload_path.name)
    write_json(output_path, output_payload)

    summary = {
        "source": rel(upload_path),
        "output": rel(output_path),
        "inputQuestionCount": len([q for q in questions if isinstance(q, dict)]),
        "keptQuestionCount": len(kept_questions),
        "excludedQuestionCount": len(excluded),
        "missingPlanDecisionCount": len(missing_plan_ids),
        "excludedSamples": excluded[:50],
    }
    return output_path, summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    rows = load_jsonl(args.plan)
    decisions_by_id, decision_summary = build_id_decisions(rows)
    upload_paths: list[Path] = []
    for path in args.upload_json:
        upload_paths.extend(upload_json_paths(path))
    if not upload_paths:
        raise FileNotFoundError("upload JSON files not found")

    file_summaries: list[dict[str, Any]] = []
    output_paths: list[str] = []
    for upload_path in upload_paths:
        output_path, file_summary = filter_upload_file(
            upload_path,
            decisions_by_id=decisions_by_id,
            output_dir=args.output_dir,
        )
        output_paths.append(rel(output_path))
        file_summaries.append(file_summary)

    report = {
        "schemaVersion": "gas-shunin-upload-ready-subset/v1",
        "generatedAt": utc_now(),
        "plan": rel(args.plan),
        "outputDir": rel(args.output_dir),
        "outputFiles": output_paths,
        "decisionSummary": decision_summary,
        "fileSummaries": file_summaries,
    }
    write_json(args.report, report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build gas-shunin upload-ready subsets from full upload JSON.")
    parser.add_argument(
        "--plan",
        type=Path,
        default=DEFAULT_PLAN,
        help="gas-shunin all_questions_plan.jsonl",
    )
    parser.add_argument("--upload-json", type=Path, nargs="+", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory for filtered upload JSON",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT_DIR / "output" / "gas-shunin-upload-ready-subset-report.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
