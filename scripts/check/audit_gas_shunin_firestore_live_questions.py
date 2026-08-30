#!/usr/bin/env python3
"""Build a read-only, one-row-per-question audit for live gas-shunin data."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from audit_beginner_calculation_explanations import beginner_flags
from audit_calculation_explanations import derivation_status, is_calculation_candidate


ROOT = Path(__file__).resolve().parents[2]
QUALIFICATIONS = {
    "kou": ("甲種", "gas-shunin-kou"),
    "otsu": ("乙種", "gas-shunin-otsu"),
}
STAGES = (
    "00_source",
    "10_questionType_fixed",
    "15_correctChoiceText_fixed",
    "18_law_context_prepared",
    "21_explanationText_added",
    "22_questionSetId_linked",
    "23_correctChoiceText_fixed",
)
VERDICT = {
    "正しい": "正しい",
    "正解": "正しい",
    "間違い": "間違い",
    "不正解": "間違い",
    "誤り": "間違い",
}
QUOTE_RE = re.compile(r"\[quote\](.*?)\[/quote\]\s*$", re.DOTALL)
SOURCE_KEY_RE = re.compile(r"gas-shunin:(?:kou|otsu):(\d{4}):([^:]+):q(\d+)")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).replace("−", "-").replace("‐", "-").replace("―", "-")


def normalize_choice(value: Any) -> str:
    text = normalize(value).replace("～", "~").replace("〜", "~")
    try:
        return f"number:{Decimal(text.replace(',', '')).normalize()}"
    except InvalidOperation:
        return re.sub(r"[。．、，,.・「」『』（）()【】]", "", text)


def canonical_verdict(value: Any) -> str | None:
    return VERDICT.get(str(value or "").strip())


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("question_bodies", "questions", "items", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def source_key(record: dict[str, Any], qualification: str, year: int) -> str:
    explicit = str(record.get("sourceQuestionKey") or "").strip()
    if explicit:
        return explicit
    identity = str(
        record.get("publicQuestionId")
        or record.get("public_question_id")
        or record.get("originalQuestionId")
        or record.get("original_question_id")
        or record.get("source_question_id")
        or ""
    ).strip()
    return f"{qualification}:{year}:{identity}" if identity else ""


def source_ids(record: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for candidate in (
        "publicQuestionId",
        "public_question_id",
        "originalQuestionId",
        "original_question_id",
        "source_original_question_id",
        "source_question_id",
        "reviewQuestionId",
    ):
        value = str(record.get(candidate) or "").strip()
        if not value:
            continue
        if value.startswith("firestore:"):
            result.update(part.strip() for part in value.removeprefix("firestore:").split(",") if part.strip())
        else:
            result.add(value)
    return result


def firestore_source_ids(record: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for candidate in ("original_question_id", "reviewQuestionId"):
        value = str(record.get(candidate) or "").strip()
        if value.startswith("firestore:"):
            result.update(part.strip() for part in value.removeprefix("firestore:").split(",") if part.strip())
    return result


def record_year(record: dict[str, Any], fallback: int) -> int:
    return int(record.get("examYear") or record.get("list_group_id") or fallback)


def update_catalog_record(target: dict[str, Any], source: dict[str, Any], path: Path, stage: str) -> None:
    field_names = {
        "body": ("questionBodyText", "originalQuestionBodyText"),
        "choices": ("choiceTextList",),
        "answers": ("correctChoiceText",),
        "explanations": ("explanationText",),
        "questionSetId": ("questionSetId",),
        "choiceQuestionSetIds": ("choiceQuestionSetIds", "questionSetIds"),
        "questionType": ("questionType",),
    }
    for dest, candidates in field_names.items():
        for candidate in candidates:
            if candidate in source and source[candidate] not in (None, "", []):
                target[dest] = source[candidate]
                target.setdefault("fieldEvidence", {})[dest] = rel(path)
                break
    target.setdefault("sourceIds", set()).update(source_ids(source))
    target.setdefault("evidence", []).append({"stage": stage, "path": rel(path)})


def load_manual_catalog(qualification: str) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    path = (
        ROOT
        / "output"
        / qualification
        / "review"
        / "01_04_manual_review"
        / f"{qualification}_01_04_manual_review.jsonl"
    )
    if not path.exists():
        return catalog
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        item = json.loads(line)
        if item.get("reviewDecision") != "ok":
            continue
        year = int(item.get("examYear") or 0)
        key = source_key(item, qualification, year)
        if not key:
            continue
        if key in catalog:
            current = catalog[key]
            incoming_ids = firestore_source_ids(item)
            current_ids = current.get("sourceIds", set())
            if (
                incoming_ids
                and incoming_ids.isdisjoint(current_ids)
                and normalize(item.get("questionBodyText")) != normalize(current.get("body"))
            ):
                key = f"{key}#{sorted(incoming_ids)[0]}"
        target = catalog.setdefault(
            key,
            {
                "qualification": qualification,
                "year": year,
                "sourceKey": key,
                "sourceIds": set(),
                "evidence": [],
                "fieldEvidence": {},
            },
        )
        update_catalog_record(target, item, path, "01_04_manual_review")
        target["manualReviewLine"] = line_number
    return catalog


def find_catalog_target(
    catalog: dict[str, dict[str, Any]],
    id_index: dict[str, str],
    item: dict[str, Any],
    qualification: str,
    year: int,
) -> tuple[str, dict[str, Any]] | None:
    key = source_key(item, qualification, year)
    item_ids = source_ids(item)
    direct_ids = firestore_source_ids(item)
    for identity in direct_ids:
        if identity and identity in id_index:
            existing_key = id_index[identity]
            return existing_key, catalog[existing_key]
    if key and key in catalog:
        current = catalog[key]
        current_ids = current.get("sourceIds", set())
        incoming_body = normalize(item.get("questionBodyText") or item.get("originalQuestionBodyText"))
        current_body = normalize(current.get("body"))
        distinct_direct_record = bool(direct_ids and direct_ids.isdisjoint(current_ids))
        distinct_body_record = bool(
            item_ids and current_ids and item_ids.isdisjoint(current_ids) and incoming_body and current_body != incoming_body
        )
        if distinct_direct_record or distinct_body_record:
            identity = sorted(direct_ids or item_ids)[0]
            alternate_key = f"{key}#{identity}"
            if alternate_key not in catalog:
                catalog[alternate_key] = {
                    "qualification": qualification,
                    "year": year,
                    "sourceKey": alternate_key,
                    "sourceIds": set(),
                    "evidence": [],
                    "fieldEvidence": {},
                }
            return alternate_key, catalog[alternate_key]
        return key, current
    if direct_ids and key:
        catalog[key] = {
            "qualification": qualification,
            "year": year,
            "sourceKey": key,
            "sourceIds": set(),
            "evidence": [],
            "fieldEvidence": {},
        }
        return key, catalog[key]
    for identity in item_ids:
        if identity and identity in id_index:
            existing_key = id_index[identity]
            return existing_key, catalog[existing_key]
    if not key:
        return None
    target = {
        "qualification": qualification,
        "year": year,
        "sourceKey": key,
        "sourceIds": set(),
        "evidence": [],
        "fieldEvidence": {},
    }
    catalog[key] = target
    return key, target


def load_source_catalog(qualification: str) -> dict[str, dict[str, Any]]:
    catalog = load_manual_catalog(qualification)
    root = ROOT / "output" / qualification / "questions_json"
    id_index: dict[str, str] = {}

    def refresh_ids(key: str, target: dict[str, Any]) -> None:
        for identity in target.get("sourceIds", set()):
            id_index.setdefault(identity, key)

    for key, target in catalog.items():
        refresh_ids(key, target)

    for stage in STAGES:
        paths = sorted(root.glob(f"20[0-9][0-9]/{stage}/*.json"))
        for path in paths:
            if "old" in path.parts:
                continue
            fallback_year = int(path.parts[-3])
            for record_index, item in enumerate(records(read_json(path)), 1):
                year = record_year(item, fallback_year)
                key = source_key(item, qualification, year)
                if stage == "00_source" and key in catalog:
                    current = catalog[key]
                    new_ids = source_ids(item)
                    current_ids = current.get("sourceIds", set())
                    bodies_differ = normalize(item.get("questionBodyText")) != normalize(current.get("body"))
                    if bodies_differ and new_ids.isdisjoint(current_ids):
                        identity = sorted(new_ids)[0] if new_ids else f"{path.stem}-{record_index}"
                        key = f"{key}#{identity}"
                        item = dict(item)
                        item["sourceQuestionKey"] = key
                found = find_catalog_target(catalog, id_index, item, qualification, year)
                if found is None:
                    continue
                key, target = found
                update_catalog_record(target, item, path, stage)
                refresh_ids(key, target)

    for path in sorted(root.glob("20[0-9][0-9]/24_questionIssueCorrections/*.json")):
        if "old" in path.parts:
            continue
        fallback_year = int(path.parts[-3])
        for item in records(read_json(path)):
            year = record_year(item, fallback_year)
            found = find_catalog_target(catalog, id_index, item, qualification, year)
            if found is None:
                continue
            key, target = found
            changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
            update_catalog_record(target, changes, path, "24_questionIssueCorrections")
            target.setdefault("evidence", []).append(
                {"stage": "24_questionIssueCorrections", "path": rel(path)}
            )
            refresh_ids(key, target)
    return catalog


def source_rows(catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in catalog.values():
        choices = list_value(item.get("choices"))
        answers = list_value(item.get("answers"))
        explanations = list_value(item.get("explanations"))
        choice_question_set_ids = list_value(item.get("choiceQuestionSetIds"))
        if not choices:
            continue
        for index, choice in enumerate(choices):
            result.append(
                {
                    "qualification": item["qualification"],
                    "year": item["year"],
                    "sourceKey": item["sourceKey"],
                    "sourceIds": sorted(item.get("sourceIds", set())),
                    "body": item.get("body") or "",
                    "choice": choice,
                    "answer": answers[index] if index < len(answers) else None,
                    "explanation": explanations[index] if index < len(explanations) else (
                        explanations[0] if len(explanations) == 1 else None
                    ),
                    "questionSetId": (
                        choice_question_set_ids[index]
                        if index < len(choice_question_set_ids)
                        and str(choice_question_set_ids[index] or "").strip()
                        else item.get("questionSetId")
                    ),
                    "questionType": item.get("questionType"),
                    "choiceIndex": index + 1,
                    "evidence": item.get("fieldEvidence", {}),
                    "manualReviewLine": item.get("manualReviewLine"),
                }
            )
    return result


def load_active_live(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    return [
        item
        for item in payload.get("questions", [])
        if item.get("isDeleted") is False and item.get("isChoiceOnly") is False
    ]


def build_source_indexes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_identity: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_body_choice: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_choice: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        body = normalize(row["body"])
        choice = normalize_choice(row["choice"])
        by_body_choice[(row["year"], body, choice)].append(row)
        by_choice[(row["year"], choice)].append(row)
        for identity in row["sourceIds"]:
            by_identity[(row["year"], identity, choice)].append(row)
    return {"identity": by_identity, "bodyChoice": by_body_choice, "choice": by_choice}


def unique(values: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_key = {(item["sourceKey"], item["choiceIndex"]): item for item in values}
    return next(iter(by_key.values())) if len(by_key) == 1 else None


def match_source(question: dict[str, Any], indexes: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    year = int(question.get("examYear") or 0)
    identities = [
        str(question.get("questionId") or ""),
        str(question.get("originalQuestionId") or ""),
    ]
    choice = normalize_choice(question.get("originalQuestionChoiceText"))
    body_values = [
        normalize(question.get("originalQuestionBodyText")),
        normalize(question.get("questionBodyText")),
    ]
    for identity in identities:
        if identity and choice:
            match = unique(indexes["identity"].get((year, identity, choice), []))
            if match:
                method = "firestore_id_and_choice" if identity == identities[0] else "original_id_and_choice"
                return match, method
    for body in body_values:
        if not body or not choice:
            continue
        match = unique(indexes["bodyChoice"].get((year, body, choice), []))
        if match:
            return match, "body_and_choice"
    if choice:
        match = unique(indexes["choice"].get((year, choice), []))
        if match:
            return match, "year_unique_choice"
    return None, "unmapped"


def quote_text(question_text: Any) -> str | None:
    match = QUOTE_RE.search(str(question_text or ""))
    return match.group(1) if match else None


def identity_issues(question: dict[str, Any], source: dict[str, Any] | None) -> list[str]:
    issues: list[str] = []
    qtype = str(question.get("questionType") or "")
    text = str(question.get("questionText") or "")
    original_choice = str(question.get("originalQuestionChoiceText") or "")
    quote = quote_text(text)
    if qtype == "true_false":
        if quote is None:
            if not original_choice or normalize_choice(original_choice) not in normalize_choice(text):
                issues.append("true_false_statement_missing_from_question_text")
        elif original_choice and normalize_choice(quote) != normalize_choice(original_choice):
            issues.append("quote_original_choice_mismatch")
    elif quote is not None and original_choice and normalize_choice(quote) != normalize_choice(original_choice):
        issues.append("quote_original_choice_mismatch")

    if source is None:
        return issues
    source_body = normalize(source.get("body"))
    display_body = QUOTE_RE.sub("", text)
    display_body_norm = normalize(display_body)
    if source_body and display_body_norm:
        similarity = difflib.SequenceMatcher(None, source_body, display_body_norm).ratio()
        if source_body not in display_body_norm and display_body_norm not in source_body:
            if similarity < 0.25:
                issues.append("display_body_source_identity_mismatch")
            elif similarity < 0.72:
                issues.append("display_body_source_identity_variation")
    source_choice = normalize_choice(source.get("choice"))
    if original_choice and source_choice != normalize_choice(original_choice):
        issues.append("original_choice_source_identity_mismatch")
    if quote is not None and source_choice != normalize_choice(quote):
        issues.append("display_choice_source_identity_mismatch")
    return sorted(set(issues))


def explanation_issues(question: dict[str, Any]) -> list[str]:
    explanation = str(question.get("explanationText") or "").strip()
    if not explanation:
        return ["explanation_missing"]
    qtype = str(question.get("questionType") or "")
    expected = canonical_verdict(question.get("correctChoiceText"))
    if qtype == "true_false":
        prefix = f"{expected}。" if expected else ""
        return [] if prefix and explanation.startswith(prefix) else ["explanation_prefix_mismatch"]
    if qtype in {"flash_card", "group_choice"}:
        return [] if explanation.startswith("正しい。") else ["explanation_prefix_mismatch"]
    return ["unsupported_question_type"]


def schema_issues(question: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field in ("questionText", "correctChoiceText", "explanationText", "questionSetId"):
        if not str(question.get(field) or "").strip():
            issues.append(f"missing_{field}")
    if not str(question.get("questionBodyText") or "").strip():
        issues.append("missing_questionBodyText")
    ref = str(question.get("questionSetRef") or "")
    if "undefined" in ref:
        issues.append("malformed_questionSetRef")
    return issues


def duplicate_map(questions: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups: dict[tuple[int, str], list[str]] = defaultdict(list)
    for question in questions:
        key = (int(question.get("examYear") or 0), normalize(question.get("questionText")))
        groups[key].append(str(question["questionId"]))
    result: dict[str, list[str]] = {}
    for ids in groups.values():
        if len(ids) > 1:
            for question_id in ids:
                result[question_id] = sorted(ids)
    return result


def content_status(issues: list[str], mapped: bool) -> str:
    fix_prefixes = (
        "answer_",
        "display_",
        "original_choice_",
        "quote_",
        "true_false_",
        "explanation_",
        "calculation_",
        "missing_questionText",
        "missing_correctChoiceText",
    )
    if any(issue.startswith(fix_prefixes) for issue in issues):
        return "needs_fix"
    return "pass" if mapped else "needs_review"


def audit_grade(grade_key: str, live_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grade, qualification = QUALIFICATIONS[grade_key]
    catalog = load_source_catalog(qualification)
    source = source_rows(catalog)
    indexes = build_source_indexes(source)
    questions = load_active_live(live_path)
    duplicates = duplicate_map(questions)
    rows: list[dict[str, Any]] = []
    for question in sorted(questions, key=lambda item: str(item.get("questionId") or "")):
        question_id = str(question.get("questionId") or "")
        matched, method = match_source(question, indexes)
        content_issues = identity_issues(question, matched) + explanation_issues(question)
        live_answer = canonical_verdict(question.get("correctChoiceText"))
        source_verdict = canonical_verdict(matched.get("answer")) if matched else None
        expected_answer = "正しい" if matched and question.get("questionType") == "group_choice" else source_verdict
        if matched and expected_answer and live_answer != expected_answer:
            content_issues.append("answer_source_mismatch")
        if matched and matched.get("questionSetId") and question.get("questionSetId") != matched.get("questionSetId"):
            content_issues.append("question_set_source_mismatch")

        calculation = is_calculation_candidate(question)
        calculation_reasons: list[str] = []
        beginner_missing: list[str] = []
        if calculation:
            explanation = str(question.get("explanationText") or "")
            _, calculation_reasons = derivation_status(explanation)
            beginner_missing = [name for name, ok in beginner_flags(explanation).items() if not ok]
            if "missing_explicit_formula_or_operator" in calculation_reasons:
                content_issues.append("calculation_explanation_incomplete")

        schema = schema_issues(question)
        review_issues: list[str] = []
        if "display_body_source_identity_variation" in content_issues:
            content_issues.remove("display_body_source_identity_variation")
            review_issues.append("display_body_source_identity_requires_review")
        if calculation and beginner_missing:
            review_issues.append("calculation_explanation_style_requires_review")
        if matched is None:
            review_issues.append("source_unmapped")
        if question_id in duplicates:
            review_issues.append("exact_live_duplicate")
        if "question_set_source_mismatch" in content_issues:
            review_issues.append("question_set_classification_requires_review")
            content_issues.remove("question_set_source_mismatch")

        cstatus = content_status(content_issues, matched is not None)
        schema_status = "needs_fix" if schema else "pass"
        if cstatus == "needs_fix" or schema_status == "needs_fix":
            overall = "needs_fix"
        elif review_issues:
            overall = "needs_review"
        else:
            overall = "pass"

        rows.append(
            {
                "questionId": question_id,
                "grade": grade,
                "qualification": qualification,
                "examYear": question.get("examYear"),
                "questionSetId": question.get("questionSetId"),
                "questionType": question.get("questionType"),
                "overallStatus": overall,
                "contentStatus": cstatus,
                "schemaStatus": schema_status,
                "sourceMatch": {
                    "status": "matched" if matched else "unmapped",
                    "method": method,
                    "sourceKey": matched.get("sourceKey") if matched else None,
                    "choiceIndex": matched.get("choiceIndex") if matched else None,
                    "evidence": matched.get("evidence") if matched else None,
                    "manualReviewLine": matched.get("manualReviewLine") if matched else None,
                },
                "answerCheck": {
                    "live": live_answer,
                    "source": expected_answer,
                    "status": "match" if expected_answer and live_answer == expected_answer else (
                        "mismatch" if expected_answer else "unverified"
                    ),
                },
                "contentIssues": sorted(set(content_issues)),
                "reviewIssues": sorted(set(review_issues)),
                "schemaIssues": schema,
                "duplicateQuestionIds": duplicates.get(question_id, []),
                "calculationCheck": {
                    "candidate": calculation,
                    "derivationReasons": calculation_reasons,
                    "beginnerMissingFlags": beginner_missing,
                },
                "live": {
                    "questionText": question.get("questionText"),
                    "originalQuestionBodyText": question.get("originalQuestionBodyText"),
                    "originalQuestionChoiceText": question.get("originalQuestionChoiceText"),
                    "correctChoiceText": question.get("correctChoiceText"),
                    "explanationText": question.get("explanationText"),
                },
            }
        )

    summary = {
        "grade": grade,
        "qualification": qualification,
        "liveQuestionCount": len(questions),
        "sourceQuestionCount": len(catalog),
        "sourceStatementCount": len(source),
        "overallStatusCounts": dict(Counter(row["overallStatus"] for row in rows)),
        "contentStatusCounts": dict(Counter(row["contentStatus"] for row in rows)),
        "schemaStatusCounts": dict(Counter(row["schemaStatus"] for row in rows)),
        "contentIssueCounts": dict(Counter(issue for row in rows for issue in row["contentIssues"])),
        "reviewIssueCounts": dict(Counter(issue for row in rows for issue in row["reviewIssues"])),
        "schemaIssueCounts": dict(Counter(issue for row in rows for issue in row["schemaIssues"])),
        "answerStatusCounts": dict(Counter(row["answerCheck"]["status"] for row in rows)),
        "exactDuplicateGroupCount": len({tuple(row["duplicateQuestionIds"]) for row in rows if row["duplicateQuestionIds"]}),
        "exactDuplicateExcessCount": sum(len(group) - 1 for group in {
            tuple(row["duplicateQuestionIds"]) for row in rows if row["duplicateQuestionIds"]
        }),
    }
    return rows, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kou-live", type=Path, required=True)
    parser.add_argument("--otsu-live", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    all_rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for key, path in (("kou", args.kou_live), ("otsu", args.otsu_live)):
        rows, summary = audit_grade(key, path)
        all_rows.extend(rows)
        summaries[key] = summary

    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    with args.ledger.open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    result = {
        "schemaVersion": "gas-shunin-firestore-live-question-audit/v1",
        "generatedAt": utc_now(),
        "filters": {"isDeleted": False, "isChoiceOnly": False},
        "questionCount": len(all_rows),
        "grades": summaries,
        "overallStatusCounts": dict(Counter(row["overallStatus"] for row in all_rows)),
        "contentStatusCounts": dict(Counter(row["contentStatus"] for row in all_rows)),
        "schemaStatusCounts": dict(Counter(row["schemaStatus"] for row in all_rows)),
        "contentIssueCounts": dict(Counter(issue for row in all_rows for issue in row["contentIssues"])),
        "reviewIssueCounts": dict(Counter(issue for row in all_rows for issue in row["reviewIssues"])),
        "schemaIssueCounts": dict(Counter(issue for row in all_rows for issue in row["schemaIssues"])),
        "answerStatusCounts": dict(Counter(row["answerCheck"]["status"] for row in all_rows)),
        "ledger": str(args.ledger),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
