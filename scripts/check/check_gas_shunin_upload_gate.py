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

from scripts.convert.convert_merged_to_firestore import question_id_from_source_unique_key  # noqa: E402


CONTENT_REVIEW_BLOCKING_STATUSES = {"hold", "pending"}


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


def source_id_indexes(
    qualifications: list[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str], dict[str, str]]:
    by_original: dict[str, set[str]] = defaultdict(set)
    new_by_original: dict[str, set[str]] = defaultdict(set)
    all_existing_ids: set[str] = set()
    existing_question_set_by_id: dict[str, str] = {}
    for path in iter_source_paths(qualifications):
        payload = load_json(path)
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        for question in bodies:
            if not isinstance(question, dict):
                continue
            original_id = str(question.get("originalQuestionId") or question.get("original_question_id") or "").strip()
            if not original_id:
                original_id = str(question.get("publicQuestionId") or question.get("public_question_id") or "").strip()
            ids = question.get("firestoreQuestionIds")
            if not original_id:
                continue
            firestore_id_values = (
                [str(value).strip() for value in ids if str(value or "").strip()]
                if isinstance(ids, list)
                else []
            )
            if firestore_id_values:
                source_questions = question.get("firestoreSourceQuestions")
                for index, text in enumerate(firestore_id_values):
                    if text:
                        by_original[original_id].add(text)
                        all_existing_ids.add(text)
                        if isinstance(source_questions, list) and index < len(source_questions):
                            source_question = source_questions[index]
                            if isinstance(source_question, dict):
                                source_question_id = str(source_question.get("questionId") or "").strip()
                                if not source_question_id or source_question_id == text:
                                    question_set_id = str(source_question.get("questionSetId") or "").strip()
                                    if question_set_id:
                                        existing_question_set_by_id[text] = question_set_id
                continue

            source_unique_keys = question.get("sourceUniqueKeys")
            if not isinstance(source_unique_keys, list):
                continue
            for source_unique_key in source_unique_keys:
                text = str(source_unique_key or "").strip()
                if text:
                    new_by_original[original_id].add(question_id_from_source_unique_key(text))
    return by_original, new_by_original, all_existing_ids, existing_question_set_by_id


def upload_questions(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [item for item in payload["questions"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError(f"questions array not found: {path}")


def upload_question_ids(upload_paths: list[Path]) -> set[str]:
    question_ids: set[str] = set()
    for path in upload_paths:
        for question in upload_questions(path):
            question_id = str(question.get("questionId") or "").strip()
            if question_id:
                question_ids.add(question_id)
    return question_ids


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


def planned_new_question_ids_by_original(plan_path: Path) -> dict[str, set[str]]:
    ids_by_original: dict[str, set[str]] = defaultdict(set)
    for row in load_jsonl(plan_path):
        firestore_ids = row.get("firestoreQuestionIds")
        if isinstance(firestore_ids, list) and any(str(value or "").strip() for value in firestore_ids):
            continue
        original_id = str(
            row.get("originalQuestionId")
            or row.get("publicQuestionId")
            or row.get("reviewQuestionId")
            or ""
        ).strip()
        source_unique_keys = row.get("sourceUniqueKeys")
        if not original_id or not isinstance(source_unique_keys, list):
            continue
        for source_unique_key in source_unique_keys:
            text = str(source_unique_key or "").strip()
            if text:
                ids_by_original[original_id].add(question_id_from_source_unique_key(text))
    return ids_by_original


def content_review_ready(row: dict[str, Any]) -> bool:
    for field in ("review02CorrectChoiceText", "review03ExplanationText"):
        if str(row.get(field) or "") in CONTENT_REVIEW_BLOCKING_STATUSES:
            return False
    return True


def question_set_ready_for_upload(row: dict[str, Any]) -> bool:
    review04 = str(row.get("review04QuestionSetId") or "")
    if review04 == "ok":
        return True
    if review04 == "hold" and row.get("sourceOrigin") == "firestore_snapshot":
        return True
    return False


def source_conflict_ready_for_upload(row: dict[str, Any]) -> bool:
    status = str(row.get("sourceConflictStatus") or "")
    if status in {"none", "metadata_resolved"}:
        return True
    if status != "needs_source_review":
        return False
    if not content_review_ready(row) or not question_set_ready_for_upload(row):
        return False

    policy = str(row.get("sourceContentConflictPolicy") or "")
    if row.get("sourceOrigin") == "firestore_snapshot" and "preserve_firestore" in policy:
        return True

    source_key_conflict = row.get("sourceKeyConflict")
    source_unique_keys = row.get("sourceUniqueKeys")
    if (
        row.get("sourceOrigin") == "gassyunin_site"
        and isinstance(source_key_conflict, dict)
        and source_key_conflict.get("reason") == "site_record_overlaps_existing_firestore_statements"
        and isinstance(source_unique_keys, list)
        and source_unique_keys
        and all(":site-shadow:" in str(value) for value in source_unique_keys)
    ):
        return True

    return False


def check_plan(
    plan_path: Path,
    allow_source_conflicts: bool,
    max_samples: int,
    included_question_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], Counter[str]]:
    rows = load_jsonl(plan_path)
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    status_counts = Counter(str(row.get("sourceConflictStatus") or "missing") for row in rows)
    scoped_rows: list[dict[str, Any]] = []
    for row in rows:
        if included_question_ids is not None and not (planned_question_ids(row) & included_question_ids):
            continue
        scoped_rows.append(row)

    conflict_rows = [
        row
        for row in scoped_rows
        if row.get("sourceConflictStatus") == "needs_source_review"
        and not source_conflict_ready_for_upload(row)
    ]
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
    blocked_choice_mismatch_rows = [
        row for row in scoped_rows if row.get("sourceConflictStatus") == "source_choice_count_mismatch"
    ]
    if blocked_choice_mismatch_rows:
        issue_counts["source_choice_count_mismatch"] = len(blocked_choice_mismatch_rows)
        for row in blocked_choice_mismatch_rows[:max_samples]:
            if len(issues) >= max_samples:
                break
            issues.append(
                {
                    "code": "source_choice_count_mismatch",
                    "planSequence": row.get("planSequence"),
                    "qualification": row.get("qualification"),
                    "sourceQuestionKey": row.get("sourceQuestionKey"),
                    "reviewQuestionId": row.get("reviewQuestionId"),
                }
            )
    return issues, {
        "planRowCount": len(rows),
        "scopedPlanRowCount": len(scoped_rows),
        "sourceConflictStatusCounts": dict(sorted(status_counts.items())),
        "needsSourceReviewRowCount": status_counts.get("needs_source_review", 0),
        "scopedNeedsSourceReviewBlockerCount": len(conflict_rows),
    }, issue_counts


def check_upload_json(
    upload_paths: list[Path],
    by_original_id: dict[str, set[str]],
    new_ids_by_original_id: dict[str, set[str]],
    all_existing_firestore_ids: set[str],
    existing_question_set_by_id: dict[str, str],
    max_samples: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], Counter[str]]:
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    checked = 0
    existing_origin_count = 0
    new_origin_count = 0
    question_id_counter: Counter[str] = Counter()
    for path in upload_paths:
        for question in upload_questions(path):
            checked += 1
            original_id = str(question.get("originalQuestionId") or "").strip()
            question_id = str(question.get("questionId") or "").strip()
            question_set_id = str(question.get("questionSetId") or "").strip()
            if question_id:
                question_id_counter[question_id] += 1
            if not question_set_id:
                issue_counts["missing_question_set_id"] += 1
                if len(issues) < max_samples:
                    issues.append(
                        {
                            "code": "missing_question_set_id",
                            "uploadFile": rel(path),
                            "originalQuestionId": original_id,
                            "questionId": question_id,
                        }
                    )
            expected_question_set_id = existing_question_set_by_id.get(question_id)
            if expected_question_set_id and question_set_id != expected_question_set_id:
                issue_counts["existing_firestore_question_set_id_would_change"] += 1
                if len(issues) < max_samples:
                    issues.append(
                        {
                            "code": "existing_firestore_question_set_id_would_change",
                            "uploadFile": rel(path),
                            "originalQuestionId": original_id,
                            "questionId": question_id,
                            "questionSetId": question_set_id,
                            "expectedQuestionSetId": expected_question_set_id,
                        }
                    )
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
            else:
                new_origin_count += 1
                allowed_new_ids = new_ids_by_original_id.get(original_id, set())
                if allowed_new_ids and question_id not in allowed_new_ids:
                    issue_counts["new_question_id_not_source_unique_key_derived"] += 1
                    if len(issues) < max_samples:
                        issues.append(
                            {
                                "code": "new_question_id_not_source_unique_key_derived",
                                "uploadFile": rel(path),
                                "originalQuestionId": original_id,
                                "questionId": question_id,
                                "allowedSourceUniqueKeyDerivedIds": sorted(allowed_new_ids),
                            }
                        )
                if question_id in all_existing_firestore_ids:
                    issue_counts["new_question_id_collides_with_existing_firestore_id"] += 1
                    if len(issues) < max_samples:
                        issues.append(
                            {
                                "code": "new_question_id_collides_with_existing_firestore_id",
                                "uploadFile": rel(path),
                                "originalQuestionId": original_id,
                                "questionId": question_id,
                            }
                        )

    for question_id, count in question_id_counter.items():
        if count <= 1:
            continue
        issue_counts["duplicate_upload_question_id"] += 1
        if len(issues) < max_samples:
            issues.append(
                {
                    "code": "duplicate_upload_question_id",
                    "questionId": question_id,
                    "count": count,
                }
            )
    return issues, {
        "uploadFileCount": len(upload_paths),
        "uploadQuestionCount": checked,
        "existingFirestoreOriginalQuestionCount": existing_origin_count,
        "newOriginalQuestionCount": new_origin_count,
    }, issue_counts


def run(args: argparse.Namespace) -> dict[str, Any]:
    issue_samples: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    included_question_ids = upload_question_ids(args.upload_json) if args.upload_json else None
    plan_issues, plan_summary, plan_issue_counts = check_plan(
        args.plan,
        args.allow_source_conflicts,
        args.max_samples,
        included_question_ids=included_question_ids,
    )
    issue_samples.extend(plan_issues)
    issue_counts.update(plan_issue_counts)

    upload_summary = {
        "uploadFileCount": 0,
        "uploadQuestionCount": 0,
        "existingFirestoreOriginalQuestionCount": 0,
        "newOriginalQuestionCount": 0,
    }
    if args.upload_json:
        by_original, new_by_original, all_existing_ids, existing_question_set_by_id = source_id_indexes(
            args.qualifications
        )
        for original_id, plan_ids in planned_new_question_ids_by_original(args.plan).items():
            new_by_original[original_id].update(plan_ids)
        upload_issues, upload_summary, upload_issue_counts = check_upload_json(
            args.upload_json,
            by_original,
            new_by_original,
            all_existing_ids,
            existing_question_set_by_id,
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
            "sourceConflictStatus": "included needs_source_review rows fail unless upload policy preserves existing Firestore content/IDs or adds site-shadow docs without existing-ID collision",
            "sourceChoiceCountMismatch": "included source_choice_count_mismatch rows fail",
            "questionSetId": "upload questions must have a non-empty questionSetId",
            "existingQuestionSetId": "existing Firestore document questionSetId must not change during upload",
            "questionId": "existing Firestore-derived originalQuestionId must upload to one of its existing firestoreQuestionIds",
            "newQuestionId": "new source-derived questions must use sourceUniqueKey-derived deterministic document IDs and not collide with local existing Firestore IDs",
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
