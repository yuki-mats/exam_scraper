#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.question_identity import review_question_id  # noqa: E402


PLAN_PATH = (
    ROOT_DIR
    / "docs"
    / "goals"
    / "gas-shunin-01-04-full-pass"
    / "notes"
    / "question-plan"
    / "all_questions_plan.jsonl"
)
SUMMARY_PATH = PLAN_PATH.with_name("summary.json")
STATE_PATH = ROOT_DIR / "docs" / "goals" / "gas-shunin-01-04-full-pass" / "state.yaml"
DEFAULT_REPORT_PATH = ROOT_DIR / "output" / "gas-shunin-goal-readiness-report.json"

QUALIFICATIONS = {
    "gas-shunin-kou": {
        "expected_total": 412,
        "review": ROOT_DIR
        / "output"
        / "gas-shunin-kou"
        / "review"
        / "01_04_manual_review"
        / "gas-shunin-kou_01_04_manual_review.jsonl",
    },
    "gas-shunin-otsu": {
        "expected_total": 522,
        "review": ROOT_DIR
        / "output"
        / "gas-shunin-otsu"
        / "review"
        / "01_04_manual_review"
        / "gas-shunin-otsu_01_04_manual_review.jsonl",
    },
}

PATCH_STAGES = ("questionType", "correctChoice", "explanation", "questionSet")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path | str) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def as_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row must be object: {rel(path)}:{line_no}")
        rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter, key=lambda item: str(item))}


def truthy_string(value: Any) -> str:
    return str(value or "").strip()


def firestore_ids(question: dict[str, Any]) -> list[str]:
    ids = question.get("firestoreQuestionIds")
    if not isinstance(ids, list):
        return []
    return [truthy_string(item) for item in ids if truthy_string(item)]


def statement_count(question: dict[str, Any]) -> int:
    choice_texts = question.get("choiceTextList")
    if isinstance(choice_texts, list):
        return len(choice_texts)
    source_count = question.get("sourceStatementCount")
    if isinstance(source_count, int):
        return source_count
    correct = question.get("correctChoiceText")
    if isinstance(correct, list):
        return len(correct)
    return 0


def iter_source_paths(qualification: str) -> list[Path]:
    root = ROOT_DIR / "output" / qualification / "questions_json"
    return sorted(path for path in root.glob("*/00_source/question_*.json") if "99_archived" not in path.parts)


def load_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for qualification in QUALIFICATIONS:
        for path in iter_source_paths(qualification):
            payload = load_json(path)
            bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
            if not isinstance(bodies, list):
                issues.append(
                    {
                        "severity": "error",
                        "code": "missing_question_bodies",
                        "qualification": qualification,
                        "sourceFile": rel(path),
                    }
                )
                continue
            for index, question in enumerate(bodies, start=1):
                if not isinstance(question, dict):
                    issues.append(
                        {
                            "severity": "error",
                            "code": "invalid_question_body",
                            "qualification": qualification,
                            "sourceFile": rel(path),
                            "questionIndex": index,
                        }
                    )
                    continue
                ids = firestore_ids(question)
                count = statement_count(question)
                rows.append(
                    {
                        "qualification": qualification,
                        "sourceFile": rel(path),
                        "questionIndexInFile": index,
                        "question": question,
                        "reviewQuestionId": review_question_id(question),
                        "firestoreQuestionIds": ids,
                        "statementCount": count,
                        "sourceOrigin": question.get("sourceOrigin") or ("firestore_snapshot" if ids else "gassyunin_site"),
                    }
                )
    return rows, issues


def load_plan(path: Path) -> list[dict[str, Any]]:
    rows = load_jsonl(path)
    for row in rows:
        row["sourceFile"] = rel(as_path(str(row.get("sourceFile") or "")))
    return rows


def state_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": rel(path), "exists": False}
    text = path.read_text(encoding="utf-8")
    active = re.search(r"^active_task:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    completed = re.search(r"^\s*completed_questions:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    pending = re.search(r"^\s*pending_questions:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    active_task_status = None
    if active:
        task_pattern = re.compile(
            rf"^\s*-\s*id:\s*{re.escape(active.group(1))}\s*$.*?^\s*status:\s*(\S+)\s*$",
            flags=re.MULTILINE | re.DOTALL,
        )
        match = task_pattern.search(text)
        if match:
            active_task_status = match.group(1)
    return {
        "path": rel(path),
        "exists": True,
        "activeTask": active.group(1) if active else None,
        "activeTaskStatus": active_task_status,
        "completedQuestions": int(completed.group(1)) if completed else None,
        "pendingQuestions": int(pending.group(1)) if pending else None,
    }


def add_issue(
    issues: list[dict[str, Any]],
    *,
    severity: str,
    code: str,
    detail: str,
    row: dict[str, Any] | None = None,
    **extra: Any,
) -> None:
    issue: dict[str, Any] = {"severity": severity, "code": code, "detail": detail}
    if row:
        for key in (
            "planSequence",
            "qualification",
            "sourceFile",
            "questionIndexInFile",
            "questionLabel",
            "reviewQuestionId",
            "qualifiedReviewQuestionId",
            "sourceQuestionKey",
        ):
            if row.get(key) not in (None, ""):
                issue[key] = row.get(key)
    issue.update(extra)
    issues.append(issue)


def summarize_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualified_ids = Counter(truthy_string(row.get("qualifiedReviewQuestionId")) for row in rows)
    duplicate_ids = [key for key, count in qualified_ids.items() if key and count > 1]
    next_pending = next(
        (
            {
                "planSequence": row.get("planSequence"),
                "qualification": row.get("qualification"),
                "examYear": row.get("examYear"),
                "questionLabel": row.get("questionLabel"),
                "reviewQuestionId": row.get("reviewQuestionId"),
                "sourceFile": row.get("sourceFile"),
            }
            for row in sorted(rows, key=lambda item: int(item.get("planSequence") or 0))
            if row.get("executionStatus") == "pending"
        ),
        None,
    )
    return {
        "rowCount": len(rows),
        "qualificationCounts": counter_dict(Counter(row.get("qualification") for row in rows)),
        "executionStatusCounts": counter_dict(Counter(row.get("executionStatus") for row in rows)),
        "reviewDecisionCounts": counter_dict(Counter(row.get("reviewDecision") for row in rows)),
        "sourceOriginCounts": counter_dict(Counter(row.get("sourceOrigin") for row in rows)),
        "executionPhaseCounts": counter_dict(Counter(row.get("executionPhase") for row in rows)),
        "sourceConflictStatusCounts": counter_dict(Counter(row.get("sourceConflictStatus") for row in rows)),
        "duplicateQualifiedReviewQuestionIdCount": len(duplicate_ids),
        "duplicateQualifiedReviewQuestionIdSamples": duplicate_ids[:10],
        "nextPending": next_pending,
    }


def summarize_sources(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_files = {row["sourceFile"] for row in rows}
    firestore_rows = [row for row in rows if row["firestoreQuestionIds"]]
    site_rows = [row for row in rows if not row["firestoreQuestionIds"]]
    firestore_doc_counter: Counter[str] = Counter()
    source_unique_counter: Counter[str] = Counter()
    for row in rows:
        firestore_doc_counter.update(row["firestoreQuestionIds"])
        question = row["question"]
        keys = question.get("sourceUniqueKeys")
        if isinstance(keys, list):
            source_unique_counter.update(truthy_string(key) for key in keys if truthy_string(key))
    duplicate_firestore_ids = [key for key, count in firestore_doc_counter.items() if count > 1]
    duplicate_source_unique_keys = [key for key, count in source_unique_counter.items() if count > 1]
    return {
        "fileCount": len(source_files),
        "questionCount": len(rows),
        "statementCount": sum(row["statementCount"] for row in rows),
        "qualificationCounts": counter_dict(Counter(row["qualification"] for row in rows)),
        "sourceOriginCounts": counter_dict(Counter(row["sourceOrigin"] for row in rows)),
        "firestoreQuestionCount": len(firestore_rows),
        "siteQuestionCount": len(site_rows),
        "firestoreStatementDocIdCount": sum(len(row["firestoreQuestionIds"]) for row in firestore_rows),
        "uniqueFirestoreStatementDocIdCount": len(firestore_doc_counter),
        "duplicateFirestoreStatementDocIdCount": len(duplicate_firestore_ids),
        "duplicateFirestoreStatementDocIdSamples": duplicate_firestore_ids[:10],
        "uniqueSourceUniqueKeyCount": len(source_unique_counter),
        "duplicateSourceUniqueKeyCount": len(duplicate_source_unique_keys),
        "duplicateSourceUniqueKeySamples": duplicate_source_unique_keys[:10],
    }


def summarize_review_ledgers() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for qualification, config in QUALIFICATIONS.items():
        path = config["review"]
        rows = load_jsonl(path)
        review_ids = Counter(truthy_string(row.get("reviewQuestionId")) for row in rows)
        duplicates = [key for key, count in review_ids.items() if key and count > 1]
        if len(rows) != config["expected_total"]:
            add_issue(
                issues,
                severity="error",
                code="review_ledger_count_mismatch",
                detail="review ledger row count must match expected total",
                qualification=qualification,
                expected=config["expected_total"],
                actual=len(rows),
                reviewPath=rel(path),
            )
        if duplicates:
            add_issue(
                issues,
                severity="error",
                code="duplicate_review_ledger_id",
                detail="review ledger contains duplicate reviewQuestionId values",
                qualification=qualification,
                samples=duplicates[:10],
                reviewPath=rel(path),
            )
        summary[qualification] = {
            "path": rel(path),
            "rowCount": len(rows),
            "expectedTotal": config["expected_total"],
            "reviewDecisionCounts": counter_dict(Counter(row.get("reviewDecision") for row in rows)),
            "duplicateReviewQuestionIdCount": len(duplicates),
            "duplicateReviewQuestionIdSamples": duplicates[:10],
        }
    return summary, issues


def check_source_invariants(source_rows: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    source_key_counter: Counter[tuple[str, str, int]] = Counter()
    for row in source_rows:
        source_key_counter[(row["qualification"], row["sourceFile"], int(row["questionIndexInFile"]))] += 1
        question = row["question"]
        ids = row["firestoreQuestionIds"]
        count = row["statementCount"]
        statuses = question.get("statementSourceStatuses")
        if ids:
            if row["sourceOrigin"] != "firestore_snapshot":
                add_issue(
                    issues,
                    severity="error",
                    code="firestore_source_origin_mismatch",
                    detail="Firestore-derived source question must use sourceOrigin=firestore_snapshot",
                    row=row,
                )
            if len(ids) != count:
                add_issue(
                    issues,
                    severity="error",
                    code="firestore_question_ids_count_mismatch",
                    detail="firestoreQuestionIds must contain one existing doc ID per statement",
                    row=row,
                    firestoreQuestionIdCount=len(ids),
                    statementCount=count,
                )
            if not isinstance(statuses, list) or len(statuses) != count:
                add_issue(
                    issues,
                    severity="error",
                    code="statement_source_status_count_mismatch",
                    detail="statementSourceStatuses must match statement count",
                    row=row,
                    statementSourceStatusCount=len(statuses) if isinstance(statuses, list) else None,
                    statementCount=count,
                )
            elif len(ids) == count:
                for index, firestore_id in enumerate(ids, start=1):
                    status = statuses[index - 1] if index - 1 < len(statuses) else {}
                    if status.get("firestoreQuestionId") != firestore_id:
                        add_issue(
                            issues,
                            severity="error",
                            code="statement_firestore_id_mismatch",
                            detail="statementSourceStatuses[].firestoreQuestionId must match firestoreQuestionIds order",
                            row=row,
                            statementNo=index,
                            expected=firestore_id,
                            actual=status.get("firestoreQuestionId"),
                        )
                    if status.get("firestoreRegistered") is not True:
                        add_issue(
                            issues,
                            severity="error",
                            code="statement_firestore_registered_false",
                            detail="Firestore-derived statement must be marked firestoreRegistered=true",
                            row=row,
                            statementNo=index,
                        )
        else:
            if row["sourceOrigin"] != "gassyunin_site":
                add_issue(
                    issues,
                    severity="error",
                    code="site_source_origin_mismatch",
                    detail="Site-derived source question must use sourceOrigin=gassyunin_site",
                    row=row,
                )
    for (qualification, source_file, index), count in source_key_counter.items():
        if count > 1:
            add_issue(
                issues,
                severity="error",
                code="duplicate_source_question_position",
                detail="A source file/question index maps to multiple question rows",
                qualification=qualification,
                sourceFile=source_file,
                questionIndexInFile=index,
                count=count,
            )


def check_plan_invariants(
    plan_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    source_by_position = {
        (row["qualification"], row["sourceFile"], int(row["questionIndexInFile"])): row for row in source_rows
    }
    seen_qualified: Counter[str] = Counter()
    for row in plan_rows:
        qualified = truthy_string(row.get("qualifiedReviewQuestionId"))
        seen_qualified[qualified] += 1
        key = (
            truthy_string(row.get("qualification")),
            truthy_string(row.get("sourceFile")),
            int(row.get("questionIndexInFile") or 0),
        )
        source_row = source_by_position.get(key)
        if not source_row:
            add_issue(
                issues,
                severity="error",
                code="plan_source_row_missing",
                detail="Plan row must point to an existing 00_source question position",
                row=row,
            )
            continue

        source_review_id = source_row["reviewQuestionId"]
        if row.get("reviewQuestionId") != source_review_id:
            add_issue(
                issues,
                severity="error",
                code="plan_review_question_id_mismatch",
                detail="Plan reviewQuestionId must match 00_source-derived review key",
                row=row,
                expected=source_review_id,
                actual=row.get("reviewQuestionId"),
            )
        expected_qualified = f"{row.get('qualification')}:{source_review_id}"
        if row.get("qualifiedReviewQuestionId") != expected_qualified:
            add_issue(
                issues,
                severity="error",
                code="plan_qualified_review_question_id_mismatch",
                detail="qualifiedReviewQuestionId must be qualification + reviewQuestionId",
                row=row,
                expected=expected_qualified,
                actual=row.get("qualifiedReviewQuestionId"),
            )
        if source_row["firestoreQuestionIds"]:
            expected = "firestore:" + ",".join(source_row["firestoreQuestionIds"])
            if row.get("reviewQuestionId") != expected:
                add_issue(
                    issues,
                    severity="error",
                    code="firestore_review_question_id_policy_mismatch",
                    detail="Existing Firestore rows must use firestoreQuestionIds as the review key",
                    row=row,
                    expected=expected,
                    actual=row.get("reviewQuestionId"),
                )
            if row.get("firestoreQuestionIds") != source_row["firestoreQuestionIds"]:
                add_issue(
                    issues,
                    severity="error",
                    code="plan_firestore_question_ids_mismatch",
                    detail="Plan firestoreQuestionIds must match 00_source",
                    row=row,
                    expected=source_row["firestoreQuestionIds"],
                    actual=row.get("firestoreQuestionIds"),
                )
            if not row.get("originalQuestionId"):
                add_issue(
                    issues,
                    severity="error",
                    code="missing_existing_original_question_id",
                    detail="Existing Firestore plan rows must preserve originalQuestionId",
                    row=row,
                )
        if row.get("sourceConflictStatus") == "needs_source_review":
            add_issue(
                issues,
                severity="warning",
                code="source_conflict_needs_review",
                detail="Strict upload must remain blocked until this source conflict is reviewed or explicitly allowed",
                row=row,
                sourceContentConflictCount=row.get("sourceContentConflictCount"),
            )
    for qualified, count in seen_qualified.items():
        if qualified and count > 1:
            add_issue(
                issues,
                severity="error",
                code="duplicate_plan_qualified_review_question_id",
                detail="Plan must have one row per review question",
                qualifiedReviewQuestionId=qualified,
                count=count,
            )


def load_patch(path: Path, cache: dict[Path, list[dict[str, Any]]], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if path in cache:
        return cache[path]
    if not path.exists():
        add_issue(
            issues,
            severity="error",
            code="patch_file_missing",
            detail="Patch file referenced by plan is missing",
            patchFile=rel(path),
        )
        cache[path] = []
        return []
    payload = load_json(path)
    if not isinstance(payload, list):
        add_issue(
            issues,
            severity="error",
            code="patch_file_not_array",
            detail="Patch file must be a JSON array",
            patchFile=rel(path),
        )
        cache[path] = []
        return []
    rows = [row for row in payload if isinstance(row, dict)]
    if len(rows) != len(payload):
        add_issue(
            issues,
            severity="error",
            code="patch_file_contains_non_object",
            detail="Patch file contains non-object entries",
            patchFile=rel(path),
        )
    cache[path] = rows
    return rows


def check_patch_invariants(plan_rows: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    cache: dict[Path, list[dict[str, Any]]] = {}
    patch_file_counter: Counter[str] = Counter()
    checked_entries = 0
    for row in plan_rows:
        patch_files = row.get("patchFiles")
        if not isinstance(patch_files, dict):
            add_issue(
                issues,
                severity="error",
                code="plan_patch_files_missing",
                detail="Plan row must include patchFiles",
                row=row,
            )
            continue
        for stage in PATCH_STAGES:
            patch_value = patch_files.get(stage)
            if not patch_value:
                add_issue(
                    issues,
                    severity="error",
                    code="plan_patch_stage_missing",
                    detail=f"Plan row must include patchFiles.{stage}",
                    row=row,
                    stage=stage,
                )
                continue
            patch_path = as_path(str(patch_value))
            patch_file_counter[rel(patch_path)] += 1
            patch_rows = load_patch(patch_path, cache, issues)
            matches = [
                patch_row
                for patch_row in patch_rows
                if patch_row.get("original_question_id") == row.get("reviewQuestionId")
            ]
            checked_entries += len(matches)
            if len(matches) != 1:
                add_issue(
                    issues,
                    severity="error",
                    code="patch_original_question_id_count_mismatch",
                    detail="Each patch stage must contain exactly one entry per one-question review row",
                    row=row,
                    stage=stage,
                    patchFile=rel(patch_path),
                    matchCount=len(matches),
                )
                continue
            patch_row = matches[0]
            if row.get("sourceFile") and patch_row.get("source_filepath") != row.get("sourceFile"):
                add_issue(
                    issues,
                    severity="error",
                    code="patch_source_filepath_mismatch",
                    detail="Patch entry must point back to the same 00_source file",
                    row=row,
                    stage=stage,
                    patchFile=rel(patch_path),
                    expected=row.get("sourceFile"),
                    actual=patch_row.get("source_filepath"),
                )
            if row.get("sourceOrigin") == "firestore_snapshot":
                expected_original = row.get("originalQuestionId")
                if patch_row.get("source_original_question_id") != expected_original:
                    add_issue(
                        issues,
                        severity="error",
                        code="patch_source_original_question_id_mismatch",
                        detail="Existing Firestore patch entry must preserve originalQuestionId separately from review key",
                        row=row,
                        stage=stage,
                        patchFile=rel(patch_path),
                        expected=expected_original,
                        actual=patch_row.get("source_original_question_id"),
                    )
            if stage == "explanation" and row.get("executionStatus") == "done":
                choice_count = int(row.get("choiceCount") or row.get("sourceStatementCount") or 0)
                suggested = patch_row.get("suggestedQuestions")
                details = patch_row.get("suggestedQuestionDetails")
                if not isinstance(suggested, list) or len(suggested) != choice_count:
                    add_issue(
                        issues,
                        severity="error",
                        code="suggested_questions_count_mismatch",
                        detail="Done explanation patches must keep suggestedQuestions per choice",
                        row=row,
                        stage=stage,
                        patchFile=rel(patch_path),
                        expected=choice_count,
                        actual=len(suggested) if isinstance(suggested, list) else None,
                    )
                if not isinstance(details, list) or len(details) != choice_count:
                    add_issue(
                        issues,
                        severity="error",
                        code="suggested_question_details_count_mismatch",
                        detail="Done explanation patches must keep suggestedQuestionDetails per choice",
                        row=row,
                        stage=stage,
                        patchFile=rel(patch_path),
                        expected=choice_count,
                        actual=len(details) if isinstance(details, list) else None,
                    )
    return {
        "patchFileCount": len(cache),
        "planPatchReferences": sum(patch_file_counter.values()),
        "matchedPatchEntries": checked_entries,
        "patchFilesByReferenceCount": counter_dict(patch_file_counter),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    plan_rows = load_plan(args.plan)
    source_rows, source_issues = load_sources()
    issues.extend(source_issues)
    check_source_invariants(source_rows, issues)
    check_plan_invariants(plan_rows, source_rows, issues)
    patch_summary = check_patch_invariants(plan_rows, issues)
    review_summary, review_issues = summarize_review_ledgers()
    issues.extend(review_issues)

    plan_summary = summarize_plan(plan_rows)
    source_summary = summarize_sources(source_rows)
    expected_total = sum(config["expected_total"] for config in QUALIFICATIONS.values())
    if plan_summary["rowCount"] != expected_total:
        add_issue(
            issues,
            severity="error",
            code="plan_total_count_mismatch",
            detail="Plan total must match expected gas-shunin kou+otsu work count",
            expected=expected_total,
            actual=plan_summary["rowCount"],
        )
    if source_summary["questionCount"] != expected_total:
        add_issue(
            issues,
            severity="error",
            code="source_total_count_mismatch",
            detail="00_source total question count must match expected gas-shunin kou+otsu work count",
            expected=expected_total,
            actual=source_summary["questionCount"],
        )

    severity_counts = Counter(issue["severity"] for issue in issues)
    issue_code_counts = Counter(issue["code"] for issue in issues)
    error_count = severity_counts.get("error", 0)
    warning_count = severity_counts.get("warning", 0)
    needs_source_review = plan_summary["sourceConflictStatusCounts"].get("needs_source_review", 0)

    return {
        "schemaVersion": "gas-shunin-goal-readiness-audit/v1",
        "generatedAt": utc_now(),
        "ok": error_count == 0,
        "readyForQuestionWork": error_count == 0,
        "readyForStrictUpload": error_count == 0 and needs_source_review == 0,
        "strictUploadBlocker": {
            "blocked": needs_source_review > 0,
            "reason": "sourceConflictStatus=needs_source_review remains unresolved",
            "count": needs_source_review,
        },
        "paths": {
            "plan": rel(args.plan),
            "planSummary": rel(SUMMARY_PATH),
            "state": rel(STATE_PATH),
            "report": rel(args.report),
        },
        "state": state_snapshot(STATE_PATH),
        "expected": {
            "totalQuestions": expected_total,
            "qualifications": {key: value["expected_total"] for key, value in QUALIFICATIONS.items()},
            "policy": {
                "workUnit": "one 00_source question body per review/patch row",
                "existingFirestoreQuestionId": "firestoreQuestionIds are the only upload ID source for existing Firestore statement-level question docs",
                "sourceUniqueKey": "merge/provenance key only; it must not replace existing Firestore document IDs",
            },
        },
        "plan": plan_summary,
        "sources": source_summary,
        "reviewLedgers": review_summary,
        "patches": patch_summary,
        "issueCount": len(issues),
        "issueSeverityCounts": counter_dict(severity_counts),
        "issueCodeCounts": counter_dict(issue_code_counts),
        "sampleIssueCount": min(len(issues), args.max_issues),
        "issues": issues[: args.max_issues],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit gas-shunin goal readiness and Firestore ID preservation.")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--max-issues", type=int, default=200)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
