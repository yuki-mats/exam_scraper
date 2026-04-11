from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from scripts.merge.merge_utils import (
    build_manual_output_path,
    maybe_split_for_manual_output,
)


EXPLANATION_FIELDS = [
    "explanationText",
    "explanation_common_prefix",
    "explanation_common_prefix_inferred_correct_choice",
    "explanation_common_summary",
    "explanation_choice_snippets",
    "explanation_choice_correctness",
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_patch_entries(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        for key in ("patched_questions", "question_bodies", "questions"):
            value = data.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
    return []


def normalize_question_ids(data: dict) -> None:
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        return
    for question in questions:
        if not isinstance(question, dict):
            continue
        if not question.get("original_question_id") and question.get("public_question_id"):
            question["original_question_id"] = question["public_question_id"]


def build_patch_map_from_paths(
    patch_paths: Iterable[Path],
    *,
    value_key: str | None = None,
    key_fields: Iterable[str] = ("original_question_id",),
) -> Dict[str, Any]:
    mapping: Dict[str, Any] = {}
    for patch_path in patch_paths:
        patch_data = load_json(patch_path)
        for entry in extract_patch_entries(patch_data):
            key_value = None
            for key_field in key_fields:
                value = entry.get(key_field)
                if value:
                    key_value = str(value)
                    break
            if key_value is None:
                continue
            mapping[key_value] = entry if value_key is None else entry.get(value_key)
    return mapping


def apply_question_type(
    data: dict,
    qtype_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = question.get("original_question_id")
        if question_id is None:
            continue
        # 追加: choiceTextListが全て空欄ならgroup_choiceにする
        choice_list = question.get("choiceTextList")
        if isinstance(choice_list, list) and all((c is None or str(c).strip() == "") for c in choice_list):
            question["questionType"] = "group_choice"
            updated += 1
            continue
        new_type = qtype_map.get(str(question_id))
        if new_type is None:
            continue
        question["questionType"] = new_type
        updated += 1
    return updated


def apply_explanation_fields(
    data: dict,
    explanation_map: Mapping[str, dict],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = question.get("original_question_id")
        if question_id is None:
            continue
        entry = explanation_map.get(str(question_id))
        if not isinstance(entry, dict):
            continue
        for field in EXPLANATION_FIELDS:
            if field in entry and entry[field] is not None:
                question[field] = entry[field]
                updated += 1
    return updated


def apply_question_set(
    data: dict,
    question_set_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = question.get("original_question_id")
        if question_id is None:
            continue
        new_value = question_set_map.get(str(question_id))
        if new_value is None:
            continue
        question["questionSetId"] = new_value
        updated += 1
    return updated


def apply_correct_choice(
    data: dict,
    correct_choice_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = question.get("original_question_id")
        if question_id is None:
            continue
        new_value = correct_choice_map.get(str(question_id))
        if new_value is None:
            continue
        question["correctChoiceText"] = new_value
        updated += 1
    return updated


def materialize_view_files(
    source_paths: Iterable[Path],
    output_dir: Path,
    *,
    qtype_map: Mapping[str, Any] | None = None,
    explanation_map: Mapping[str, dict] | None = None,
    question_set_map: Mapping[str, Any] | None = None,
    correct_choice_map: Mapping[str, Any] | None = None,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}

    for source_path in source_paths:
        data = load_json(source_path)
        if qtype_map:
            apply_question_type(data, qtype_map)
        if explanation_map:
            apply_explanation_fields(data, explanation_map)
        if question_set_map:
            apply_question_set(data, question_set_map)
        if correct_choice_map:
            apply_correct_choice(data, correct_choice_map)

        output_path = output_dir / source_path.name
        valid_data, manual_data = maybe_split_for_manual_output(data, output_path)
        save_json(valid_data, output_path)
        if manual_data:
            manual_path = build_manual_output_path(output_path)
            save_json(manual_data, manual_path)
        written[source_path.name] = output_path

    return written
