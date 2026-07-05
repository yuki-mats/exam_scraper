#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id


TIMESTAMP_SUFFIX_PATTERN = re.compile(r"_(\d{8}_\d{4}|\d{8}_\d{6})$")

SOURCE_SUBDIR = "00_source"
MERGED_SUBDIR = "30_merged_2"
CURRENT_CORRECT_SUBDIR = "23_correctChoiceText_fixed"

QTYPE_SUBDIR = "10_questionType_fixed"
INTENT_SUBDIR = "15_correctChoiceText_fixed"
EXPLANATION_SUBDIR = "21_explanationText_added"
QUESTION_SET_SUBDIR = "22_questionSetId_linked"


class ContractPatchMaterializeError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def question_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        entries = payload.get("question_bodies") or payload.get("questions")
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict)]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def source_id(question: dict[str, Any]) -> str:
    value = review_question_id(question)
    if not value:
        raise ContractPatchMaterializeError("question id is missing")
    return str(value)


def strip_timestamp_suffix(stem: str) -> str:
    return TIMESTAMP_SUFFIX_PATTERN.sub("", stem)


def source_stem_from_merged_name(path: Path) -> str | None:
    stem = strip_timestamp_suffix(path.stem)
    suffix = "_merged"
    if not stem.endswith(suffix):
        return None
    return stem[: -len(suffix)]


def timestamp_sort_key(path: Path) -> tuple[int, str, str]:
    match = TIMESTAMP_SUFFIX_PATTERN.search(path.stem)
    if not match:
        return (0, "", path.name)
    return (1, match.group(1), path.name)


def latest_merged_map(merged_dir: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted(merged_dir.glob("*.json"), key=timestamp_sort_key):
        source_stem = source_stem_from_merged_name(path)
        if source_stem:
            result[source_stem] = path
    return result


def load_current_correct_overrides(list_group_dir: Path) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    correct_dir = list_group_dir / CURRENT_CORRECT_SUBDIR
    if not correct_dir.exists():
        return overrides
    for path in sorted(correct_dir.glob("*.json"), key=timestamp_sort_key):
        for entry in question_entries(load_json(path)):
            oid = entry.get("original_question_id") or entry.get("originalQuestionId")
            if oid and "correctChoiceText" in entry:
                overrides[str(oid)] = entry.get("correctChoiceText")
    return overrides


def normalize_source_snippets(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    normalized: list[list[str]] = []
    for entry in value:
        if isinstance(entry, list):
            normalized.append([str(item) for item in entry if isinstance(item, str) and item.strip()])
        elif isinstance(entry, str) and entry.strip():
            normalized.append([entry])
        else:
            normalized.append([])
    return normalized


def build_choice_change_detail(before: Any, after: Any) -> str:
    if before == after:
        return ""
    if isinstance(before, list) and isinstance(after, list):
        changes: list[str] = []
        count = max(len(before), len(after))
        for index in range(count):
            left = before[index] if index < len(before) else None
            right = after[index] if index < len(after) else None
            if left != right:
                changes.append(f"選択肢{index + 1}: {left} -> {right}")
        return " / ".join(changes)
    return f"{before} -> {after}"


def fallback_suggested_questions(is_law_related: bool) -> tuple[list[str], list[dict[str, str]]]:
    if is_law_related:
        questions = [
            "この問題はどの条文から確認しますか？",
            "現行法ではどう考えますか？",
        ]
        answers = [
            "法令根拠監査で保存した lawReferences と lawRevisionFacts を確認します。条文名、条番号、確認日、現行法ベースの正誤を分けて押さえます。",
            "現行法ベースの correctChoiceText を前提にし、出題当時の扱いと差分が保存されている場合は lawRevisionFacts に沿って説明します。",
        ]
    else:
        questions = [
            "正誤を判断するポイントはどこですか？",
            "他の選択肢とどう区別しますか？",
        ]
        answers = [
            "問題文の条件、数値、対象、例外の有無を確認し、各選択肢の解説で正誤の理由を対応させます。",
            "似た用語や基準を並べて、正しい選択肢と誤りの選択肢で何が違うかを確認します。",
        ]
    return questions, [
        {"question": question, "answer": answer}
        for question, answer in zip(questions, answers)
    ]


def normalized_law_references(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    for choice_index, refs in enumerate(value):
        if not isinstance(refs, list):
            normalized.append(refs)
            continue
        normalized_refs: list[Any] = []
        for ref in refs:
            if not isinstance(ref, dict):
                normalized_refs.append(ref)
                continue
            copied = dict(ref)
            if copied.get("scope") == "choice":
                copied["choiceIndex"] = choice_index
            normalized_refs.append(copied)
        normalized.append(normalized_refs)
    return normalized


def build_question_type_entry(
    source_question: dict[str, Any],
    merged_question: dict[str, Any],
) -> dict[str, Any]:
    return {
        "questionBodyText": source_question.get("questionBodyText", ""),
        "choiceTextList": source_question.get("choiceTextList", []),
        "questionType": merged_question.get("questionType") or source_question.get("questionType", ""),
        "original_question_id": source_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }


def build_intent_entry(
    source_question: dict[str, Any],
    merged_question: dict[str, Any],
    current_correct_overrides: dict[str, Any],
) -> dict[str, Any]:
    oid = source_id(source_question)
    source_intent = source_question.get("questionIntent")
    merged_intent = merged_question.get("questionIntent") or source_intent
    intent_changed = merged_intent != source_intent

    source_correct = source_question.get("correctChoiceText")
    current_correct = current_correct_overrides.get(oid, source_correct)
    correct_changed = current_correct != source_correct

    entry = {
        "questionIntent_changed": intent_changed,
        "questionIntent_change_detail": f"{source_intent} -> {merged_intent}" if intent_changed else "",
        "questionIntent_change_reason": "設問文の要求と整合させるため" if intent_changed else "",
        "questionIntent": merged_intent,
        "correctChoiceText_changed": correct_changed,
        "correctChoiceText_change_detail": build_choice_change_detail(source_correct, current_correct),
        "correctChoiceText_change_reason": "現行法監査結果と整合させるため" if correct_changed else "",
        "correctChoiceText": current_correct,
        "explanation_choice_snippets": normalize_source_snippets(
            source_question.get("explanation_choice_snippets")
        ),
        "original_question_id": oid,
        "question_url": source_question.get("question_url", ""),
    }
    for optional_key in (
        "answer_result_text",
        "answer_result_inferred_correct_choice_numbers",
        "manualQuestionIntentOverride",
    ):
        if optional_key in source_question:
            entry[optional_key] = source_question.get(optional_key)
    return entry


def build_explanation_entry(
    source_question: dict[str, Any],
    merged_question: dict[str, Any],
) -> dict[str, Any]:
    is_law_related = bool(merged_question.get("isLawRelated"))
    suggested_questions = merged_question.get("suggestedQuestions")
    suggested_details = merged_question.get("suggestedQuestionDetails")
    if not isinstance(suggested_questions, list) or not suggested_questions:
        suggested_questions, suggested_details = fallback_suggested_questions(is_law_related)

    entry: dict[str, Any] = {
        "explanationText": merged_question.get("explanationText", []),
        "suggestedQuestions": suggested_questions,
        "suggestedQuestionDetails": suggested_details,
        "original_question_id": source_id(source_question),
        "question_url": source_question.get("question_url", ""),
        "isLawRelated": is_law_related,
        "lawGroundedExplanationNotNeeded": not is_law_related,
    }
    for optional_key in (
        "lawRevisionFacts",
        "explanation_common_prefix",
        "explanation_common_prefix_inferred_correct_choice",
        "explanation_common_summary",
        "explanation_choice_snippets",
        "explanation_choice_correctness",
    ):
        if optional_key in merged_question and merged_question.get(optional_key) is not None:
            entry[optional_key] = merged_question.get(optional_key)
    if "lawReferences" in merged_question and merged_question.get("lawReferences") is not None:
        entry["lawReferences"] = normalized_law_references(merged_question.get("lawReferences"))
    return entry


def build_question_set_entry(
    source_question: dict[str, Any],
    merged_question: dict[str, Any],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "questionSetId": merged_question.get("questionSetId", ""),
        "original_question_id": source_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }
    for field in ("choiceQuestionSetIds", "questionSetIds"):
        if field in merged_question and merged_question.get(field) is not None:
            entry[field] = merged_question.get(field)
    return entry


def materialize_for_source_file(
    *,
    source_path: Path,
    merged_path: Path,
    list_group_dir: Path,
    timestamp: str,
    current_correct_overrides: dict[str, Any],
) -> list[Path]:
    source_payload = load_json(source_path)
    merged_payload = load_json(merged_path)
    source_questions = question_entries(source_payload)
    merged_questions = question_entries(merged_payload)
    merged_by_id = {source_id(question): question for question in merged_questions}
    source_stem = source_path.stem

    question_type_entries: list[dict[str, Any]] = []
    intent_entries: list[dict[str, Any]] = []
    explanation_entries: list[dict[str, Any]] = []
    question_set_entries: list[dict[str, Any]] = []

    for source_question in source_questions:
        oid = source_id(source_question)
        merged_question = merged_by_id.get(oid)
        if merged_question is None:
            raise ContractPatchMaterializeError(
                f"{source_path.name}: merged question not found for {oid}"
            )
        question_type_entries.append(build_question_type_entry(source_question, merged_question))
        intent_entries.append(
            build_intent_entry(source_question, merged_question, current_correct_overrides)
        )
        explanation_entries.append(build_explanation_entry(source_question, merged_question))
        question_set_entries.append(build_question_set_entry(source_question, merged_question))

    outputs = [
        (
            list_group_dir / QTYPE_SUBDIR / f"{source_stem}_questionType_fixed_{timestamp}.json",
            question_type_entries,
        ),
        (
            list_group_dir / INTENT_SUBDIR / f"{source_stem}_merged_correctChoiceText_fixed_{timestamp}.json",
            intent_entries,
        ),
        (
            list_group_dir / EXPLANATION_SUBDIR / f"{source_stem}_merged_explanationText_added_{timestamp}.json",
            explanation_entries,
        ),
        (
            list_group_dir / QUESTION_SET_SUBDIR / f"{source_stem}_questionSetId_linked_{timestamp}.json",
            question_set_entries,
        ),
    ]
    for output_path, entries in outputs:
        save_json(output_path, entries)
    return [output_path for output_path, _ in outputs]


def materialize_contract_patches(list_group_dir: Path, timestamp: str) -> list[Path]:
    source_dir = list_group_dir / SOURCE_SUBDIR
    merged_map = latest_merged_map(list_group_dir / MERGED_SUBDIR)
    current_correct_overrides = load_current_correct_overrides(list_group_dir)
    outputs: list[Path] = []
    for source_path in sorted(source_dir.glob("question_*.json")):
        merged_path = merged_map.get(source_path.stem)
        if merged_path is None:
            raise ContractPatchMaterializeError(
                f"{source_path.name}: latest merged file not found"
            )
        outputs.extend(
            materialize_for_source_file(
                source_path=source_path,
                merged_path=merged_path,
                list_group_dir=list_group_dir,
                timestamp=timestamp,
                current_correct_overrides=current_correct_overrides,
            )
        )
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="最新30_merged_2からquality-gate契約準拠の10/15/21/22 patchを再生成します。"
    )
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--timestamp", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outputs = materialize_contract_patches(args.list_group_dir, args.timestamp)
    print(f"[OK] materialized {len(outputs)} patch files")
    for output_path in outputs:
        print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
