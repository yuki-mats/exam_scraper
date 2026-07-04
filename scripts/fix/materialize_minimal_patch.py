#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from itertools import zip_longest
from pathlib import Path
from typing import Any


TASK_CHOICES = (
    "question_type",
    "question_intent",
    "correct_choice",
    "explanation",
    "question_set",
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def extract_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        for key in ("patched_questions", "question_bodies", "questions", "entries"):
            value = data.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
    raise ValueError("JSON 配列または entries/patched_questions/question_bodies/questions を含む object を指定してください")


def get_source_questions(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("source JSON must be an object")
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    return [question for question in questions if isinstance(question, dict)]


def resolve_original_id(question: dict[str, Any]) -> str:
    value = question.get("original_question_id") or question.get("public_question_id")
    if not value:
        raise ValueError("original_question_id/public_question_id がありません")
    return str(value)


def build_source_lookup(
    source_questions: list[dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    ordered_ids: list[str] = []
    lookup: dict[str, dict[str, Any]] = {}
    for question in source_questions:
        oid = resolve_original_id(question)
        ordered_ids.append(oid)
        lookup[oid] = question
    return ordered_ids, lookup


def order_raw_entries(
    source_questions: list[dict[str, Any]],
    raw_entries: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    ordered_ids, source_lookup = build_source_lookup(source_questions)
    raw_lookup: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []

    for entry in raw_entries:
        oid = entry.get("original_question_id")
        if not oid:
            raise ValueError("raw entry に original_question_id がありません")
        oid = str(oid)
        if oid in raw_lookup:
            duplicate_ids.append(oid)
            continue
        raw_lookup[oid] = entry

    if duplicate_ids:
        raise ValueError(f"raw entry に重複した original_question_id があります: {sorted(set(duplicate_ids))}")

    missing_ids = [oid for oid in ordered_ids if oid not in raw_lookup]
    extra_ids = [oid for oid in raw_lookup if oid not in source_lookup]
    if missing_ids:
        raise ValueError(f"raw entry に不足があります: {missing_ids}")
    if extra_ids:
        raise ValueError(f"source に存在しない original_question_id が raw entry にあります: {sorted(extra_ids)}")

    return [(source_lookup[oid], raw_lookup[oid]) for oid in ordered_ids]


def normalize_source_snippets(source_snippets: Any) -> list[list[str]]:
    if not isinstance(source_snippets, list):
        return []
    normalized: list[list[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list) and entry:
            first = entry[0]
            normalized.append([first] if isinstance(first, str) and first else [])
        elif isinstance(entry, str) and entry:
            normalized.append([entry])
        else:
            normalized.append([])
    return normalized


def build_correct_choice_change_detail(before: Any, after: Any) -> str:
    if not isinstance(before, list) or not isinstance(after, list):
        return ""
    changes: list[str] = []
    for idx, (left, right) in enumerate(zip_longest(before, after, fillvalue=None), start=1):
        if left == right:
            continue
        changes.append(f"選択肢{idx}を「{left}」→「{right}」に修正")
    return " / ".join(changes)


def materialize_question_type(
    source_question: dict[str, Any],
    raw_entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "questionBodyText": source_question.get("questionBodyText", ""),
        "choiceTextList": source_question.get("choiceTextList", []),
        "questionType": raw_entry.get("questionType", ""),
        "original_question_id": resolve_original_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }


def materialize_correct_choice(
    source_question: dict[str, Any],
    raw_entry: dict[str, Any],
) -> dict[str, Any]:
    current_value = source_question.get("correctChoiceText")
    next_value = raw_entry.get("correctChoiceText")
    changed = isinstance(current_value, list) and next_value != current_value
    detail = build_correct_choice_change_detail(current_value, next_value) if changed else ""
    reason = str(raw_entry.get("correctChoiceText_change_reason") or "").strip() if changed else ""
    if changed and not reason:
        reason = "ローカル一次情報との整合を取るため"
    return {
        "correctChoiceText_changed": changed,
        "correctChoiceText_change_detail": detail,
        "correctChoiceText_change_reason": reason,
        "correctChoiceText": next_value,
        "explanation_choice_snippets": normalize_source_snippets(
            source_question.get("explanation_choice_snippets")
        ),
        "original_question_id": resolve_original_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }


def materialize_question_intent(
    source_question: dict[str, Any],
    raw_entry: dict[str, Any],
) -> dict[str, Any]:
    current_value = source_question.get("questionIntent")
    next_value = raw_entry.get("questionIntent")
    changed = next_value != current_value

    detail = str(raw_entry.get("questionIntent_change_detail") or "").strip()
    if changed and not detail:
        detail = f"{current_value} -> {next_value}"

    reason = str(raw_entry.get("questionIntent_change_reason") or "").strip()
    if changed and not reason:
        reason = "設問文の要求と整合させるため"

    return {
        "questionIntent_changed": changed,
        "questionIntent_change_detail": detail if changed else "",
        "original_question_id": resolve_original_id(source_question),
        "questionIntent": next_value,
        "questionIntent_change_reason": reason if changed else "",
    }


def materialize_explanation(
    source_question: dict[str, Any],
    raw_entry: dict[str, Any],
) -> dict[str, Any]:
    materialized = {
        "explanationText": raw_entry.get("explanationText", []),
        "suggestedQuestions": raw_entry.get("suggestedQuestions", []),
        "suggestedQuestionDetails": raw_entry.get("suggestedQuestionDetails", []),
        "original_question_id": resolve_original_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }
    law_references = raw_entry.get("lawReferences")
    if law_references is not None:
        materialized["lawReferences"] = law_references
    if "isLawRelated" in raw_entry:
        materialized["isLawRelated"] = raw_entry.get("isLawRelated")
    if "lawGroundedExplanationNotNeeded" in raw_entry:
        materialized["lawGroundedExplanationNotNeeded"] = raw_entry.get(
            "lawGroundedExplanationNotNeeded"
        )
    return materialized


def materialize_question_set(
    source_question: dict[str, Any],
    raw_entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "questionSetId": raw_entry.get("questionSetId", ""),
        "original_question_id": resolve_original_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }


def materialize_entries(
    task: str,
    source_questions: list[dict[str, Any]],
    raw_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered_pairs = order_raw_entries(source_questions, raw_entries)
    materializers = {
        "question_type": materialize_question_type,
        "question_intent": materialize_question_intent,
        "correct_choice": materialize_correct_choice,
        "explanation": materialize_explanation,
        "question_set": materialize_question_set,
    }
    materializer = materializers[task]
    return [materializer(source_question, raw_entry) for source_question, raw_entry in ordered_pairs]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成AIの最小JSONを正式パッチJSONに補完する"
    )
    parser.add_argument("--task", required=True, choices=TASK_CHOICES)
    parser.add_argument("--source", required=True, help="基準となる question_*.json")
    parser.add_argument("--raw", required=True, help="生成AIの最小JSON")
    parser.add_argument("--output", required=True, help="補完後の正式パッチJSON")
    args = parser.parse_args()

    source_questions = get_source_questions(load_json(Path(args.source)))
    raw_entries = extract_entries(load_json(Path(args.raw)))
    materialized = materialize_entries(args.task, source_questions, raw_entries)
    save_json(Path(args.output), materialized)
    print(f"[OK] materialized {len(materialized)} entries")
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
