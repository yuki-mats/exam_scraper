#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
QUESTIONS_ROOT = ROOT_DIR / "output" / "anma" / "questions_json"

PATCH_DEFS = {
    "10_questionType_fixed": "questionType_fixed",
    "15_correctChoiceText_fixed": "correctChoiceText_fixed",
    "21_explanationText_added": "explanationText_added",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def resolve_original_id(question: dict[str, Any]) -> str:
    value = question.get("original_question_id") or question.get("public_question_id")
    return str(value or "")


def flatten_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    flattened: list[str] = []
    for item in value:
        if isinstance(item, list):
            flattened.extend([str(text) for text in item if isinstance(text, str) and text.strip()])
        elif isinstance(item, str) and item.strip():
            flattened.append(item)
    return flattened


def compact_join(parts: list[str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for part in parts:
        text = str(part).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return "\n\n".join(output)


def clean_summary_piece(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^\[\d+\]\s*まとめ\s*", "", cleaned)
    return cleaned.strip()


def question_prompt_core(body: str) -> str:
    text = str(body or "").strip().rstrip("。")
    suffixes = [
        "として正しいのはどれか",
        "について正しいのはどれか",
        "で正しいのはどれか",
        "に含まれるのはどれか",
        "に該当しないのはどれか",
        "でないのはどれか",
        "の組合せで正しいのはどれか",
        "の組み合わせで正しいのはどれか",
        "のうち正しいのはどれか",
        "のはどれか",
        "はどれか",
    ]
    for suffix in suffixes:
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip("、 ,")
            break
    return text.strip()


def build_question_type_entries(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "questionBodyText": question.get("questionBodyText", ""),
            "choiceTextList": question.get("choiceTextList", []),
            "questionType": question.get("questionType", ""),
            "original_question_id": resolve_original_id(question),
            "question_url": question.get("question_url", ""),
        }
        for question in questions
    ]


def build_intent_entries(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "questionIntent_changed": False,
            "questionIntent_change_detail": "",
            "original_question_id": resolve_original_id(question),
            "questionIntent": question.get("questionIntent", ""),
            "questionIntent_change_reason": "",
        }
        for question in questions
    ]


def build_explanation_text(question: dict[str, Any]) -> list[str]:
    choices = question.get("choiceTextList") if isinstance(question.get("choiceTextList"), list) else []
    snippets = question.get("explanation_choice_snippets") if isinstance(question.get("explanation_choice_snippets"), list) else []
    common_parts = flatten_strings(question.get("explanation_common_prefix"))
    common_parts.extend(flatten_strings(question.get("explanation_common_summary")))
    answer_text = str(question.get("answer_result_text") or "").strip()

    explanations: list[str] = []
    for index in range(len(choices)):
        choice_snippets = flatten_strings(snippets[index]) if index < len(snippets) else []
        body = compact_join(choice_snippets or common_parts)
        if not body:
            body = "この選択肢は、問題文の条件と正答情報を照合して判断します。"
        if answer_text and answer_text not in body:
            body = f"{body}\n\n{answer_text}"
        explanations.append(body)
    return explanations


def build_suggested_questions(question: dict[str, Any]) -> list[str]:
    body = str(question.get("questionBodyText") or "").strip()
    core = question_prompt_core(body)
    third = "この論点の判断ポイントは何か。"
    if core:
        third = f"{core}の判断ポイントは何か。"
    return [
        "正解肢はなぜこれか。",
        "他の選択肢が誤りな理由は何か。",
        third,
    ]


def build_suggested_question_details(question: dict[str, Any]) -> list[dict[str, str]]:
    choices = question.get("choiceTextList") if isinstance(question.get("choiceTextList"), list) else []
    correct_numbers = question.get("answer_result_inferred_correct_choice_numbers")
    correct_index = 0
    if isinstance(correct_numbers, list) and correct_numbers:
        first = correct_numbers[0]
        if isinstance(first, int) and 1 <= first <= len(choices):
            correct_index = first - 1

    correct_choice = ""
    if choices and 0 <= correct_index < len(choices):
        correct_choice = str(choices[correct_index] or "").strip()

    summary_parts = flatten_strings(question.get("explanation_common_summary"))
    if not summary_parts:
        summary_parts = flatten_strings(question.get("explanation_common_prefix"))
    summary_text = compact_join([clean_summary_piece(text) for text in summary_parts])
    body = str(question.get("questionBodyText") or "").strip()
    core = question_prompt_core(body)

    return [
        {
            "question": "正解肢はなぜこれか。",
            "answer": (
                f"正解は「{correct_choice}」です。"
                f"{body}の条件に合います。"
                if correct_choice
                else f"{body}の条件に合う選択肢が正解です。"
            ),
        },
        {
            "question": "他の選択肢が誤りな理由は何か。",
            "answer": "不正解の選択肢は、定義・条件・数値・対象範囲のいずれかが設問と一致しません。",
        },
        {
            "question": build_suggested_questions(question)[2],
            "answer": (
                f"要点は、{summary_text}"
                if summary_text
                else (
                    f"{core}の判断では、問題文の条件と正答情報を照合することが大切です。"
                    if core
                    else "問題文の条件と正答情報を照合して判断します。"
                )
            ),
        },
    ]


def build_explanation_entries(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions:
        payload.append(
            {
                "explanationText": build_explanation_text(question),
                "suggestedQuestions": build_suggested_questions(question),
                "suggestedQuestionDetails": build_suggested_question_details(question),
                "original_question_id": resolve_original_id(question),
                "question_url": question.get("question_url", ""),
                "lawGroundedExplanationNotNeeded": False,
            }
        )
    return payload


def process_year(year: str, *, overwrite: bool) -> None:
    year_dir = QUESTIONS_ROOT / year
    source_dir = year_dir / "00_source"
    if not source_dir.exists():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    for source_path in sorted(source_dir.glob("question_*.json")):
        payload = load_json(source_path)
        questions = payload.get("question_bodies")
        if not isinstance(questions, list):
            raise ValueError(f"question_bodies missing: {source_path}")
        questions = [question for question in questions if isinstance(question, dict)]

        outputs = {
            "10_questionType_fixed": build_question_type_entries(questions),
            "15_correctChoiceText_fixed": build_intent_entries(questions),
            "21_explanationText_added": build_explanation_entries(questions),
        }
        for subdir, suffix in PATCH_DEFS.items():
            patch_path = year_dir / subdir / f"{source_path.stem}_{suffix}.json"
            if patch_path.exists() and not overwrite:
                continue
            save_json(patch_path, outputs[subdir])
            print(f"[WROTE] {patch_path.relative_to(ROOT_DIR)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate anma 10/15/21 patches from 00_source.")
    parser.add_argument("--years", nargs="+", required=True, help="target years, e.g. 2026")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing patch files if they already exist",
    )
    args = parser.parse_args()

    for year in args.years:
        process_year(str(year), overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
