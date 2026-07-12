#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.pipeline.finalize_gas_shunin_law_explanations import (  # noqa: E402
    DEFAULT_REVIEW_DIR,
    find_patch_entry,
    load_json,
    load_review_records,
    normalize_label,
    normalize_text,
    rel,
    resolve_patch_path,
)


DEFAULT_UPLOAD_DIR = (
    ROOT_DIR / "output" / "gas-shunin-all" / "questions_json" / "upload_ready_subset_all"
)
DEFAULT_REPORT = (
    ROOT_DIR
    / "output"
    / "gas-shunin-all"
    / "review"
    / "manual_law_explanation_audit"
    / "gas_shunin_law_explanation_publication_check.json"
)
LAW_ID_RE = re.compile(
    r"^gas-shunin-(?:kou|otsu)-\d{4}-law-q\d+-s\d+(?:-site-shadow-[0-9a-f]+)?$"
)
EXPLANATION_PREFIXES = {
    "正しい": ("正しい。", "正解。"),
    "間違い": ("間違い。", "不正解。"),
}
EXTERNAL_PRIMARY_SOURCE_PREFIXES = (
    "https://www.meti.go.jp/",
    "https://www.jia-page.or.jp/files/user/doc/exam/",
    "https://laws.e-gov.go.jp/",
)
ARCHIVED_JIA_OFFICIAL_RE = re.compile(
    r"^/web/\d+(?:id_)?/https://www\.jia-page\.or\.jp/files/user/doc/exam/[^/?#]+\.pdf$"
)


def valid_explanation_prefix(verdict: str, explanation: str) -> bool:
    return explanation.startswith(EXPLANATION_PREFIXES.get(verdict, ()))


def strip_explanation_prefix(verdict: str, explanation: str) -> str:
    for prefix in EXPLANATION_PREFIXES.get(verdict, ()):
        if explanation.startswith(prefix):
            return explanation.removeprefix(prefix).strip()
    return explanation.strip()


def review_correct_choice_labels(review: dict[str, Any]) -> list[Any] | None:
    source = review.get("sourceCorrectChoiceText")
    patched = review.get("patchedCorrectChoiceText")
    if (
        isinstance(patched, list)
        and isinstance(source, list)
        and len(patched) == len(source)
    ):
        return patched
    return source if isinstance(source, list) else None


def valid_external_primary_reference(ref: dict[str, Any]) -> bool:
    source_url = str(ref.get("sourceUrl") or "")
    if not ref.get("externalPrimarySource"):
        return False
    if source_url.startswith(EXTERNAL_PRIMARY_SOURCE_PREFIXES):
        return True
    parsed = urlparse(source_url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "web.archive.org"
        and bool(ARCHIVED_JIA_OFFICIAL_RE.fullmatch(parsed.path))
        and not parsed.query
        and not parsed.fragment
    )


def is_law_section_doc(question: dict[str, Any]) -> bool:
    question_id = str(question.get("questionId") or "")
    original_id = str(question.get("originalQuestionId") or "")
    return bool(LAW_ID_RE.fullmatch(question_id)) or original_id.startswith(
        "gasushunin-koushu-hourei-"
    )


def upload_files(upload_dir: Path) -> list[Path]:
    files = sorted(upload_dir.glob("*_firestore_*.json"))
    if not files:
        files = sorted(upload_dir.glob("*_firestore.json"))
    return files


def load_upload_questions(upload_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    questions: list[dict[str, Any]] = []
    source_by_id: dict[str, str] = {}
    for path in upload_files(upload_dir):
        payload = load_json(path)
        values = payload.get("questions") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError(f"questions array not found: {path}")
        for question in values:
            if not isinstance(question, dict):
                continue
            questions.append(question)
            source_by_id[str(question.get("questionId") or "")] = rel(path)
    return questions, source_by_id


def review_choice_map(
    records: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_id = {str(question.get("questionId") or ""): question for question in questions}
    by_origin: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for question in questions:
        key = (
            str(question.get("qualificationId") or ""),
            int(question.get("examYear") or 0),
            str(question.get("originalQuestionId") or ""),
        )
        by_origin.setdefault(key, []).append(question)

    result: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    payload_cache: dict[Path, list[Any]] = {}

    for review in records:
        patch_path = resolve_patch_path(str(review.get("explanationPatchFile") or ""))
        payload = payload_cache.get(patch_path)
        if payload is None:
            loaded = load_json(patch_path)
            if not isinstance(loaded, list):
                errors.append(f"patch root must be list: {rel(patch_path)}")
                continue
            payload = loaded
            payload_cache[patch_path] = payload
        try:
            _, entry = find_patch_entry(payload, review)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        choices = entry.get("choiceTextList")
        reviews = review.get("choiceReviews")
        labels = review_correct_choice_labels(review)
        if not isinstance(choices, list) or not isinstance(reviews, list) or not isinstance(labels, list):
            errors.append(f"invalid review arrays: {review.get('sourceQuestionKey')}")
            continue

        direct_ids: list[str] = []
        original_patch_id = str(entry.get("original_question_id") or "")
        if original_patch_id.startswith("firestore:"):
            direct_ids = [value.strip() for value in original_patch_id.removeprefix("firestore:").split(",")]

        for index, choice_text in enumerate(choices):
            matches: list[dict[str, Any]] = []
            if len(direct_ids) == len(choices) and direct_ids[index] in by_id:
                matches = [by_id[direct_ids[index]]]
            else:
                origin = str(
                    entry.get("public_question_id")
                    or entry.get("source_original_question_id")
                    or entry.get("original_question_id")
                    or ""
                )
                key = (
                    str(review.get("qualification") or ""),
                    int(review.get("examYear") or 0),
                    origin,
                )
                matches = [
                    question
                    for question in by_origin.get(key, [])
                    if normalize_text(question.get("originalQuestionChoiceText")) == normalize_text(choice_text)
                ]
            if len(matches) != 1:
                errors.append(
                    f"upload choice match count={len(matches)}: {review.get('sourceQuestionKey')} choice={index}"
                )
                continue
            question = matches[0]
            question_id = str(question.get("questionId") or "")
            if question_id in result:
                errors.append(f"duplicate review mapping: {question_id}")
                continue
            result[question_id] = {
                "review": review,
                "choiceReview": reviews[index],
                "expectedVerdict": normalize_label(labels[index]),
                "patchEntry": entry,
                "choiceIndex": index,
            }
    return result, errors


def check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    questions, source_by_id = load_upload_questions(args.upload_dir)
    law_questions = [question for question in questions if is_law_section_doc(question)]
    records = load_review_records(args.review_dir)
    mapped, mapping_errors = review_choice_map(records, questions)
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()

    def add(code: str, question: dict[str, Any] | None = None, **detail: Any) -> None:
        issue_counts[code] += 1
        if len(issues) >= args.max_samples:
            return
        payload: dict[str, Any] = {"code": code, **detail}
        if question is not None:
            question_id = str(question.get("questionId") or "")
            payload.update(
                {
                    "questionId": question_id,
                    "qualificationId": question.get("qualificationId"),
                    "examYear": question.get("examYear"),
                    "originalQuestionId": question.get("originalQuestionId"),
                    "choiceText": question.get("originalQuestionChoiceText"),
                    "uploadFile": source_by_id.get(question_id),
                }
            )
        issues.append(payload)

    for error in mapping_errors:
        add("review_mapping_error", message=error)

    law_ids = {str(question.get("questionId") or "") for question in law_questions}
    mapped_ids = set(mapped)
    for question in law_questions:
        question_id = str(question.get("questionId") or "")
        mapping = mapped.get(question_id)
        if mapping is None:
            add("missing_manual_review", question)
            continue
        actual_verdict = normalize_label(question.get("correctChoiceText"))
        if actual_verdict != mapping["expectedVerdict"]:
            add(
                "verdict_mismatch",
                question,
                expected=mapping["expectedVerdict"],
                actual=actual_verdict,
            )

        explanation = str(question.get("explanationText") or "")
        expected_prefixes = EXPLANATION_PREFIXES.get(mapping["expectedVerdict"], ())
        if args.require_materialized and not valid_explanation_prefix(
            mapping["expectedVerdict"], explanation
        ):
            add(
                "explanation_prefix_mismatch",
                question,
                expectedPrefixes=list(expected_prefixes),
            )
        if args.require_materialized and mapping["expectedVerdict"] == "間違い":
            if "選択肢の記載が誤り" in explanation:
                add(
                    "vague_wrong_explanation",
                    question,
                    explanationText=explanation,
                )
            explanation_body = strip_explanation_prefix("間違い", explanation)
            if len(normalize_text(explanation_body)) < 20:
                add("wrong_reason_too_short", question, explanationText=explanation)

        refs = question.get("lawReferences")
        non_law_basis = bool(mapping["review"].get("lawGroundedExplanationNotNeeded"))
        if args.require_materialized and not isinstance(refs, list):
            add("law_references_not_list", question)
        elif args.require_materialized and not refs and not non_law_basis:
            add("missing_law_reference", question)
        elif args.require_materialized and isinstance(refs, list):
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    add("invalid_law_reference", question, refIndex=ref_index)
                    continue
                if ref.get("verificationStatus") != "verified":
                    add(
                        "unverified_law_reference",
                        question,
                        refIndex=ref_index,
                        verificationStatus=ref.get("verificationStatus"),
                    )
                if ref.get("appLinkMode") == "egov_api":
                    if not str(ref.get("lawId") or "") or not str(ref.get("article") or ""):
                        add("incomplete_egov_reference", question, refIndex=ref_index)
                elif ref.get("appLinkMode") == "source_url":
                    if not valid_external_primary_reference(ref):
                        add("invalid_external_primary_reference", question, refIndex=ref_index)
                else:
                    add("missing_app_link_mode", question, refIndex=ref_index)

        questions_chip = question.get("suggestedQuestions")
        details = question.get("suggestedQuestionDetails")
        if args.require_materialized and (
            not isinstance(questions_chip, list)
            or not isinstance(details, list)
            or len(questions_chip) != len(details)
        ):
            add("suggested_question_shape_mismatch", question)

    for extra_id in sorted(mapped_ids - law_ids):
        add("review_mapped_outside_law_scope", questions_by_id(questions).get(extra_id), questionId=extra_id)

    report = {
        "schemaVersion": "gas-shunin-law-explanation-publication-check/v1",
        "uploadDir": rel(args.upload_dir),
        "reviewDir": rel(args.review_dir),
        "uploadQuestionCount": len(questions),
        "lawSectionQuestionCount": len(law_questions),
        "manualReviewRecordCount": len(records),
        "mappedChoiceCount": len(mapped),
        "issueCount": sum(issue_counts.values()),
        "issueCounts": dict(sorted(issue_counts.items())),
        "issues": issues,
        "ok": not issue_counts,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return (0 if report["ok"] else 1), report


def questions_by_id(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(question.get("questionId") or ""): question for question in questions}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check full manual-review and publication coverage for gas-shunin law explanations."
    )
    parser.add_argument("--upload-dir", type=Path, default=DEFAULT_UPLOAD_DIR)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-samples", type=int, default=300)
    parser.add_argument(
        "--require-materialized",
        action="store_true",
        help="Also require the final explanation/ref format in upload JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.upload_dir = args.upload_dir.expanduser().resolve()
    args.review_dir = args.review_dir.expanduser().resolve()
    args.report = args.report.expanduser().resolve() if args.report else None
    status, report = check(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
