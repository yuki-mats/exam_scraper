#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.fix.auto_assign_correct_choice_text import build_expected_correct_choice_text
from scripts.pipeline.build_tsukanshi_upload_artifacts import QUESTION_SETS, original_id, question_subject


QUALIFICATION = "tsukanshi"
REVIEW_SCHEMA_VERSION = "tsukanshi-01-04-manual-review/v1"
PROMPT_01 = "prompt/01_prompt_fix_questionType.md"
PROMPT_02 = "prompt/02_prompt_fix_questionIntent.md"
PROMPT_03 = "prompt/03_prompt_add_explanationText.md"
PROMPT_04 = "prompt/04_prompt_link_questionSetId.md"
DEFAULT_QUESTIONS_ROOT = Path("output/tsukanshi/questions_json")
DEFAULT_OUTPUT_DIR = Path("output/tsukanshi/review/01_04_manual_review")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def patch_path_for(source_path: Path, subdir: str, suffix: str) -> Path:
    return source_path.parent.parent / subdir / f"{source_path.stem}_{suffix}.json"


def build_review_row(
    *,
    source_path: Path,
    source_question: dict[str, Any],
    question_type_entry: dict[str, Any],
    intent_entry: dict[str, Any],
    explanation_entry: dict[str, Any],
    question_set_entry: dict[str, Any],
) -> dict[str, Any]:
    expected_correct_choice_text, correct_choice_reason = build_expected_correct_choice_text(source_question)
    expected_question_set_id = QUESTION_SETS[question_subject(source_question)]["questionSetId"]
    explanation_text = explanation_entry.get("explanationText") or []
    choice_text_list = source_question.get("choiceTextList") or []
    correct_choice_text = intent_entry.get("correctChoiceText") or []
    review_id = f"{source_path.parent.parent.name}:{original_id(source_question)}"

    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "reviewId": review_id,
        "workflow": "01_questionType -> 02_questionIntent_correctChoiceText -> 03_explanationText -> 04_questionSetId",
        "qualification": QUALIFICATION,
        "prompt01Path": PROMPT_01,
        "prompt02Path": PROMPT_02,
        "prompt03Path": PROMPT_03,
        "prompt04Path": PROMPT_04,
        "listGroupId": source_path.parent.parent.name,
        "examYear": str(source_question.get("examYear") or source_path.parent.parent.name),
        "examLabel": str(source_question.get("examLabel") or ""),
        "originalQuestionId": original_id(source_question),
        "questionUrl": str(source_question.get("question_url") or ""),
        "sourceFile": str(source_path),
        "questionTypePatchFile": str(patch_path_for(source_path, "10_questionType_fixed", "questionType_fixed")),
        "correctChoicePatchFile": str(patch_path_for(source_path, "15_correctChoiceText_fixed", "correctChoiceText_fixed")),
        "explanationPatchFile": str(patch_path_for(source_path, "21_explanationText_added", "explanationText_added")),
        "questionSetPatchFile": str(patch_path_for(source_path, "22_questionSetId_linked", "questionSetId_linked")),
        "questionBodyText": str(source_question.get("questionBodyText") or ""),
        "choiceTextList": choice_text_list,
        "answerResultText": str(source_question.get("answer_result_text") or ""),
        "explanationCommonPrefix": source_question.get("explanation_common_prefix") or [],
        "explanationCommonSummary": source_question.get("explanation_common_summary") or [],
        "explanationChoiceSnippets": source_question.get("explanation_choice_snippets") or [],
        "questionType": str(question_type_entry.get("questionType") or ""),
        "questionIntent": str(intent_entry.get("questionIntent") or ""),
        "correctChoiceText": correct_choice_text,
        "explanationText": explanation_text,
        "suggestedQuestions": explanation_entry.get("suggestedQuestions") or [],
        "suggestedQuestionDetails": explanation_entry.get("suggestedQuestionDetails") or [],
        "questionSetId": str(question_set_entry.get("questionSetId") or ""),
        "expectedCorrectChoiceText": expected_correct_choice_text or [],
        "expectedQuestionSetId": expected_question_set_id,
        "autoAudit": {
            "correctChoiceReason": correct_choice_reason or "",
            "correctChoiceMatchesExpected": (
                expected_correct_choice_text is not None and correct_choice_text == expected_correct_choice_text
            ),
            "questionSetMatchesExpected": str(question_set_entry.get("questionSetId") or "") == expected_question_set_id,
            "explanationLengthMatchesChoices": len(explanation_text) == len(choice_text_list),
        },
        "requiredManualChecks": [
            "01: questionType が問題文と選択肢構造に合っているか確認する",
            "02a: questionIntent が『正しいもの/誤っているもの』の設問要求に合っているか確認する",
            "02b: correctChoiceText が answer_result_text と各選択肢位置に合っているか確認する",
            "03: explanationText が各選択肢の正誤理由を誤学習なく説明しているか確認する",
            "04: questionSetId が examLabel の科目と一致しているか確認する",
        ],
        "review01QuestionType": "pending",
        "review02QuestionIntent": "pending",
        "review02CorrectChoiceText": "pending",
        "review03ExplanationText": "pending",
        "review04QuestionSetId": "pending",
        "reviewDecision": "pending",
        "reviewer": "",
        "reviewedAt": "",
        "reviewNotes": "",
        "fixInstructions": "",
    }


def build_review_rows(questions_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_path in sorted(questions_root.glob("*/00_source/question_*.json")):
        question_type_path = patch_path_for(source_path, "10_questionType_fixed", "questionType_fixed")
        intent_path = patch_path_for(source_path, "15_correctChoiceText_fixed", "correctChoiceText_fixed")
        explanation_path = patch_path_for(source_path, "21_explanationText_added", "explanationText_added")
        question_set_path = patch_path_for(source_path, "22_questionSetId_linked", "questionSetId_linked")

        source_payload = load_json(source_path)
        source_questions = source_payload.get("question_bodies")
        if not isinstance(source_questions, list):
            raise ValueError(f"question_bodies missing: {source_path}")

        question_type_entries = load_json(question_type_path)
        intent_entries = load_json(intent_path)
        explanation_entries = load_json(explanation_path)
        question_set_entries = load_json(question_set_path)
        lengths = {
            "source": len(source_questions),
            "questionType": len(question_type_entries),
            "intent": len(intent_entries),
            "explanation": len(explanation_entries),
            "questionSet": len(question_set_entries),
        }
        if len(set(lengths.values())) != 1:
            raise ValueError(f"length mismatch: {source_path} -> {lengths}")

        for source_question, question_type_entry, intent_entry, explanation_entry, question_set_entry in zip(
            source_questions,
            question_type_entries,
            intent_entries,
            explanation_entries,
            question_set_entries,
            strict=True,
        ):
            if not isinstance(source_question, dict):
                continue
            rows.append(
                build_review_row(
                    source_path=source_path,
                    source_question=source_question,
                    question_type_entry=question_type_entry,
                    intent_entry=intent_entry,
                    explanation_entry=explanation_entry,
                    question_set_entry=question_set_entry,
                )
            )
    return rows


def build_markdown_for_year(year: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# 通関士 01-04 manual review {year}",
        "",
        "- review01QuestionType / review02QuestionIntent / review02CorrectChoiceText / review03ExplanationText / review04QuestionSetId を 1 問ずつ埋める。",
        "- `reviewDecision` は `ok` / `needs_fix` / `hold` を使い、未着手は `pending` のままにする。",
        "- `needs_fix` の場合は `reviewNotes` と `fixInstructions` を残す。",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## {row['reviewId']}",
                "",
                f"- examLabel: {row['examLabel']}",
                f"- questionUrl: {row['questionUrl']}",
                f"- sourceFile: `{row['sourceFile']}`",
                f"- patches: `{row['questionTypePatchFile']}`, `{row['correctChoicePatchFile']}`, `{row['explanationPatchFile']}`, `{row['questionSetPatchFile']}`",
                f"- questionType: `{row['questionType']}`",
                f"- questionIntent: `{row['questionIntent']}`",
                f"- questionSetId: `{row['questionSetId']}` (expected: `{row['expectedQuestionSetId']}`)",
                f"- autoAudit: {json.dumps(row['autoAudit'], ensure_ascii=False)}",
                "",
                "### 問題文",
                "",
                row["questionBodyText"],
                "",
                "### 選択肢",
                "",
            ]
        )
        for idx, choice in enumerate(row["choiceTextList"], start=1):
            current_label = row["correctChoiceText"][idx - 1] if idx - 1 < len(row["correctChoiceText"]) else ""
            expected_label = (
                row["expectedCorrectChoiceText"][idx - 1]
                if idx - 1 < len(row["expectedCorrectChoiceText"])
                else ""
            )
            explanation = row["explanationText"][idx - 1] if idx - 1 < len(row["explanationText"]) else ""
            lines.extend(
                [
                    f"{idx}. {choice}",
                    f"   - currentCorrectChoiceText: {current_label}",
                    f"   - expectedCorrectChoiceText: {expected_label}",
                    f"   - explanationText: {explanation}",
                ]
            )
        lines.extend(
            [
                "",
                f"- answerResultText: {row['answerResultText']}",
                "- review01QuestionType: pending",
                "- review02QuestionIntent: pending",
                "- review02CorrectChoiceText: pending",
                "- review03ExplanationText: pending",
                "- review04QuestionSetId: pending",
                "- reviewDecision: pending",
                "- reviewNotes:",
                "- fixInstructions:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def export_review_artifacts(rows: list[dict[str, Any]], output_dir: Path) -> dict[str, Path]:
    jsonl_path = output_dir / "tsukanshi_01_04_manual_review.jsonl"
    write_jsonl(jsonl_path, rows)

    rows_by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_year[str(row["examYear"])].append(row)
    for year, year_rows in sorted(rows_by_year.items()):
        markdown = build_markdown_for_year(year, year_rows)
        write_text(output_dir / f"tsukanshi_01_04_manual_review_{year}.md", markdown)

    return {"jsonl": jsonl_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通関士 01-04 の manual review 台帳を固定名で上書き出力する",
    )
    parser.add_argument(
        "--questions-root",
        type=Path,
        default=DEFAULT_QUESTIONS_ROOT,
        help="defaults to output/tsukanshi/questions_json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="defaults to output/tsukanshi/review/01_04_manual_review",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = build_review_rows(args.questions_root.resolve())
    paths = export_review_artifacts(rows, args.output_dir.resolve())
    print(f"review_rows={len(rows)}")
    print(f"jsonl={paths['jsonl']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
