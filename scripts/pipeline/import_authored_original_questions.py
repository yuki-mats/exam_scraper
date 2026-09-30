#!/usr/bin/env python3
"""Register newly authored questions as immutable 00_source records.

The internal question_url is an identity locator, not an external source URL.
No existing source record is replaced by this importer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
QUESTION_KEY = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def build_records(payload: dict, category: dict) -> tuple[str, str, list[tuple[str, dict]]]:
    qualification = require_text(payload.get("qualificationId"), "qualificationId")
    group = require_text(payload.get("listGroupId"), "listGroupId")
    if not SLUG.fullmatch(qualification) or not SLUG.fullmatch(group):
        raise ValueError("qualificationId and listGroupId must be lowercase slugs")
    if category.get("metadata", {}).get("qualificationId") != qualification:
        raise ValueError("category qualificationId does not match")
    allowed_sets = {
        row.get("questionSetId")
        for row in category.get("questionSets", [])
        if isinstance(row, dict) and row.get("isDeleted") is False
    }
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a nonempty array")
    seen: set[str] = set()
    outputs: list[tuple[str, dict]] = []
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise ValueError(f"questions[{index}] must be an object")
        key = require_text(question.get("id"), f"questions[{index}].id")
        if not QUESTION_KEY.fullmatch(key) or key in seen:
            raise ValueError(f"invalid or duplicate question id: {key}")
        seen.add(key)
        body = require_text(question.get("questionBodyText"), f"{key}.questionBodyText")
        choices = question.get("choiceTextList")
        if not isinstance(choices, list) or not 2 <= len(choices) <= 6:
            raise ValueError(f"{key}.choiceTextList must contain 2 to 6 choices")
        choices = [require_text(value, f"{key}.choiceTextList") for value in choices]
        if len(set(choices)) != len(choices):
            raise ValueError(f"{key}.choiceTextList contains duplicates")
        correct = question.get("correctChoiceNumber")
        if type(correct) is not int or not 1 <= correct <= len(choices):
            raise ValueError(f"{key}.correctChoiceNumber is out of range")
        explanation = require_text(question.get("explanationText"), f"{key}.explanationText")
        question_set = require_text(question.get("questionSetId"), f"{key}.questionSetId")
        if question_set not in allowed_sets:
            raise ValueError(f"{key}.questionSetId is not in category.json")
        public_id = hashlib.sha256(f"{qualification}:authored:{key}".encode()).hexdigest()[:16]
        source_id = f"{qualification}:authored:{key}"
        locator = f"ankiplus://authored/{qualification}/{key}"
        record = {
            "sourceOrigin": "authored_original",
            "sourceQuestionKey": source_id,
            "source_question_id": source_id,
            "question_url": locator,
            "questionSourceSite": "ankiplus-original",
            "public_question_id": public_id,
            "original_question_id": public_id,
            "sourceUniqueKeys": [f"{source_id}:s{i:02d}" for i in range(1, len(choices) + 1)],
            "list_group_id": group,
            "questionBodyText": body,
            "choiceTextList": choices,
            "questionType": "true_false",
            "questionIntent": "select_correct",
            "correctChoiceText": ["正しい" if i == correct else "間違い" for i in range(1, len(choices) + 1)],
            "answer_result_text": f"正解は {correct} です。",
            "answer_result_inferred_correct_choice_numbers": [correct],
            "explanation_common_prefix": [explanation],
            "explanationText": [explanation],
            "examLabel": f"{qualification} 独自問題",
            "examSource": "独自問題",
            "questionLabel": f"独自問題 {key}",
            "questionSetId": question_set,
            "category": question_set,
            "questionImageStorageUrls": [],
            "originalQuestionChoiceImageUrls": [[] for _ in choices],
            "_independentImageRequired": False,
        }
        outputs.append((f"question_{key}.json", {"list_group_id": group, "question_bodies": [record]}))
    return qualification, group, outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Authored questions JSON")
    parser.add_argument("--category", required=True, type=Path)
    parser.add_argument("--write", action="store_true", help="Create 00_source records; default is validation only")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    category = json.loads(args.category.read_text(encoding="utf-8"))
    qualification, group, outputs = build_records(payload, category)
    destination = ROOT / "output" / qualification / "questions_json" / group / "00_source"
    existing = [destination / name for name, _ in outputs if (destination / name).exists()]
    if existing:
        raise FileExistsError(f"00_source already exists: {existing[0]}")
    print(f"validated {len(outputs)} authored questions: {qualification}/{group}")
    if args.write:
        destination.mkdir(parents=True, exist_ok=True)
        for name, data in outputs:
            (destination / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"created {len(outputs)} source records in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
