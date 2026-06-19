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


QUALIFICATION_GRADE = {
    "gas-shunin-kou": "kou",
    "gas-shunin-otsu": "otsu",
    "gas-shunin-hei": "hei",
}

FIRESTORE_GRADE_ALIASES = {
    "koushu": "kou",
    "kou": "kou",
    "otsushu": "otsu",
    "otsu": "otsu",
    "heishu": "hei",
    "hei": "hei",
}

GASSYUNIN_SUBJECTS = {
    "法令": "law",
    "基礎理論": "kiso",
    "製造": "seizo",
    "供給": "kyokyu",
    "消費機器": "shohi",
}

SOURCE_SUBJECTS = {
    "hourei": "law",
    "law": "law",
    "kiso": "kiso",
    "seizo": "seizo",
    "kyokyu": "kyokyu",
    "shohi": "shohi",
}

SOURCE_ID_PATTERN = re.compile(
    r"^gasushunin-(?P<grade>[^-]+)-(?P<subject>[^-]+)-(?P<year>\d{4})-(?P<question_no>\d+)$"
)
QUESTION_LABEL_PATTERN = re.compile(r"問\s*(?P<question_no>\d+)")
QUESTION_URL_PATTERN = re.compile(r"#(?P<subject>[a-z]+)-q(?P<question_no>\d+)$")


def utc_now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def latest_snapshot_dir(qualification: str, output_root: Path) -> Path:
    snapshot_root = output_root / qualification / "firestore_snapshot"
    candidates = sorted(path for path in snapshot_root.iterdir() if path.is_dir())
    if not candidates:
        raise FileNotFoundError(f"Firestore snapshot が見つかりません: {snapshot_root}")
    return candidates[-1]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fout:
        json.dump(payload, fout, ensure_ascii=False, indent=2, sort_keys=True)
        fout.write("\n")


def padded_question_no(question_no: int) -> str:
    return f"q{question_no:02d}"


def padded_statement_no(statement_no: int) -> str:
    return f"s{statement_no:02d}"


def build_source_question_key(parts: dict[str, Any]) -> str:
    return ":".join(
        [
            "gas-shunin",
            str(parts["grade"]),
            str(parts["year"]),
            str(parts["subject"]),
            padded_question_no(int(parts["questionNo"])),
        ]
    )


def build_source_unique_key(parts: dict[str, Any]) -> str:
    return f"{build_source_question_key(parts)}:{padded_statement_no(int(parts['statementNo']))}"


def technical_subject_for_question_no(question_no: int) -> str:
    if 1 <= question_no <= 9:
        return "seizo"
    if 10 <= question_no <= 18:
        return "kyokyu"
    if 19 <= question_no <= 27:
        return "shohi"
    return "gijutsu"


def parse_firestore_original_question_id(original_question_id: str) -> dict[str, Any] | None:
    match = SOURCE_ID_PATTERN.match(original_question_id or "")
    if not match:
        return None

    grade = FIRESTORE_GRADE_ALIASES.get(match.group("grade"))
    if grade is None:
        return None

    source_subject = match.group("subject")
    question_no = int(match.group("question_no"))
    subject = SOURCE_SUBJECTS.get(source_subject)
    if subject is None and source_subject in {"gizyutsu", "gijutsu"}:
        subject = technical_subject_for_question_no(question_no)
    if subject is None:
        return None

    return {
        "qualification": "gas-shunin",
        "grade": grade,
        "year": int(match.group("year")),
        "subject": subject,
        "questionNo": question_no,
        "sourceSubject": source_subject,
    }


def parse_question_no(question: dict[str, Any]) -> int | None:
    label = str(question.get("questionLabel") or "")
    match = QUESTION_LABEL_PATTERN.search(label)
    if match:
        return int(match.group("question_no"))

    url = str(question.get("question_url") or "")
    match = QUESTION_URL_PATTERN.search(url)
    if match:
        return int(match.group("question_no"))
    return None


def parse_question_subject(question: dict[str, Any]) -> str | None:
    category = str(question.get("category") or "")
    if category in GASSYUNIN_SUBJECTS:
        return GASSYUNIN_SUBJECTS[category]

    url = str(question.get("question_url") or "")
    match = QUESTION_URL_PATTERN.search(url)
    if match:
        return match.group("subject")
    return None


def sorted_firestore_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        questions,
        key=lambda item: (
            str(item.get("originalQuestionId") or ""),
            str(item.get("questionId") or ""),
        ),
    )


def load_firestore_questions(snapshot_dir: Path) -> list[dict[str, Any]]:
    path = snapshot_dir / "reconstructed" / "questions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError(f"questions 配列が見つかりません: {path}")
    return [question for question in questions if isinstance(question, dict)]


def load_gassyunin_questions(qualification: str, output_root: Path) -> list[dict[str, Any]]:
    questions_root = output_root / qualification / "questions_json"
    records: list[dict[str, Any]] = []
    for path in sorted(questions_root.glob("*/00_source/question_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        bodies = data.get("question_bodies") if isinstance(data, dict) else None
        if not isinstance(bodies, list):
            continue
        for index, question in enumerate(bodies):
            if not isinstance(question, dict):
                continue
            record = dict(question)
            record["_sourceFile"] = str(path)
            record["_sourceIndex"] = index
            record["_listGroupId"] = data.get("list_group_id")
            records.append(record)
    return records


def build_firestore_statement_records(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_original_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid: list[dict[str, Any]] = []
    for question in questions:
        original_id = str(question.get("originalQuestionId") or "")
        if not original_id:
            invalid.append(
                {
                    "reason": "missing_originalQuestionId",
                    "questionId": question.get("questionId"),
                    "firestoreExisting": question,
                }
            )
            continue
        by_original_id[original_id].append(question)

    records: list[dict[str, Any]] = []
    for original_id, group in sorted(by_original_id.items()):
        base_parts = parse_firestore_original_question_id(original_id)
        if base_parts is None:
            for question in sorted_firestore_questions(group):
                invalid.append(
                    {
                        "reason": "unparseable_originalQuestionId",
                        "questionId": question.get("questionId"),
                        "originalQuestionId": original_id,
                        "firestoreExisting": question,
                    }
                )
            continue

        for statement_no, question in enumerate(sorted_firestore_questions(group), start=1):
            parts = dict(base_parts)
            parts["statementNo"] = statement_no
            source_question_key = build_source_question_key(parts)
            source_unique_key = build_source_unique_key(parts)
            records.append(
                {
                    "sourceQuestionKey": source_question_key,
                    "sourceUniqueKey": source_unique_key,
                    "sourceKeyParts": parts,
                    "questionId": question.get("questionId"),
                    "originalQuestionId": original_id,
                    "statementOrdinalWithinOriginalQuestionId": statement_no,
                    "firestoreExisting": question,
                }
            )

    return records, invalid


def statement_count_for_gassyunin_question(question: dict[str, Any], empty_choice_slot_count: int) -> tuple[int, str]:
    choice_texts = question.get("choiceTextList")
    if isinstance(choice_texts, list) and choice_texts:
        return len(choice_texts), "choiceTextList"

    correct_choices = question.get("correctChoiceText")
    if isinstance(correct_choices, list) and correct_choices:
        return len(correct_choices), "correctChoiceText"

    answer_numbers = question.get("answer_result_inferred_correct_choice_numbers")
    if isinstance(answer_numbers, list) and answer_numbers and empty_choice_slot_count > 0:
        return max(empty_choice_slot_count, max(int(number) for number in answer_numbers)), "empty_choice_slots"

    return 0, "no_statement_source"


def build_gassyunin_question_records(
    *,
    qualification: str,
    questions: list[dict[str, Any]],
    empty_choice_slot_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    grade = QUALIFICATION_GRADE[qualification]
    question_records: list[dict[str, Any]] = []
    statement_records: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for question in questions:
        year_value = question.get("examYear") or question.get("_listGroupId")
        subject = parse_question_subject(question)
        question_no = parse_question_no(question)
        if year_value is None or subject is None or question_no is None:
            invalid.append(
                {
                    "reason": "missing_key_parts",
                    "sourceFile": question.get("_sourceFile"),
                    "sourceIndex": question.get("_sourceIndex"),
                    "gassyuninSource": question,
                }
            )
            continue

        parts = {
            "qualification": "gas-shunin",
            "grade": grade,
            "year": int(year_value),
            "subject": subject,
            "questionNo": int(question_no),
        }
        source_question_key = build_source_question_key(parts)
        statement_count, statement_source = statement_count_for_gassyunin_question(
            question,
            empty_choice_slot_count,
        )
        source_question = {
            "sourceQuestionKey": source_question_key,
            "sourceKeyParts": parts,
            "statementCount": statement_count,
            "statementSource": statement_source,
            "sourceFile": question.get("_sourceFile"),
            "sourceIndex": question.get("_sourceIndex"),
            "listGroupId": question.get("_listGroupId"),
            "questionUrl": question.get("question_url"),
            "publicQuestionId": question.get("public_question_id"),
            "sourceQuestionId": question.get("source_question_id"),
            "questionBodyText": question.get("questionBodyText"),
            "questionType": question.get("questionType"),
            "questionImageStorageUrls": question.get("questionImageStorageUrls") or [],
            "answerResultText": question.get("answer_result_text"),
            "answerResultInferredCorrectChoiceNumbers": question.get(
                "answer_result_inferred_correct_choice_numbers"
            )
            or [],
            "explanationSources": {
                "gassyuninCommonPrefix": question.get("explanation_common_prefix") or [],
                "gassyuninCommonSummary": question.get("explanation_common_summary") or [],
                "gassyuninChoiceSnippets": question.get("explanation_choice_snippets") or [],
            },
            "rawGassyuninSource": question,
        }
        question_records.append(source_question)

        choice_texts = question.get("choiceTextList") if isinstance(question.get("choiceTextList"), list) else []
        marked_texts = (
            question.get("choiceTextMarkedList")
            if isinstance(question.get("choiceTextMarkedList"), list)
            else []
        )
        correct_choice_texts = (
            question.get("correctChoiceText")
            if isinstance(question.get("correctChoiceText"), list)
            else []
        )
        snippets = (
            question.get("explanation_choice_snippets")
            if isinstance(question.get("explanation_choice_snippets"), list)
            else []
        )

        for statement_no in range(1, statement_count + 1):
            statement_parts = dict(parts)
            statement_parts["statementNo"] = statement_no
            statement_records.append(
                {
                    "sourceQuestionKey": source_question_key,
                    "sourceUniqueKey": build_source_unique_key(statement_parts),
                    "sourceKeyParts": statement_parts,
                    "statementNo": statement_no,
                    "hasSourceChoiceText": statement_no <= len(choice_texts),
                    "gassyuninSource": {
                        "questionBodyText": question.get("questionBodyText"),
                        "choiceText": choice_texts[statement_no - 1] if statement_no <= len(choice_texts) else None,
                        "choiceTextMarked": marked_texts[statement_no - 1] if statement_no <= len(marked_texts) else None,
                        "correctChoiceText": correct_choice_texts[statement_no - 1]
                        if statement_no <= len(correct_choice_texts)
                        else None,
                        "questionImageStorageUrls": question.get("questionImageStorageUrls") or [],
                        "questionUrl": question.get("question_url"),
                        "publicQuestionId": question.get("public_question_id"),
                        "sourceQuestionId": question.get("source_question_id"),
                        "explanationSources": {
                            "gassyuninCommonPrefix": question.get("explanation_common_prefix") or [],
                            "gassyuninCommonSummary": question.get("explanation_common_summary") or [],
                            "gassyuninChoiceSnippet": snippets[statement_no - 1]
                            if statement_no <= len(snippets)
                            else [],
                        },
                    },
                }
            )

    return question_records, statement_records, invalid


def duplicate_records(records: list[dict[str, Any]], key_name: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(key_name))].append(record)
    return {key: values for key, values in sorted(grouped.items()) if len(values) > 1}


def match_statement_records(
    firestore_records: list[dict[str, Any]],
    gassyunin_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    firestore_by_key = {record["sourceUniqueKey"]: record for record in firestore_records}
    gassyunin_by_key = {record["sourceUniqueKey"]: record for record in gassyunin_records}
    matched_keys = sorted(set(firestore_by_key) & set(gassyunin_by_key))

    matched = [
        {
            "sourceUniqueKey": key,
            "sourceQuestionKey": firestore_by_key[key]["sourceQuestionKey"],
            "sourceKeyParts": firestore_by_key[key]["sourceKeyParts"],
            "questionId": firestore_by_key[key]["questionId"],
            "originalQuestionId": firestore_by_key[key]["originalQuestionId"],
            "firestoreExisting": firestore_by_key[key]["firestoreExisting"],
            "gassyuninSource": gassyunin_by_key[key]["gassyuninSource"],
        }
        for key in matched_keys
    ]
    unmatched_firestore = [
        firestore_by_key[key] for key in sorted(set(firestore_by_key) - set(gassyunin_by_key))
    ]
    unmatched_gassyunin = [
        gassyunin_by_key[key] for key in sorted(set(gassyunin_by_key) - set(firestore_by_key))
    ]
    return matched, unmatched_firestore, unmatched_gassyunin


def build_validation_report(
    *,
    qualification: str,
    generated_at: str,
    firestore_snapshot_dir: Path,
    output_dir: Path,
    firestore_records: list[dict[str, Any]],
    gassyunin_question_records: list[dict[str, Any]],
    gassyunin_statement_records: list[dict[str, Any]],
    matched: list[dict[str, Any]],
    unmatched_firestore: list[dict[str, Any]],
    unmatched_gassyunin: list[dict[str, Any]],
    invalid_firestore: list[dict[str, Any]],
    invalid_gassyunin: list[dict[str, Any]],
    duplicates: dict[str, Any],
) -> dict[str, Any]:
    firestore_years = Counter(str(record["sourceKeyParts"].get("year")) for record in firestore_records)
    gassyunin_years = Counter(str(record["sourceKeyParts"].get("year")) for record in gassyunin_statement_records)
    matched_years = Counter(str(record["sourceKeyParts"].get("year")) for record in matched)
    firestore_subjects = Counter(str(record["sourceKeyParts"].get("subject")) for record in firestore_records)
    gassyunin_subjects = Counter(str(record["sourceKeyParts"].get("subject")) for record in gassyunin_statement_records)
    matched_subjects = Counter(str(record["sourceKeyParts"].get("subject")) for record in matched)

    return {
        "generatedAt": generated_at,
        "qualification": qualification,
        "firestoreSnapshotDir": str(firestore_snapshot_dir),
        "outputDir": str(output_dir),
        "keyPolicy": {
            "sourceQuestionKey": "gas-shunin:{grade}:{year}:{subject}:q{questionNo}",
            "sourceUniqueKey": "gas-shunin:{grade}:{year}:{subject}:q{questionNo}:s{statementNo}",
            "questionId": "Firestore document id is preserved",
            "originalQuestionId": "Existing Firestore field is preserved",
            "textPolicy": "No generated question, choice, or explanation text; source text is copied only",
        },
        "summary": {
            "firestoreStatementCount": len(firestore_records),
            "gassyuninQuestionCount": len(gassyunin_question_records),
            "gassyuninStatementCount": len(gassyunin_statement_records),
            "matchedStatementCount": len(matched),
            "unmatchedFirestoreStatementCount": len(unmatched_firestore),
            "unmatchedGassyuninStatementCount": len(unmatched_gassyunin),
            "invalidFirestoreRecordCount": len(invalid_firestore),
            "invalidGassyuninRecordCount": len(invalid_gassyunin),
            "duplicateFirestoreSourceUniqueKeyCount": len(duplicates["firestoreSourceUniqueKey"]),
            "duplicateGassyuninSourceUniqueKeyCount": len(duplicates["gassyuninSourceUniqueKey"]),
            "duplicateGassyuninSourceQuestionKeyCount": len(duplicates["gassyuninSourceQuestionKey"]),
        },
        "distributions": {
            "firestoreStatementsByYear": dict(sorted(firestore_years.items())),
            "gassyuninStatementsByYear": dict(sorted(gassyunin_years.items())),
            "matchedStatementsByYear": dict(sorted(matched_years.items())),
            "firestoreStatementsBySubject": dict(sorted(firestore_subjects.items())),
            "gassyuninStatementsBySubject": dict(sorted(gassyunin_subjects.items())),
            "matchedStatementsBySubject": dict(sorted(matched_subjects.items())),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ガス主任 Firestore snapshot と gassyunin 00_source を deterministic source key で突合する",
    )
    parser.add_argument(
        "qualification",
        choices=sorted(QUALIFICATION_GRADE),
        help="対象資格コード",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT_DIR / "output",
        help="output root。既定: repo/output",
    )
    parser.add_argument(
        "--firestore-snapshot-dir",
        type=Path,
        default=None,
        help="Firestore snapshot dir。未指定時は output/<qualification>/firestore_snapshot の最新を使う。",
    )
    parser.add_argument(
        "--mapping-root",
        type=Path,
        default=None,
        help="mapping output root。未指定時は output/<qualification>/merge_mapping",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="出力ディレクトリ名に使う timestamp。未指定時は UTC now。",
    )
    parser.add_argument(
        "--empty-choice-slot-count",
        type=int,
        default=5,
        help="choiceTextList が空で answer_result がある場合に作る空 statement slot 数。本文は生成しない。",
    )
    return parser.parse_args(argv)


def build_mapping(args: argparse.Namespace) -> Path:
    output_root = args.output_root.expanduser().resolve()
    firestore_snapshot_dir = (
        args.firestore_snapshot_dir.expanduser().resolve()
        if args.firestore_snapshot_dir
        else latest_snapshot_dir(args.qualification, output_root)
    )
    mapping_root = (
        args.mapping_root.expanduser().resolve()
        if args.mapping_root
        else output_root / args.qualification / "merge_mapping"
    )
    timestamp = args.timestamp or utc_now_label()
    output_dir = mapping_root / timestamp

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    firestore_questions = load_firestore_questions(firestore_snapshot_dir)
    firestore_records, invalid_firestore = build_firestore_statement_records(firestore_questions)
    gassyunin_questions = load_gassyunin_questions(args.qualification, output_root)
    gassyunin_question_records, gassyunin_statement_records, invalid_gassyunin = build_gassyunin_question_records(
        qualification=args.qualification,
        questions=gassyunin_questions,
        empty_choice_slot_count=args.empty_choice_slot_count,
    )

    duplicates = {
        "firestoreSourceUniqueKey": duplicate_records(firestore_records, "sourceUniqueKey"),
        "gassyuninSourceUniqueKey": duplicate_records(gassyunin_statement_records, "sourceUniqueKey"),
        "gassyuninSourceQuestionKey": duplicate_records(gassyunin_question_records, "sourceQuestionKey"),
    }
    matched, unmatched_firestore, unmatched_gassyunin = match_statement_records(
        firestore_records,
        gassyunin_statement_records,
    )
    report = build_validation_report(
        qualification=args.qualification,
        generated_at=generated_at,
        firestore_snapshot_dir=firestore_snapshot_dir,
        output_dir=output_dir,
        firestore_records=firestore_records,
        gassyunin_question_records=gassyunin_question_records,
        gassyunin_statement_records=gassyunin_statement_records,
        matched=matched,
        unmatched_firestore=unmatched_firestore,
        unmatched_gassyunin=unmatched_gassyunin,
        invalid_firestore=invalid_firestore,
        invalid_gassyunin=invalid_gassyunin,
        duplicates=duplicates,
    )

    write_json(output_dir / "firestore_statements.json", {"statements": firestore_records})
    write_json(output_dir / "gassyunin_questions.json", {"questions": gassyunin_question_records})
    write_json(output_dir / "gassyunin_statements.json", {"statements": gassyunin_statement_records})
    write_json(output_dir / "matched_statements.json", {"statements": matched})
    write_json(output_dir / "unmatched_firestore_statements.json", {"statements": unmatched_firestore})
    write_json(output_dir / "unmatched_gassyunin_statements.json", {"statements": unmatched_gassyunin})
    write_json(
        output_dir / "invalid_records.json",
        {
            "firestore": invalid_firestore,
            "gassyunin": invalid_gassyunin,
        },
    )
    write_json(output_dir / "duplicates.json", duplicates)
    write_json(output_dir / "validation_report.json", report)

    summary = report["summary"]
    print(f"mapping_dir: {output_dir}")
    print(f"qualification: {args.qualification}")
    print(f"firestore statements: {summary['firestoreStatementCount']}")
    print(f"gassyunin questions: {summary['gassyuninQuestionCount']}")
    print(f"gassyunin statements: {summary['gassyuninStatementCount']}")
    print(f"matched statements: {summary['matchedStatementCount']}")
    print(f"unmatched firestore statements: {summary['unmatchedFirestoreStatementCount']}")
    print(f"unmatched gassyunin statements: {summary['unmatchedGassyuninStatementCount']}")
    print(f"invalid records: firestore={summary['invalidFirestoreRecordCount']} gassyunin={summary['invalidGassyuninRecordCount']}")
    return output_dir


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    build_mapping(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
