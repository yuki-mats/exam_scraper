#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import (
    SourceIdentityBinding,
    SourceRecordIdentity,
    load_source_record_inventory,
    resolve_identity_candidates,
    source_identity_aliases,
    workflow_identity_aliases,
)
from scripts.merge.merge_utils import strip_timestamp_suffix


TASK_CHOICES = (
    "question_type",
    "question_intent",
    "correct_choice",
    "law_context",
    "explanation",
    "question_set",
)


@dataclass(frozen=True)
class SourcePatchInput:
    question: dict[str, Any]
    identity: SourceRecordIdentity


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
        entries = data
    elif isinstance(data, dict):
        for key in ("patched_questions", "question_bodies", "questions", "entries"):
            value = data.get(key)
            if isinstance(value, list):
                entries = value
                break
        else:
            raise ValueError(
                "JSON 配列または entries/patched_questions/"
                "question_bodies/questions を含む object を指定してください"
            )
    else:
        raise ValueError(
            "JSON 配列または entries/patched_questions/"
            "question_bodies/questions を含む object を指定してください"
        )
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("raw patch entry はすべて object である必要があります")
    return entries


def get_source_questions(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("source JSON must be an object")
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    if any(not isinstance(question, dict) for question in questions):
        raise ValueError("source question はすべて object である必要があります")
    return questions


def resolve_original_id(question: dict[str, Any]) -> str:
    value = question.get("original_question_id") or question.get("public_question_id")
    if not value:
        raise ValueError("original_question_id/public_question_id がありません")
    return str(value)


def _identity_aliases(record: dict[str, Any]) -> set[str]:
    return source_identity_aliases(record) | workflow_identity_aliases(record)


def _source_stem(path: Path) -> str:
    return strip_timestamp_suffix(path.stem).removesuffix("_merged")


def bind_source_questions(
    source_path: Path,
    source_questions: list[dict[str, Any]],
) -> list[SourcePatchInput]:
    group_dir = source_path.parent.parent
    if group_dir.parent.name != "questions_json":
        raise ValueError(f"source path が questions_json 配下ではありません: {source_path}")
    inventory = load_source_record_inventory(
        group_dir / "00_source",
        qualification=group_dir.parent.parent.name,
        list_group_id=group_dir.name,
    )
    identity_by_binding = {
        item.identity.binding: item.identity for item in inventory
    }
    if source_path.parent.name == "00_source":
        source_items = [
            item for item in inventory if item.path.resolve() == source_path.resolve()
        ]
        if len(source_items) != len(source_questions):
            raise ValueError("source inventory と source question 件数が一致しません")
        return [
            SourcePatchInput(question=question, identity=item.identity)
            for question, item in zip(source_questions, source_items)
        ]

    index = resolve_identity_candidates(
        source_questions,
        sources=(item.identity for item in inventory),
        record_of=lambda question: question,
        aliases_of=_identity_aliases,
        source_stem_of=lambda _question: _source_stem(source_path),
        label="materialize source record",
    )
    if index.unmatched_count:
        raise ValueError(
            f"source inventory に対応しない source record が{index.unmatched_count}件あります"
        )
    errors = {
        message
        for messages in index.errors_by_binding.values()
        for message in messages
    }
    if errors:
        raise ValueError(" ".join(sorted(errors)))
    binding_by_question_id: dict[int, SourceIdentityBinding] = {}
    for binding, candidates in index.by_binding.items():
        if len(candidates) != 1:
            raise ValueError(
                "source record が同じ source identity に重複しています: "
                + " / ".join(binding.as_tuple())
            )
        binding_by_question_id[id(candidates[0])] = binding
    if len(binding_by_question_id) != len(source_questions):
        raise ValueError("source record を全件 exact identity に対応できません")
    return [
        SourcePatchInput(
            question=question,
            identity=identity_by_binding[binding_by_question_id[id(question)]],
        )
        for question in source_questions
    ]


def order_raw_entries(
    source_inputs: list[SourcePatchInput],
    raw_entries: list[dict[str, Any]],
) -> list[tuple[SourcePatchInput, dict[str, Any]]]:
    index = resolve_identity_candidates(
        raw_entries,
        sources=(source.identity for source in source_inputs),
        record_of=lambda entry: entry,
        aliases_of=_identity_aliases,
        source_stem_of=lambda _entry: "",
        label="minimal patch record",
    )
    if index.unmatched_count:
        raise ValueError(
            f"source に対応しない raw entry が{index.unmatched_count}件あります"
        )
    errors = {
        message
        for messages in index.errors_by_binding.values()
        for message in messages
    }
    if errors:
        raise ValueError(" ".join(sorted(errors)))
    ordered: list[tuple[SourcePatchInput, dict[str, Any]]] = []
    for source in source_inputs:
        candidates = index.by_binding.get(source.identity.binding, ())
        if len(candidates) != 1:
            raise ValueError(
                "raw entry を source record へ一意に対応できません: "
                + " / ".join(source.identity.binding.as_tuple())
                + f" (matches={len(candidates)})"
            )
        ordered.append((source, candidates[0]))
    if len(ordered) != len(raw_entries):
        raise ValueError("raw entry 件数と source record 件数が一致しません")
    return ordered


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


def materialize_law_context(
    source_question: dict[str, Any],
    raw_entry: dict[str, Any],
) -> dict[str, Any]:
    materialized = {
        "isLawRelated": raw_entry.get("isLawRelated"),
        "lawGroundedExplanationNotNeeded": raw_entry.get(
            "lawGroundedExplanationNotNeeded"
        ),
        "original_question_id": resolve_original_id(source_question),
        "question_url": source_question.get("question_url", ""),
    }
    if "lawReferences" in raw_entry:
        materialized["lawReferences"] = raw_entry.get("lawReferences")
    if "lawRevisionFacts" in raw_entry:
        materialized["lawRevisionFacts"] = raw_entry.get("lawRevisionFacts")
    if "lawContextForExplanation" in raw_entry:
        materialized["lawContextForExplanation"] = raw_entry.get(
            "lawContextForExplanation"
        )
    return materialized


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
    if "lawRevisionFacts" in raw_entry:
        materialized["lawRevisionFacts"] = raw_entry.get("lawRevisionFacts")
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
    source_inputs: list[SourcePatchInput],
    raw_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered_pairs = order_raw_entries(source_inputs, raw_entries)
    materializers = {
        "question_type": materialize_question_type,
        "question_intent": materialize_question_intent,
        "correct_choice": materialize_correct_choice,
        "law_context": materialize_law_context,
        "explanation": materialize_explanation,
        "question_set": materialize_question_set,
    }
    materializer = materializers[task]
    materialized: list[dict[str, Any]] = []
    for source, raw_entry in ordered_pairs:
        entry = materializer(source.question, raw_entry)
        entry["original_question_id"] = source.identity.binding.review_question_id
        entry.update(source.identity.binding.as_mapping())
        materialized.append(entry)
    return materialized


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成AIの最小JSONを正式パッチJSONに補完する"
    )
    parser.add_argument("--task", required=True, choices=TASK_CHOICES)
    parser.add_argument("--source", required=True, help="基準となる question_*.json")
    parser.add_argument("--raw", required=True, help="生成AIの最小JSON")
    parser.add_argument("--output", required=True, help="補完後の正式パッチJSON")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    source_questions = get_source_questions(load_json(source_path))
    source_inputs = bind_source_questions(source_path, source_questions)
    raw_entries = extract_entries(load_json(Path(args.raw)))
    materialized = materialize_entries(args.task, source_inputs, raw_entries)
    save_json(Path(args.output), materialized)
    print(f"[OK] materialized {len(materialized)} entries")
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
