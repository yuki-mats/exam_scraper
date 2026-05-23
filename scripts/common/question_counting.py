from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


def find_key_values(obj: Any, key_name: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == key_name:
                found.append(value)
            found.extend(find_key_values(value, key_name))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_key_values(item, key_name))
    return found


def extract_question_records(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]

    if isinstance(obj, dict):
        for key in ("questions", "items"):
            value = obj.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if any(key in obj for key in ("questionSetId", "originalQuestionId", "original_question_id")):
            return [obj]

    return []


def get_record_question_key(record: Mapping[str, Any]) -> str | None:
    question_type = str(record.get("questionType") or "").strip()
    if question_type == "true_false":
        key_order = ("questionId", "question_id", "originalQuestionId", "original_question_id")
    else:
        key_order = ("originalQuestionId", "original_question_id", "questionId", "question_id")

    for key in key_order:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def count_question_sets_in_records(records: Iterable[Mapping[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    counted_pairs: set[tuple[str, str]] = set()

    for record in records:
        question_set_id = record.get("questionSetId")
        question_key = get_record_question_key(record)
        if question_set_id is None or str(question_set_id).strip() == "" or not question_key:
            continue

        pair = (str(question_set_id), question_key)
        if pair in counted_pairs:
            continue
        counted_pairs.add(pair)
        counter[str(question_set_id)] += 1

    return counter


def analyze_question_payload(obj: Any) -> tuple[int, Counter[str]]:
    records = extract_question_records(obj)
    unique_question_keys = {
        question_key
        for record in records
        for question_key in [get_record_question_key(record)]
        if question_key
    }
    total_questions = len(unique_question_keys)
    if total_questions == 0:
        original_question_ids = find_key_values(obj, "original_question_id")
        if original_question_ids:
            total_questions = len({str(pid).strip() for pid in original_question_ids if str(pid).strip()})

    return total_questions, count_question_sets_in_records(records)


def analyze_question_file(path: Path) -> tuple[int, Counter[str], Any]:
    with open(path, "r", encoding="utf-8") as handle:
        obj = json.load(handle)
    total_questions, question_set_counts = analyze_question_payload(obj)
    return total_questions, question_set_counts, obj
