#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.question_identity import review_question_id  # noqa: E402
from scripts.pipeline.build_gas_shunin_source_key_mapping import (  # noqa: E402
    GASSYUNIN_SUBJECTS,
    QUALIFICATION_GRADE,
    build_source_question_key,
    build_source_unique_key,
    parse_firestore_original_question_id,
    parse_question_no,
    parse_question_subject,
    technical_subject_for_question_no,
)


QUESTION_LABEL_RE = re.compile(r"問\s*(\d+)")
VALID_QUESTION_TYPES = {"true_false", "flash_card", "group_choice"}
VALID_CORRECT_CHOICE_TEXT = {"正しい", "間違い", "正解", "不正解", "誤り", ""}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_source_paths(qualification: str) -> list[Path]:
    root = ROOT_DIR / "output" / qualification / "questions_json"
    return sorted(path for path in root.glob("*/00_source/question_*.json") if "99_archived" not in path.parts)


def question_no_from_label(value: Any) -> int | None:
    match = QUESTION_LABEL_RE.search(str(value or ""))
    if not match:
        return None
    return int(match.group(1))


def question_no_for(question: dict[str, Any]) -> int | None:
    value = question.get("questionNo")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return parse_question_no(question) or question_no_from_label(question.get("questionLabel"))


def subject_for(question: dict[str, Any]) -> str | None:
    value = str(question.get("sourceSubject") or "").strip()
    if value:
        return value

    subject = parse_question_subject(question)
    if subject:
        return subject

    parsed = parse_firestore_original_question_id(
        str(question.get("original_question_id") or question.get("originalQuestionId") or "")
    )
    if parsed:
        return str(parsed.get("subject") or "")

    category = str(question.get("category") or "").strip()
    if category in GASSYUNIN_SUBJECTS:
        return GASSYUNIN_SUBJECTS[category]

    question_no = question_no_for(question)
    if question_no is not None:
        return technical_subject_for_question_no(question_no)
    return None


def source_key_parts(
    *,
    qualification: str,
    source_path: Path,
    question: dict[str, Any],
) -> dict[str, Any] | None:
    grade = QUALIFICATION_GRADE[qualification]
    year_value = question.get("examYear") or source_path.parent.parent.name
    question_no = question_no_for(question)
    subject = subject_for(question)
    if year_value is None or question_no is None or subject is None:
        return None
    return {
        "qualification": "gas-shunin",
        "grade": grade,
        "year": int(year_value),
        "subject": str(subject),
        "questionNo": int(question_no),
    }


def source_keys_for(
    *,
    qualification: str,
    source_path: Path,
    question: dict[str, Any],
    statement_count: int,
    apply_conflict_variant: bool = True,
) -> tuple[str | None, list[str], dict[str, Any] | None]:
    parts = source_key_parts(qualification=qualification, source_path=source_path, question=question)
    if parts is None:
        return None, [], None
    source_question_key = build_source_question_key(parts)
    source_unique_keys = []
    for statement_no in range(1, statement_count + 1):
        statement_parts = dict(parts)
        statement_parts["statementNo"] = statement_no
        source_unique_keys.append(build_source_unique_key(statement_parts))
    variant = source_key_conflict_variant(question) if apply_conflict_variant else None
    if variant:
        source_unique_keys = [f"{key}:legacy:{variant}" for key in source_unique_keys]
    return source_question_key, source_unique_keys, parts


def source_key_conflict_variant(question: dict[str, Any]) -> str | None:
    conflict = question.get("sourceKeyConflict")
    if not isinstance(conflict, dict):
        return None
    variant = str(conflict.get("variantKey") or question.get("sourceConflictVariantKey") or "").strip()
    return variant or None


def statement_count(question: dict[str, Any]) -> int:
    choice_texts = question.get("choiceTextList")
    if isinstance(choice_texts, list):
        return len(choice_texts)
    correct_choices = question.get("correctChoiceText")
    if isinstance(correct_choices, list):
        return len(correct_choices)
    value = question.get("sourceStatementCount")
    if isinstance(value, int):
        return value
    return 0


def is_firestore_source(question: dict[str, Any]) -> bool:
    ids = question.get("firestoreQuestionIds")
    return isinstance(ids, list) and any(str(item or "").strip() for item in ids)


def expected_statement_statuses(
    question: dict[str, Any],
    source_unique_keys: list[str],
    natural_source_unique_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    firestore_ids = question.get("firestoreQuestionIds") if isinstance(question.get("firestoreQuestionIds"), list) else []
    firestore = is_firestore_source(question)
    statuses: list[dict[str, Any]] = []
    for index, source_unique_key in enumerate(source_unique_keys, start=1):
        status: dict[str, Any] = {
            "statementNo": index,
            "sourceUniqueKey": source_unique_key,
            "firestoreRegistered": firestore,
            "siteOnly": not firestore,
        }
        if natural_source_unique_keys and index <= len(natural_source_unique_keys):
            natural_key = natural_source_unique_keys[index - 1]
            if natural_key != source_unique_key:
                status["sourceNaturalUniqueKey"] = natural_key
        if index <= len(firestore_ids) and str(firestore_ids[index - 1] or "").strip():
            status["firestoreQuestionId"] = str(firestore_ids[index - 1])
        statuses.append(status)
    return statuses


def normalize_question_metadata(
    *,
    qualification: str,
    source_path: Path,
    question: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = copy.deepcopy(question)
    changes: dict[str, Any] = {}
    count = statement_count(normalized)
    source_question_key, source_unique_keys, parts = source_keys_for(
        qualification=qualification,
        source_path=source_path,
        question=normalized,
        statement_count=count,
    )
    natural_source_question_key, natural_source_unique_keys, _ = source_keys_for(
        qualification=qualification,
        source_path=source_path,
        question=normalized,
        statement_count=count,
        apply_conflict_variant=False,
    )

    choice_texts = normalized.get("choiceTextList") if isinstance(normalized.get("choiceTextList"), list) else []

    def set_if_different(key: str, value: Any) -> None:
        if normalized.get(key) != value:
            normalized[key] = value
            changes[key] = value

    if isinstance(normalized.get("questionBodyText"), str):
        set_if_different("originalQuestionBodyText", normalized.get("originalQuestionBodyText") or normalized["questionBodyText"])
    if choice_texts:
        set_if_different("originalQuestionChoiceText", normalized.get("originalQuestionChoiceText") or choice_texts)

    if parts is not None:
        set_if_different("sourceKeyParts", parts)
        set_if_different("sourceGrade", parts["grade"])
        set_if_different("sourceSubject", parts["subject"])
        set_if_different("questionNo", parts["questionNo"])
        set_if_different("sourceQuestionKey", source_question_key)
        set_if_different("sourceUniqueKeys", source_unique_keys)
        set_if_different("sourceStatementCount", len(source_unique_keys))
        if source_unique_keys != natural_source_unique_keys:
            set_if_different("sourceNaturalQuestionKey", natural_source_question_key)
            set_if_different("sourceNaturalUniqueKeys", natural_source_unique_keys)
        set_if_different(
            "statementSourceStatuses",
            expected_statement_statuses(normalized, source_unique_keys, natural_source_unique_keys),
        )

    if is_firestore_source(normalized):
        set_if_different("sourceOrigin", "firestore_snapshot")
        set_if_different("sourceProvider", normalized.get("sourceProvider") or "firestore")
        set_if_different("sourceAcquisitionMethod", normalized.get("sourceAcquisitionMethod") or "firestore_snapshot")
        set_if_different("sourcePriority", normalized.get("sourcePriority") or 1)
        set_if_different("isSiteSourced", False)
    else:
        set_if_different("sourceOrigin", "gassyunin_site")
        set_if_different("sourceProvider", normalized.get("sourceProvider") or "gassyunin.com")
        set_if_different("sourceAcquisitionMethod", normalized.get("sourceAcquisitionMethod") or "site_html")
        set_if_different("sourcePriority", normalized.get("sourcePriority") or 2)
        set_if_different("isSiteSourced", True)
        if normalized.get("question_url"):
            set_if_different("sourceUrl", normalized.get("sourceUrl") or normalized.get("question_url"))

    return normalized, changes


def validate_question(
    *,
    qualification: str,
    source_path: Path,
    index: int,
    question: dict[str, Any],
    source_unique_key_counter: Counter[str],
    review_id_counter: Counter[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def issue(code: str, detail: str) -> None:
        issues.append(
            {
                "severity": "error",
                "code": code,
                "sourceFile": rel(source_path),
                "questionIndex": index,
                "reviewQuestionId": review_question_id(question),
                "detail": detail,
            }
        )

    body = question.get("questionBodyText")
    choices = question.get("choiceTextList")
    correct = question.get("correctChoiceText")
    explanations = question.get("explanationText")
    count = statement_count(question)
    source_question_key, source_unique_keys, parts = source_keys_for(
        qualification=qualification,
        source_path=source_path,
        question=question,
        statement_count=count,
    )
    natural_source_question_key, natural_source_unique_keys, _ = source_keys_for(
        qualification=qualification,
        source_path=source_path,
        question=question,
        statement_count=count,
        apply_conflict_variant=False,
    )

    if not isinstance(body, str) or not body.strip():
        issue("missing_questionBodyText", "questionBodyText is required")
    if not isinstance(choices, list) or not choices or not all(isinstance(item, str) for item in choices):
        issue("invalid_choiceTextList", "choiceTextList must be a non-empty string array")
    if question.get("questionType") not in VALID_QUESTION_TYPES:
        issue("invalid_questionType", f"questionType={question.get('questionType')!r}")
    if not isinstance(correct, list) or len(correct) != count:
        issue("invalid_correctChoiceText_length", f"correctChoiceText length must equal statement count {count}")
    elif any(item not in VALID_CORRECT_CHOICE_TEXT for item in correct):
        issue("invalid_correctChoiceText_value", "correctChoiceText contains unexpected labels")
    if explanations is not None and (not isinstance(explanations, list) or len(explanations) not in {0, count}):
        issue("invalid_explanationText_length", f"explanationText length must be 0 or {count}")
    if not isinstance(question.get("originalQuestionBodyText"), str) or not question.get("originalQuestionBodyText"):
        issue("missing_originalQuestionBodyText", "originalQuestionBodyText is required")
    if question.get("originalQuestionBodyText") != question.get("questionBodyText"):
        issue("originalQuestionBodyText_mismatch", "originalQuestionBodyText must match questionBodyText")
    if not isinstance(question.get("originalQuestionChoiceText"), list) or len(question.get("originalQuestionChoiceText") or []) != count:
        issue("missing_originalQuestionChoiceText", "originalQuestionChoiceText must be a list matching choiceTextList")
    elif question.get("originalQuestionChoiceText") != question.get("choiceTextList"):
        issue("originalQuestionChoiceText_mismatch", "originalQuestionChoiceText must match choiceTextList")
    if parts is None or not source_question_key:
        issue("missing_source_key_parts", "sourceQuestionKey cannot be built")
    if question.get("sourceQuestionKey") != source_question_key:
        issue("sourceQuestionKey_mismatch", "sourceQuestionKey is missing or not deterministic")
    if question.get("sourceUniqueKeys") != source_unique_keys:
        issue("sourceUniqueKeys_mismatch", "sourceUniqueKeys are missing or not deterministic")
    if source_unique_keys != natural_source_unique_keys:
        if question.get("sourceNaturalQuestionKey") != natural_source_question_key:
            issue("sourceNaturalQuestionKey_mismatch", "conflict rows must keep the unsuffixed natural question key")
        if question.get("sourceNaturalUniqueKeys") != natural_source_unique_keys:
            issue("sourceNaturalUniqueKeys_mismatch", "conflict rows must keep the unsuffixed natural unique keys")
    if question.get("sourceStatementCount") != count:
        issue("sourceStatementCount_mismatch", "sourceStatementCount must equal statement count")
    statuses = question.get("statementSourceStatuses")
    if not isinstance(statuses, list) or len(statuses) != count:
        issue("statementSourceStatuses_mismatch", "statementSourceStatuses must match statement count")
    if is_firestore_source(question):
        ids = question.get("firestoreQuestionIds")
        if not isinstance(ids, list) or len(ids) != count or any(not str(item or "").strip() for item in ids):
            issue("firestoreQuestionIds_mismatch", "Firestore-derived rows must have one Firestore doc id per statement")
        if question.get("sourceOrigin") != "firestore_snapshot":
            issue("sourceOrigin_mismatch", "Firestore-derived rows must use sourceOrigin=firestore_snapshot")
    else:
        if question.get("sourceOrigin") != "gassyunin_site":
            issue("sourceOrigin_mismatch", "site rows must use sourceOrigin=gassyunin_site")
        if not str(question.get("question_url") or "").strip():
            issue("missing_question_url", "site rows must keep question_url")
        if not str(question.get("public_question_id") or "").strip():
            issue("missing_public_question_id", "site rows must keep public_question_id")

    review_id_counter[f"{qualification}\t{review_question_id(question)}"] += 1
    for key in source_unique_keys:
        source_unique_key_counter[key] += 1
    return issues


def run_check(args: argparse.Namespace) -> dict[str, Any]:
    qualifications = args.qualifications
    all_issues: list[dict[str, Any]] = []
    source_unique_key_counter: Counter[str] = Counter()
    review_id_counter: Counter[str] = Counter()
    file_count = 0
    question_count = 0
    changed_files: list[str] = []
    change_counts: Counter[str] = Counter()
    text_hash_before: list[tuple[Any, Any, Any]] = []
    text_hash_after: list[tuple[Any, Any, Any]] = []

    for qualification in qualifications:
        for source_path in iter_source_paths(qualification):
            file_count += 1
            payload = load_json(source_path)
            bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
            if not isinstance(bodies, list):
                all_issues.append(
                    {
                        "severity": "error",
                        "code": "missing_question_bodies",
                        "sourceFile": rel(source_path),
                        "detail": "root.question_bodies must be a list",
                    }
                )
                continue
            new_bodies: list[dict[str, Any]] = []
            file_changed = False
            for index, question in enumerate(bodies, start=1):
                if not isinstance(question, dict):
                    all_issues.append(
                        {
                            "severity": "error",
                            "code": "invalid_question_body",
                            "sourceFile": rel(source_path),
                            "questionIndex": index,
                            "detail": "question_bodies item must be an object",
                        }
                    )
                    new_bodies.append(question)
                    continue
                question_count += 1
                text_hash_before.append((rel(source_path), index, question.get("questionBodyText"), tuple(question.get("choiceTextList") or [])))
                normalized, changes = normalize_question_metadata(
                    qualification=qualification,
                    source_path=source_path,
                    question=question,
                )
                if changes:
                    file_changed = True
                    change_counts.update(changes.keys())
                text_hash_after.append((rel(source_path), index, normalized.get("questionBodyText"), tuple(normalized.get("choiceTextList") or [])))
                all_issues.extend(
                    validate_question(
                        qualification=qualification,
                        source_path=source_path,
                        index=index,
                        question=normalized if args.fix else question,
                        source_unique_key_counter=source_unique_key_counter,
                        review_id_counter=review_id_counter,
                    )
                )
                new_bodies.append(normalized if args.fix else question)

            if args.fix and file_changed:
                payload["question_bodies"] = new_bodies
                save_json(source_path, payload)
                changed_files.append(rel(source_path))

    duplicate_source_unique_keys = {
        key: count for key, count in sorted(source_unique_key_counter.items()) if count > 1
    }
    duplicate_review_ids = {
        key: count for key, count in sorted(review_id_counter.items()) if count > 1
    }
    for key, count in duplicate_source_unique_keys.items():
        all_issues.append(
            {
                "severity": "error",
                "code": "duplicate_sourceUniqueKey",
                "sourceUniqueKey": key,
                "detail": f"sourceUniqueKey appears {count} times",
            }
        )
    for key, count in duplicate_review_ids.items():
        qualification, review_id = key.split("\t", 1)
        all_issues.append(
            {
                "severity": "error",
                "code": "duplicate_reviewQuestionId",
                "qualification": qualification,
                "reviewQuestionId": review_id,
                "detail": f"reviewQuestionId appears {count} times",
            }
        )

    if text_hash_before != text_hash_after:
        all_issues.append(
            {
                "severity": "error",
                "code": "source_text_changed_by_fix",
                "detail": "questionBodyText or choiceTextList changed during metadata normalization",
            }
        )

    report = {
        "schemaVersion": "gas-shunin-00-source-contract/v1",
        "generatedAt": utc_now(),
        "fixApplied": bool(args.fix),
        "qualifications": qualifications,
        "sourceFileCount": file_count,
        "questionCount": question_count,
        "changedFileCount": len(changed_files),
        "changedFiles": changed_files,
        "changeCounts": dict(sorted(change_counts.items())),
        "sourceUniqueKeyCount": len(source_unique_key_counter),
        "reviewQuestionIdCount": len(review_id_counter),
        "duplicateSourceUniqueKeyCount": len(duplicate_source_unique_keys),
        "duplicateReviewQuestionIdCount": len(duplicate_review_ids),
        "issueCount": len(all_issues),
        "issueCounts": dict(sorted(Counter(issue["code"] for issue in all_issues).items())),
        "issues": all_issues[: args.max_issues],
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ガス主任 00_source JSON が 01〜04 prompt 作業に必要な構造と source key を満たすか検証する",
    )
    parser.add_argument(
        "--qualifications",
        nargs="+",
        default=["gas-shunin-kou", "gas-shunin-otsu"],
        choices=sorted(QUALIFICATION_GRADE),
    )
    parser.add_argument("--fix", action="store_true", help="本文・選択肢は変えず、決定的メタデータだけを補う")
    parser.add_argument("--report", type=Path, help="検証レポートJSONの保存先")
    parser.add_argument("--max-issues", type=int, default=200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_check(args)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 1 if report["issueCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
