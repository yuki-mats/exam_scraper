#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


SUBJECT_TO_CATEGORY = {
    "hourei": "法令",
    "kiso": "基礎理論",
    "seizo": "製造",
    "kyokyu": "供給",
    "shohi": "消費機器",
}

SOURCE_ID_PATTERN = re.compile(
    r"^gasushunin-(?P<grade>[^-]+)-(?P<subject>[^-]+)-(?P<year>\d{4})-(?P<question_no>\d+)$"
)
STATEMENT_PATTERN = re.compile(r"設問番号：\s*([^,、\s]+)")
STATEMENT_ORDER = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "イ": 1,
    "ロ": 2,
    "ハ": 3,
    "ㇵ": 3,
    "ニ": 4,
    "ホ": 5,
}
QUESTION_LEVEL_TYPES = {"flash_card", "group_choice"}
QUESTION_INTENTS = {"select_correct", "select_incorrect"}
CORRECT_VERDICTS = {"正解", "正しい"}
INCORRECT_VERDICTS = {"不正解", "間違い", "誤り"}


def parse_original_question_id(original_question_id: str | None) -> dict[str, Any]:
    match = SOURCE_ID_PATTERN.match(original_question_id or "")
    if match is None:
        return {}

    subject = match.group("subject")
    question_no = int(match.group("question_no"))
    if subject in {"gizyutsu", "gijutsu"}:
        if 1 <= question_no <= 9:
            subject = "seizo"
        elif 10 <= question_no <= 18:
            subject = "kyokyu"
        elif 19 <= question_no <= 27:
            subject = "shohi"

    return {
        "grade": match.group("grade"),
        "subject": subject,
        "year": int(match.group("year")),
        "questionNo": question_no,
    }


def statement_sort_key(question: dict[str, Any]) -> tuple[int, str]:
    match = STATEMENT_PATTERN.search(str(question.get("examSource") or ""))
    if match:
        marker = match.group(1).strip()
        return (STATEMENT_ORDER.get(marker, 999), str(question.get("questionId") or ""))

    suffix = re.search(r"-(\d+)$", str(question.get("questionId") or ""))
    if suffix:
        return (int(suffix.group(1)), str(question.get("questionId") or ""))
    return (999, str(question.get("questionId") or ""))


def unique_nonempty(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value in (None, "", []):
            continue
        if value not in result:
            result.append(value)
    return result


def choice_image_urls(question: dict[str, Any]) -> list[Any]:
    value = question.get("originalQuestionChoiceImageUrls")
    return value if isinstance(value, list) else []


def explanation_snippet(question: dict[str, Any]) -> list[str]:
    text = question.get("explanationText")
    if isinstance(text, str) and text.strip():
        return [text]
    return []


def collect_question_image_urls(group: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for question in group:
        urls = question.get("questionImageUrls")
        if not isinstance(urls, list):
            continue
        for url in urls:
            if isinstance(url, str) and url and url not in result:
                result.append(url)
    return result


def question_level_answer_fields(
    ordered: list[dict[str, Any]],
    question_type: Any,
) -> tuple[list[str], str | None, list[int]]:
    """Restore intrinsic verdicts from split-document answer semantics."""

    source_verdicts = [question.get("correctChoiceText", "") for question in ordered]
    if question_type not in QUESTION_LEVEL_TYPES:
        correct_numbers = [
            index
            for index, value in enumerate(source_verdicts, start=1)
            if value in CORRECT_VERDICTS
        ]
        return source_verdicts, None, correct_numbers

    raw_intents = unique_nonempty(
        [question.get("questionIntent") for question in ordered]
    )
    if (
        len(raw_intents) != 1
        or not isinstance(raw_intents[0], str)
        or raw_intents[0] not in QUESTION_INTENTS
    ):
        raise ValueError(
            "flash_card/group_choiceのintrinsic正誤を復元する"
            "questionIntentをsplit documentから一意に取得できません。"
        )
    question_intent = raw_intents[0]

    public_indexes: list[int] = []
    for index, question in enumerate(ordered):
        is_choice_only = question.get("isChoiceOnly")
        if not isinstance(is_choice_only, bool):
            raise ValueError(
                "flash_card/group_choiceのisChoiceOnlyがbooleanではありません。"
            )
        verdict = question.get("correctChoiceText")
        expected_verdicts = (
            INCORRECT_VERDICTS if is_choice_only else CORRECT_VERDICTS
        )
        if verdict not in expected_verdicts:
            raise ValueError(
                "Firestore split documentのisChoiceOnlyと"
                "correctChoiceTextが一致しません。"
            )
        if not is_choice_only:
            public_indexes.append(index)

    if len(public_indexes) != 1:
        raise ValueError(
            "flash_card/group_choiceの公開正答documentを一件に確定できません。"
        )

    public_index = public_indexes[0]
    if question_intent == "select_correct":
        intrinsic_verdicts = [
            "正しい" if index == public_index else "間違い"
            for index in range(len(ordered))
        ]
    else:
        intrinsic_verdicts = [
            "間違い" if index == public_index else "正しい"
            for index in range(len(ordered))
        ]
    return intrinsic_verdicts, question_intent, [public_index + 1]


def build_source_question(group: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(group, key=statement_sort_key)
    first = ordered[0]
    parts = parse_original_question_id(first.get("originalQuestionId"))
    question_types = unique_nonempty([question.get("questionType") for question in ordered])
    question_type = (
        question_types[0] if len(question_types) == 1 else first.get("questionType")
    )
    choice_texts = [question.get("originalQuestionChoiceText", "") for question in ordered]
    correct_choice_texts, question_intent, correct_numbers = (
        question_level_answer_fields(ordered, question_type)
    )

    record: dict[str, Any] = {
        "questionBodyText": first.get("originalQuestionBodyText") or first.get("questionBodyText") or "",
        "originalQuestionBodyText": first.get("originalQuestionBodyText") or "",
        "examYear": first.get("examYear"),
        "questionType": question_type,
        "choiceTextList": choice_texts,
        "choiceTextMarkedList": choice_texts.copy(),
        "correctChoiceText": correct_choice_texts,
        "explanationText": [question.get("explanationText", "") for question in ordered],
        "explanation_common_prefix": [],
        "explanation_common_summary": [],
        "explanation_choice_snippets": [explanation_snippet(question) for question in ordered],
        "explanation_choice_correctness": correct_choice_texts.copy(),
        "originalQuestionChoiceImageUrls": [choice_image_urls(question) for question in ordered],
        "questionImageStorageUrls": collect_question_image_urls(ordered),
        "originalQuestionId": first.get("originalQuestionId"),
        "original_question_id": first.get("originalQuestionId"),
        "source_question_id": first.get("originalQuestionId"),
        "qualificationId": first.get("qualificationId"),
        "questionSetIdList": unique_nonempty([question.get("questionSetId") for question in ordered]),
        "firestoreQuestionIds": [question.get("questionId") for question in ordered],
        "firestoreExamSources": [question.get("examSource") for question in ordered],
        "firestoreIsChoiceOnly": [question.get("isChoiceOnly") for question in ordered],
        "firestoreIsDeleted": [question.get("isDeleted") for question in ordered],
        "firestoreSourceQuestions": ordered,
    }
    if question_intent is not None:
        record["questionIntent"] = question_intent

    subject = parts.get("subject")
    if isinstance(subject, str):
        record["sourceSubject"] = subject
        record["category"] = SUBJECT_TO_CATEGORY.get(subject, subject)
    question_no = parts.get("questionNo")
    if isinstance(question_no, int):
        record["questionNo"] = question_no
        record["questionLabel"] = f"問{question_no}"

    if correct_numbers:
        record["answer_result_inferred_correct_choice_numbers"] = correct_numbers

    return record


def load_firestore_questions(snapshot_dir: Path) -> list[dict[str, Any]]:
    data = json.loads((snapshot_dir / "reconstructed" / "questions.json").read_text(encoding="utf-8"))
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError(f"questions 配列が見つかりません: {snapshot_dir}")
    return [question for question in questions if isinstance(question, dict)]


def materialize_year(
    *,
    year: int,
    questions: list[dict[str, Any]],
    output_dir: Path,
    chunk_size: int,
) -> dict[str, Any]:
    year_questions = [question for question in questions if int(question.get("examYear") or 0) == year]
    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for question in year_questions:
        groups[(question.get("originalQuestionId"), question.get("originalQuestionBodyText"))].append(question)

    source_questions = [
        build_source_question(group)
        for _, group in sorted(groups.items(), key=lambda item: (str(item[0][0]), str(item[0][1])))
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    for old_path in output_dir.glob(f"question_{year}_firestore_*.json"):
        old_path.unlink()

    written: list[str] = []
    for index, start in enumerate(range(0, len(source_questions), chunk_size), start=1):
        chunk = source_questions[start : start + chunk_size]
        path = output_dir / f"question_{year}_firestore_{index}.json"
        payload = {
            "list_group_id": str(year),
            "question_bodies": chunk,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))

    return {
        "year": year,
        "sourceQuestionCount": len(source_questions),
        "firestoreQuestionCount": len(year_questions),
        "writtenFileCount": len(written),
        "choiceTextListLengths": dict(sorted(Counter(len(question["choiceTextList"]) for question in source_questions).items())),
        "writtenFiles": written,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="甲種ガス主任 Firestore snapshot を他資格 00_source 互換の question_bodies 形式へ materialize する",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=True,
        help="Firestore snapshot directory",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT_DIR / "output" / "gas-shunin-kou" / "questions_json",
        help="questions_json root",
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=[2019, 2020, 2021, 2022, 2023],
        help="materialize target years",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=25,
        help="question_bodies per output file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot_dir = args.snapshot_dir.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    questions = load_firestore_questions(snapshot_dir)
    report = [
        materialize_year(
            year=year,
            questions=questions,
            output_dir=output_root / str(year) / "00_source",
            chunk_size=args.chunk_size,
        )
        for year in args.years
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
