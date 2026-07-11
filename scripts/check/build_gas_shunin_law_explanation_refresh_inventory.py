#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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

from scripts.check.check_gas_shunin_law_explanation_publication import (  # noqa: E402
    DEFAULT_REVIEW_DIR,
    DEFAULT_UPLOAD_DIR,
    load_upload_questions,
    review_choice_map,
)
from scripts.pipeline.finalize_gas_shunin_law_explanations import (  # noqa: E402
    load_review_records,
    normalize_label,
    normalize_text,
    rel,
)


DEFAULT_OUTPUT_DIR = (
    ROOT_DIR / "output" / "gas-shunin-all" / "review" / "law_explanation_refresh"
)
WRONG_PREFIX = "間違い。"
CORRECT_PREFIX = "正しい。"
AUDIT_STATUS_VALUES = {
    "pending",
    "reviewed_no_change",
    "reviewed_needs_update",
    "patch_applied",
    "published",
    "hold",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def question_number(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 9999


def has_explicit_wrong_difference(explanation: Any) -> bool:
    text = str(explanation or "").strip()
    if not text.startswith(WRONG_PREFIX):
        return False
    first_reason = text.removeprefix(WRONG_PREFIX).strip().split("。", 1)[0]
    return bool(first_reason) and any(
        marker in first_reason
        for marker in ("誤り", "ではなく", "異なる", "一致しない")
    )


def law_revision_matches_reference(law_revision_facts: Any, law_references: Any) -> bool:
    if not isinstance(law_revision_facts, dict):
        return False
    current = law_revision_facts.get("current")
    if not isinstance(current, dict) or not isinstance(law_references, list):
        return False
    current_law_id = str(current.get("lawId") or "")
    current_law_title = str(current.get("lawTitle") or "")
    current_article = str(current.get("article") or "")
    if not current_article or not (current_law_id or current_law_title):
        return False
    for reference in law_references:
        if not isinstance(reference, dict):
            continue
        if current_law_id:
            if str(reference.get("lawId") or "") != current_law_id:
                continue
        elif str(reference.get("lawTitle") or "") != current_law_title:
            continue
        if str(reference.get("article") or "") != current_article:
            continue
        return True
    return False


def resolve_explanation_patch_file(value: Any) -> tuple[str, str | None]:
    relative = Path(str(value or ""))
    exact = ROOT_DIR / relative
    suffixed = exact.with_name(f"{exact.stem}_explanationText_added{exact.suffix}")
    existing = [candidate for candidate in (exact, suffixed) if candidate.is_file()]
    if len(existing) == 1:
        return rel(existing[0]), None
    if not existing:
        return str(relative), f"explanation patch file not found: {relative}"
    return str(relative), f"ambiguous explanation patch file: {relative}"


def load_existing_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("auditKey"):
            result[str(row["auditKey"])] = row
    return result


def load_decisions(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not path.is_dir():
        return result
    for decision_path in sorted(path.glob("*.json")):
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        if not isinstance(decision, dict) or not decision.get("auditKey"):
            continue
        result[str(decision["auditKey"])] = decision
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def build_rows(
    *,
    review_dir: Path,
    upload_dir: Path,
    existing_ledger: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    reviews = load_review_records(review_dir)
    questions, _ = load_upload_questions(upload_dir)
    questions_by_id = {str(question.get("questionId") or ""): question for question in questions}
    mapped, mapping_errors = review_choice_map(reviews, questions)

    mapped_by_review: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for question_id, mapping in mapped.items():
        mapped_by_review[id(mapping["review"])].append((question_id, mapping))

    staged: list[dict[str, Any]] = []
    for review in reviews:
        explanation_patch_file, patch_error = resolve_explanation_patch_file(
            review.get("explanationPatchFile")
        )
        if patch_error:
            mapping_errors.append(
                f"{review.get('sourceQuestionKey')}: {patch_error}"
            )
        mapped_choices = sorted(
            mapped_by_review.get(id(review), []),
            key=lambda item: int(item[1]["choiceIndex"]),
        )
        question_ids = [question_id for question_id, _ in mapped_choices]
        key_suffix = canonical_hash(question_ids)[:12]
        audit_key = f"{review.get('sourceQuestionKey')}:{key_suffix}"
        choice_rows: list[dict[str, Any]] = []
        issue_codes: set[str] = set()

        for question_id, mapping in mapped_choices:
            question = questions_by_id[question_id]
            verdict = normalize_label(question.get("correctChoiceText"))
            explanation = str(question.get("explanationText") or "").strip()
            choice_issues: list[str] = []
            expected_prefix = f"{verdict}。"
            if not explanation.startswith(expected_prefix):
                choice_issues.append("explanation_prefix_mismatch")
            if verdict == "間違い" and not has_explicit_wrong_difference(explanation):
                choice_issues.append("wrong_difference_not_explicit")
            if verdict == "正しい" and len(normalize_text(explanation.removeprefix(CORRECT_PREFIX))) < 20:
                choice_issues.append("correct_reason_too_short")
            law_references = question.get("lawReferences")
            if not isinstance(law_references, list) or not law_references:
                choice_issues.append("law_references_missing")
            elif any(
                not isinstance(reference, dict)
                or reference.get("verificationStatus") != "verified"
                for reference in law_references
            ):
                choice_issues.append("law_reference_not_verified")
            if isinstance(law_references, list) and any(
                isinstance(reference, dict)
                and str(reference.get("reason") or "").strip() != explanation
                for reference in law_references
            ):
                choice_issues.append("law_reference_reason_mismatch")
            law_revision_facts = question.get("lawRevisionFacts")
            if not law_revision_matches_reference(law_revision_facts, law_references):
                choice_issues.append("law_revision_current_basis_mismatch")
            issue_codes.update(choice_issues)
            choice_rows.append(
                {
                    "choiceIndex": int(mapping["choiceIndex"]),
                    "questionId": question_id,
                    "verdict": verdict,
                    "choiceText": question.get("originalQuestionChoiceText"),
                    "explanationText": explanation,
                    "lawReferences": law_references if isinstance(law_references, list) else [],
                    "lawRevisionFacts": law_revision_facts,
                    "directBasis": mapping["choiceReview"].get("directBasis"),
                    "basisText": mapping["choiceReview"].get("basisText"),
                    "qualityIssueCodes": choice_issues,
                }
            )

        suggested_questions = (
            questions_by_id[question_ids[0]].get("suggestedQuestions") if question_ids else []
        )
        suggested_question_details = (
            questions_by_id[question_ids[0]].get("suggestedQuestionDetails") if question_ids else []
        )
        input_payload = {
            "sourceQuestionKey": review.get("sourceQuestionKey"),
            "sourceCorrectChoiceText": review.get("sourceCorrectChoiceText"),
            "questionIds": question_ids,
            "choices": choice_rows,
            "suggestedQuestions": suggested_questions,
            "suggestedQuestionDetails": suggested_question_details,
        }
        audit_input_hash = canonical_hash(input_payload)
        previous = existing_ledger.get(audit_key, {})
        unchanged = previous.get("auditInputHash") == audit_input_hash
        previous_status = str(previous.get("status") or "pending")
        status = previous_status if unchanged and previous_status in AUDIT_STATUS_VALUES else "pending"
        staged.append(
            {
                "schemaVersion": "gas-shunin-law-explanation-refresh-ledger/v1",
                "sequence": 0,
                "auditKey": audit_key,
                "auditInputHash": audit_input_hash,
                "status": status,
                "sourceQuestionKey": review.get("sourceQuestionKey"),
                "qualification": review.get("qualification"),
                "examYear": review.get("examYear"),
                "questionLabel": review.get("questionLabel"),
                "sourceFile": review.get("sourceFile"),
                "explanationPatchFile": explanation_patch_file,
                "choiceCount": len(choice_rows),
                "questionIds": question_ids,
                "qualityIssueCodes": sorted(issue_codes),
                "choices": choice_rows,
                "suggestedQuestions": suggested_questions,
                "suggestedQuestionDetails": suggested_question_details,
                "reviewedAt": previous.get("reviewedAt") if unchanged else None,
                "reviewDecision": previous.get("reviewDecision") if unchanged else None,
                "reviewNotes": previous.get("reviewNotes") if unchanged else None,
                "evidence": previous.get("evidence") if unchanged else None,
            }
        )

    qualification_order = {"gas-shunin-kou": 0, "gas-shunin-otsu": 1}
    staged.sort(
        key=lambda row: (
            qualification_order.get(str(row.get("qualification")), 99),
            int(row.get("examYear") or 0),
            question_number(row.get("questionLabel")),
            str(row.get("auditKey")),
        )
    )
    for sequence, row in enumerate(staged, start=1):
        row["sequence"] = sequence
    return staged, mapping_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the per-question gas-shunin law explanation refresh ledger.")
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--upload-dir", type=Path, default=DEFAULT_UPLOAD_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    ledger_path = args.output_dir / "review_ledger.jsonl"
    existing_ledger = load_existing_ledger(ledger_path)
    rows, mapping_errors = build_rows(
        review_dir=args.review_dir.resolve(),
        upload_dir=args.upload_dir.resolve(),
        existing_ledger=existing_ledger,
    )
    decision_errors: list[str] = []
    decisions = load_decisions(args.output_dir / "decisions")
    rows_by_key = {str(row["auditKey"]): row for row in rows}
    for audit_key, decision in decisions.items():
        row = rows_by_key.get(audit_key)
        if row is None:
            decision_errors.append(f"decision auditKey not found: {audit_key}")
            continue
        if decision.get("auditInputHash") != row.get("auditInputHash"):
            decision_errors.append(f"stale decision input hash: {audit_key}")
            continue
        status = str(decision.get("status") or "")
        if status not in AUDIT_STATUS_VALUES:
            decision_errors.append(f"invalid decision status={status}: {audit_key}")
            continue
        row["status"] = status
        row["reviewedAt"] = decision.get("reviewedAt")
        row["reviewDecision"] = decision.get("reviewDecision")
        row["reviewNotes"] = decision.get("reviewNotes")
        row["evidence"] = decision.get("evidence")
    write_jsonl(ledger_path, rows)

    correct_choice_flags = [
        {
            "schemaVersion": "gas-shunin-correct-choice-review-flag/v1",
            "sequence": decision.get("sequence"),
            "auditKey": audit_key,
            "auditInputHash": decision.get("auditInputHash"),
            "reviewedAt": decision.get("reviewedAt"),
            "sourceQuestionKey": rows_by_key.get(audit_key, {}).get("sourceQuestionKey"),
            "qualification": rows_by_key.get(audit_key, {}).get("qualification"),
            "examYear": rows_by_key.get(audit_key, {}).get("examYear"),
            "questionLabel": rows_by_key.get(audit_key, {}).get("questionLabel"),
            "questionIds": rows_by_key.get(audit_key, {}).get("questionIds"),
            "flags": decision.get("reviewDecision", {})
            .get("correctChoiceTextReview", {})
            .get("flags", []),
            "status": "awaiting_user_confirmation",
        }
        for audit_key, decision in decisions.items()
        if decision.get("reviewDecision", {})
        .get("correctChoiceTextReview", {})
        .get("status")
        == "flagged"
    ]
    write_jsonl(args.output_dir / "correct_choice_review_flags.jsonl", correct_choice_flags)

    status_counts = Counter(str(row["status"]) for row in rows)
    issue_question_counts = Counter(
        issue for row in rows for issue in set(row.get("qualityIssueCodes") or [])
    )
    issue_choice_counts = Counter(
        issue
        for row in rows
        for choice in row.get("choices") or []
        for issue in choice.get("qualityIssueCodes") or []
    )
    summary = {
        "schemaVersion": "gas-shunin-law-explanation-refresh-summary/v1",
        "generatedAt": utc_now(),
        "reviewDir": rel(args.review_dir),
        "uploadDir": rel(args.upload_dir),
        "ledger": rel(ledger_path),
        "questionCount": len(rows),
        "choiceCount": sum(int(row["choiceCount"]) for row in rows),
        "statusCounts": dict(sorted(status_counts.items())),
        "pendingReviewQuestionCount": status_counts.get("pending", 0),
        "needsUpdateQuestionCount": status_counts.get("reviewed_needs_update", 0),
        "holdQuestionCount": status_counts.get("hold", 0),
        "patchAppliedQuestionCount": status_counts.get("patch_applied", 0),
        "publishedQuestionCount": status_counts.get("published", 0),
        "completedQuestionCount": (
            status_counts.get("reviewed_no_change", 0) + status_counts.get("published", 0)
        ),
        "remainingQuestionCount": (
            status_counts.get("pending", 0)
            + status_counts.get("reviewed_needs_update", 0)
            + status_counts.get("patch_applied", 0)
            + status_counts.get("hold", 0)
        ),
        "correctChoiceTextFlagCount": len(correct_choice_flags),
        "issueQuestionCounts": dict(sorted(issue_question_counts.items())),
        "issueChoiceCounts": dict(sorted(issue_choice_counts.items())),
        "mappingErrorCount": len(mapping_errors),
        "mappingErrors": mapping_errors,
        "decisionErrorCount": len(decision_errors),
        "decisionErrors": decision_errors,
    }
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if mapping_errors or decision_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
